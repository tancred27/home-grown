-- BM25 keyword search at scale: inverted indexes for bm25_hits() (db/009_bm25.sql).
--
-- 009 re-tokenised every candidate claim's body and summed every visible asset's claim
-- lengths on each query: 0.8-1.75 s per search at 20,000 synthetic assets
-- (scripts/bm25_load.py), growing linearly. Here:
--   * asset_terms / claim_terms hold (document, word, count), kept by triggers, so a search
--     reads only the postings of the query's words;
--   * asset_claim_len holds the summed length of each asset's current non-sensitive and
--     sensitive claims, kept by a trigger, so document lengths need no claim scan. It is a
--     table of its own so claim writes never touch (or re-trigger) asset rows.
-- Scores, visibility rules and statistics are exactly 009's (tests/test_bm25_index.py
-- compares the two functions on the same data).

create table if not exists asset_terms (
  asset_id  uuid not null references asset(id) on delete cascade,
  lexeme    text not null,
  tf        real not null,
  primary key (asset_id, lexeme)
);
create index if not exists asset_terms_lexeme_idx on asset_terms (lexeme) include (asset_id, tf);

create table if not exists claim_terms (
  claim_id  uuid not null references claim(id) on delete cascade,
  asset_id  uuid not null,
  lexeme    text not null,
  tf        real not null,
  primary key (claim_id, lexeme)
);
create index if not exists claim_terms_lexeme_idx on claim_terms (lexeme)
  include (claim_id, asset_id, tf);

create table if not exists asset_claim_len (
  asset_id  uuid primary key references asset(id) on delete cascade,
  open_len  real not null default 0,
  sens_len  real not null default 0
);

alter table asset_claim_len enable row level security;
alter table asset_claim_len force row level security;
alter table asset_terms enable row level security;
alter table asset_terms force row level security;
alter table claim_terms enable row level security;
alter table claim_terms force row level security;
revoke all on asset_terms, claim_terms, asset_claim_len from homegrown_app;
grant select on asset_terms, claim_terms, asset_claim_len to homegrown_search;

-- ---------------------------------------------------------------- maintenance

create or replace function asset_terms_sync() returns trigger as $$
begin
  delete from asset_terms where asset_id = new.id;
  insert into asset_terms (asset_id, lexeme, tf)
  select new.id, l.lexeme, coalesce(array_length(l.positions, 1), 1)
    from unnest(coalesce(new.tsv, ''::tsvector)) l;
  return new;
end $$ language plpgsql;

-- asset.tsv is set by the BEFORE trigger asset_tsv_trg (from name, summary, profile), so an
-- UPDATE OF tsv trigger would miss most changes: compare the values instead.
drop trigger if exists asset_terms_sync_trg on asset;
drop trigger if exists asset_terms_ins_trg on asset;
create trigger asset_terms_ins_trg after insert on asset
  for each row execute function asset_terms_sync();
create trigger asset_terms_sync_trg after update on asset
  for each row when (old.tsv is distinct from new.tsv) execute function asset_terms_sync();

create or replace function claim_lengths(p_asset uuid) returns void as $$
  -- An aggregate without GROUP BY always returns a row, so the asset check sits outside it:
  -- when an asset is deleted, its cascading claim deletes must not re-create a length row.
  insert into asset_claim_len (asset_id, open_len, sens_len)
  select p_asset, x.o, x.s
    from (select coalesce(sum(c.lex_len) filter (where not c.sensitive), 0) as o,
                 coalesce(sum(c.lex_len) filter (where c.sensitive), 0) as s
            from claim c
           where c.asset_id = p_asset and c.valid_to is null) x
   where exists (select 1 from asset a where a.id = p_asset)
  on conflict (asset_id) do update
     set open_len = excluded.open_len, sens_len = excluded.sens_len
$$ language sql;

create or replace function claim_terms_sync() returns trigger as $$
begin
  if tg_op = 'DELETE' then
    perform claim_lengths(old.asset_id);
    return old;
  end if;
  if tg_op = 'INSERT' or new.body is distinct from old.body then
    delete from claim_terms where claim_id = new.id;
    insert into claim_terms (claim_id, asset_id, lexeme, tf)
    select new.id, new.asset_id, l.lexeme, coalesce(array_length(l.positions, 1), 1)
      from unnest(to_tsvector('english', new.body)) l;
  end if;
  perform claim_lengths(new.asset_id);
  return new;
end $$ language plpgsql;

drop trigger if exists claim_terms_sync_trg on claim;
create trigger claim_terms_sync_trg after insert or update or delete on claim
  for each row execute function claim_terms_sync();

-- ---------------------------------------------------------------- backfill

insert into asset_terms (asset_id, lexeme, tf)
select a.id, l.lexeme, coalesce(array_length(l.positions, 1), 1)
  from asset a cross join lateral unnest(coalesce(a.tsv, ''::tsvector)) l
on conflict do nothing;
insert into claim_terms (claim_id, asset_id, lexeme, tf)
select c.id, c.asset_id, l.lexeme, coalesce(array_length(l.positions, 1), 1)
  from claim c cross join lateral unnest(to_tsvector('english', c.body)) l
on conflict do nothing;
insert into asset_claim_len (asset_id, open_len, sens_len)
select asset_id,
       coalesce(sum(lex_len) filter (where not sensitive), 0),
       coalesce(sum(lex_len) filter (where sensitive), 0)
  from claim where valid_to is null group by asset_id
on conflict (asset_id) do update set open_len = excluded.open_len, sens_len = excluded.sens_len;

-- ---------------------------------------------------------------- search

create or replace function bm25_hits(p_query tsquery, p_lex text[], p_limit int,
                                     p_k1 real default 1.2, p_b real default 0.75)
returns table (asset_id uuid, score real)
language sql stable security definer
set search_path = public, pg_temp
as $$
  with me as (select nullif(current_setting('app.user_id', true), '')::uuid as u),
  groups as (select m.group_id from membership m, me where m.user_id = me.u),
  readable as (select s.source_ref from source_acl s, me where s.user_id = me.u),
  -- every visible asset with its document length: own text plus readable claims at half weight
  doclen as materialized (
    select a.id,
           a.source_ref in (select source_ref from readable) as can_read,
           a.lex_len + 0.5 * (coalesce(l.open_len, 0)
             + case when a.source_ref in (select source_ref from readable)
                    then coalesce(l.sens_len, 0) else 0 end) as len
      from asset a
      left join asset_claim_len l on l.asset_id = a.id
     where a.discovery_scope in (select group_id from groups)),
  stats as materialized (select count(*)::real as n, greatest(avg(len), 1e-9)::real as avgdl from doclen),
  -- postings of the query's words: the asset's own text, and its readable current claims
  post as materialized (
    select t.asset_id as id, t.lexeme as t, t.tf as f
      from asset_terms t
      join doclen d on d.id = t.asset_id
     where t.lexeme = any(p_lex)
    union all
    select t.asset_id, t.lexeme, 0.5 * t.tf
      from claim_terms t
      join claim c on c.id = t.claim_id
      join doclen d on d.id = t.asset_id
     where t.lexeme = any(p_lex)
       and c.valid_to is null
       and (not c.sensitive or d.can_read)),
  tf as materialized (select id, t, sum(f) as f from post group by id, t),
  df as materialized (select t, count(distinct id)::real as df from post group by t)
  select tf.id,
         sum(ln(1 + (s.n - df.df + 0.5) / (df.df + 0.5))
             * tf.f * (p_k1 + 1)
             / (tf.f + p_k1 * (1 - p_b + p_b * dl.len / s.avgdl)))::real as score
    from tf
    join df on df.t = tf.t
    join doclen dl on dl.id = tf.id
   cross join stats s
   group by tf.id
   order by 2 desc, tf.id
   limit p_limit
$$;

alter function bm25_hits(tsquery, text[], int, real, real) owner to homegrown_search;
