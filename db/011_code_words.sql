-- The code's own words as a third keyword list (experiments/scale/RESULTS_gate1.md,
-- addenda 35-37: confirmed on 196 fresh questions, +8 to +11 points in the top 5).
--
-- For each crawled unit, the crawler stores the words of its code: declaration lines and
-- doc comments from the unit's areas, already scrubbed of keys, passwords and internal
-- hostnames (extract/code.py), with identifiers also split into words (getUserToken ->
-- get user token). Search ranks them with BM25 in their own list, so long code text never
-- dilutes an asset's normal keyword score, and fuses that list with the other two by RRF.
--
-- Visibility: a unit's code words rank it for anyone who can DISCOVER the unit, like its
-- capability claims and area summaries (both also written from the code). Which words
-- matched is shown only to people who can READ the unit's source: code_words_explain()
-- returns terms only for them. Statistics count only units the caller can discover.

create table if not exists code_words (
  asset_id    uuid primary key references asset(id) on delete cascade,
  tsv         tsvector not null,
  lex_len     int generated always as (tsv_tokens(tsv)) stored,
  commit_sha  text,
  updated_at  timestamptz not null default now()
);
create index if not exists code_words_tsv_idx on code_words using gin (tsv);

-- Nobody reads the table directly: search and explain go through the functions below.
alter table code_words enable row level security;
alter table code_words force row level security;
revoke all on code_words from homegrown_app;
grant select on code_words to homegrown_search;

create or replace function code_words_hits(p_query tsquery, p_lex text[], p_limit int,
                                           p_k1 real default 1.2, p_b real default 0.75)
returns table (asset_id uuid, score real)
language sql stable security definer
set search_path = public, pg_temp
as $$
  with me as (select nullif(current_setting('app.user_id', true), '')::uuid as u),
  groups as (select m.group_id from membership m, me where m.user_id = me.u),
  docs as materialized (
    select w.asset_id, w.tsv, w.lex_len
      from code_words w
      join asset a on a.id = w.asset_id
     where a.discovery_scope in (select group_id from groups)),
  stats as materialized (
    select count(*)::real as n, greatest(avg(lex_len), 1e-9)::real as avgdl from docs),
  hits as materialized (select d.asset_id, d.tsv, d.lex_len from docs d where d.tsv @@ p_query),
  tf as materialized (
    select h.asset_id, l.lexeme as t, coalesce(array_length(l.positions, 1), 1)::real as f,
           h.lex_len
      from hits h cross join lateral unnest(h.tsv) l
     where l.lexeme = any(p_lex)),
  df as materialized (
    select t.t, (select count(*) from docs d where d.tsv @@ quote_literal(t.t)::tsquery)::real as df
      from (select distinct unnest(p_lex) as t) t)
  select tf.asset_id,
         sum(ln(1 + (s.n - df.df + 0.5) / (df.df + 0.5))
             * tf.f * (p_k1 + 1)
             / (tf.f + p_k1 * (1 - p_b + p_b * tf.lex_len / s.avgdl)))::real as score
    from tf
    join df on df.t = tf.t
   cross join stats s
   group by tf.asset_id
   order by 2 desc, tf.asset_id
   limit p_limit
$$;

-- For answer traces: how many query words matched each unit's code, and which ones, but
-- the words only when the caller can read that unit's source.
create or replace function code_words_explain(p_lex text[], p_ids uuid[])
returns table (asset_id uuid, matched int, terms text[])
language sql stable security definer
set search_path = public, pg_temp
as $$
  with me as (select nullif(current_setting('app.user_id', true), '')::uuid as u),
  groups as (select m.group_id from membership m, me where m.user_id = me.u),
  readable as (select s.source_ref from source_acl s, me where s.user_id = me.u),
  m as (
    select w.asset_id, l.lexeme, a.source_ref in (select source_ref from readable) as can_read
      from code_words w
      join asset a on a.id = w.asset_id
     cross join lateral unnest(w.tsv) l
     where w.asset_id = any(p_ids)
       and a.discovery_scope in (select group_id from groups)
       and l.lexeme = any(p_lex))
  select m.asset_id, count(*)::int,
         case when bool_and(m.can_read) then array_agg(m.lexeme order by m.lexeme) end
    from m
   group by m.asset_id
$$;

alter function code_words_hits(tsquery, text[], int, real, real) owner to homegrown_search;
alter function code_words_explain(text[], uuid[]) owner to homegrown_search;
revoke all on function code_words_hits(tsquery, text[], int, real, real) from public;
revoke all on function code_words_explain(text[], uuid[]) from public;
grant execute on function code_words_hits(tsquery, text[], int, real, real) to homegrown_app;
grant execute on function code_words_explain(text[], uuid[]) to homegrown_app;
