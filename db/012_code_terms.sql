-- Code words at scale: an inverted index for code_words_hits() (db/011_code_words.sql).
--
-- 011 counted term frequencies by unpacking the whole code-word vector of every unit that
-- matched any query word. Work grew with corpus size times code length: about 0.45 s at
-- 5,000 synthetic units (scripts/code_words_load.py). Here each unit's words are also kept
-- as rows (unit, word, count), maintained by a trigger, so a search reads only the postings
-- of the query's words. Results, visibility rules and statistics are unchanged.

create table if not exists code_terms (
  asset_id  uuid not null references asset(id) on delete cascade,
  lexeme    text not null,
  tf        real not null,
  primary key (asset_id, lexeme)
);
create index if not exists code_terms_lexeme_idx on code_terms (lexeme) include (asset_id, tf);

alter table code_terms enable row level security;
alter table code_terms force row level security;
revoke all on code_terms from homegrown_app;
grant select on code_terms to homegrown_search;

create or replace function code_terms_sync() returns trigger as $$
begin
  delete from code_terms where asset_id = new.asset_id;
  insert into code_terms (asset_id, lexeme, tf)
  select new.asset_id, l.lexeme, coalesce(array_length(l.positions, 1), 1)
    from unnest(new.tsv) l;
  return new;
end $$ language plpgsql;

drop trigger if exists code_terms_sync_trg on code_words;
create trigger code_terms_sync_trg after insert or update of tsv on code_words
  for each row execute function code_terms_sync();

-- Backfill what 011 already stored.
insert into code_terms (asset_id, lexeme, tf)
select w.asset_id, l.lexeme, coalesce(array_length(l.positions, 1), 1)
  from code_words w cross join lateral unnest(w.tsv) l
on conflict do nothing;

-- Same signature as 011 (p_query is no longer needed to find candidates, kept for callers).
create or replace function code_words_hits(p_query tsquery, p_lex text[], p_limit int,
                                           p_k1 real default 1.2, p_b real default 0.75)
returns table (asset_id uuid, score real)
language sql stable security definer
set search_path = public, pg_temp
as $$
  with me as (select nullif(current_setting('app.user_id', true), '')::uuid as u),
  groups as (select m.group_id from membership m, me where m.user_id = me.u),
  -- units the caller can discover, with their code length only (never the vectors)
  docs as materialized (
    select w.asset_id, w.lex_len
      from code_words w
      join asset a on a.id = w.asset_id
     where a.discovery_scope in (select group_id from groups)),
  stats as materialized (
    select count(*)::real as n, greatest(avg(lex_len), 1e-9)::real as avgdl from docs),
  -- postings of the query's words, among discoverable units only
  post as materialized (
    select t.asset_id, t.lexeme, t.tf, d.lex_len
      from code_terms t
      join docs d on d.asset_id = t.asset_id
     where t.lexeme = any(p_lex)),
  df as materialized (select lexeme, count(*)::real as df from post group by lexeme)
  select p.asset_id,
         sum(ln(1 + (s.n - df.df + 0.5) / (df.df + 0.5))
             * p.tf * (p_k1 + 1)
             / (p.tf + p_k1 * (1 - p_b + p_b * p.lex_len / s.avgdl)))::real as score
    from post p
    join df on df.lexeme = p.lexeme
   cross join stats s
   group by p.asset_id
   order by 2 desc, p.asset_id
   limit p_limit
$$;

create or replace function code_words_explain(p_lex text[], p_ids uuid[])
returns table (asset_id uuid, matched int, terms text[])
language sql stable security definer
set search_path = public, pg_temp
as $$
  with me as (select nullif(current_setting('app.user_id', true), '')::uuid as u),
  groups as (select m.group_id from membership m, me where m.user_id = me.u),
  readable as (select s.source_ref from source_acl s, me where s.user_id = me.u),
  m as (
    select t.asset_id, t.lexeme, a.source_ref in (select source_ref from readable) as can_read
      from code_terms t
      join asset a on a.id = t.asset_id
     where t.asset_id = any(p_ids)
       and a.discovery_scope in (select group_id from groups)
       and t.lexeme = any(p_lex))
  select m.asset_id, count(*)::int,
         case when bool_and(m.can_read) then array_agg(m.lexeme order by m.lexeme) end
    from m
   group by m.asset_id
$$;

alter function code_words_hits(tsquery, text[], int, real, real) owner to homegrown_search;
alter function code_words_explain(text[], uuid[]) owner to homegrown_search;
