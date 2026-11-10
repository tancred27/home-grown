-- BM25 keyword scoring under row-level security (the "H-all" retrieval chosen in
-- experiments/scale/RESULTS_gate1.md, addendum 15).
--
-- An asset's keyword document is its own text (asset.tsv: name, aliases, summary) plus the
-- current claims the caller may read, the claims at half weight. Scores use
-- BM25 with k1 = 1.2, b = 0.75 and idf = ln(1 + (N - df + 0.5) / (df + 0.5)), the same
-- formula as scripts/gate1_harness.py.
--
-- Corpus statistics (N, document frequency, average length) count ONLY assets the caller
-- can discover, so another team's hidden assets can't influence anyone's ranking.
--
-- Like fts_hits() (008_fts.sql), bm25_hits() runs as homegrown_search so it can use the
-- GIN indexes, and applies the policies' visibility rules itself:
--   asset visible  <=> asset.discovery_scope is one of the caller's groups
--   claim visible  <=> its asset is visible AND (not sensitive OR the caller can read
--                      the asset's source)
--   no caller       => no groups => nothing (fail closed)
-- tests/test_leakage.py holds it to those rules. search.py joins the ids back to `asset`
-- under RLS, a second check.

-- Token count of a tsvector as BM25 counts it: occurrences (positions), 1 for a
-- position-less lexeme.
create or replace function tsv_tokens(v tsvector) returns int
language sql immutable parallel safe
as $$ select coalesce(sum(coalesce(array_length(l.positions, 1), 1)), 0)::int from unnest(v) l $$;

-- Stored lengths, so average document length needs no per-query re-tokenising.
-- Generated columns are computed after BEFORE triggers, so asset.lex_len follows the
-- tsv that asset_tsv_trg sets.
alter table asset add column if not exists lex_len int
  generated always as (tsv_tokens(tsv)) stored;
alter table claim add column if not exists lex_len int
  generated always as (tsv_tokens(to_tsvector('english', body))) stored;

create or replace function bm25_hits(p_query tsquery, p_lex text[], p_limit int,
                                     p_k1 real default 1.2, p_b real default 0.75)
returns table (asset_id uuid, score real)
language sql stable security definer
set search_path = public, pg_temp
as $$
  with me as (select nullif(current_setting('app.user_id', true), '')::uuid as u),
  groups as (select m.group_id from membership m, me where m.user_id = me.u),
  readable as (select s.source_ref from source_acl s, me where s.user_id = me.u),
  -- every current claim the caller may read, with its asset
  vclaim as materialized (
    select c.asset_id, c.body, c.lex_len
      from claim c
      join asset a on a.id = c.asset_id
     where c.valid_to is null
       and a.discovery_scope in (select group_id from groups)
       and (not c.sensitive or a.source_ref in (select source_ref from readable))),
  -- document length of every visible asset
  doclen as materialized (
    select a.id, a.lex_len + 0.5 * coalesce(cl.s, 0) as len
      from asset a
      left join (select asset_id, sum(lex_len) as s from vclaim group by asset_id) cl
             on cl.asset_id = a.id
     where a.discovery_scope in (select group_id from groups)),
  stats as materialized (select count(*)::real as n, greatest(avg(len), 1e-9)::real as avgdl from doclen),
  -- candidates: visible assets whose text or a readable claim matches the query
  hits as materialized (
    select a.id
      from asset a
     where a.tsv @@ p_query
       and a.discovery_scope in (select group_id from groups)
    union
    select c.asset_id
      from claim c
      join asset a on a.id = c.asset_id
     where to_tsvector('english', c.body) @@ p_query
       and c.valid_to is null
       and a.discovery_scope in (select group_id from groups)
       and (not c.sensitive or a.source_ref in (select source_ref from readable))),
  -- term frequency per candidate and query lexeme; claims at half weight
  tf as materialized (
    select x.id, x.t, sum(x.f) as f
      from (select h.id, l.lexeme as t, coalesce(array_length(l.positions, 1), 1)::real as f
              from hits h
              join asset a on a.id = h.id
              cross join lateral unnest(a.tsv) l
             where l.lexeme = any(p_lex)
            union all
            select h.id, l.lexeme, 0.5 * coalesce(array_length(l.positions, 1), 1)
              from hits h
              join vclaim c on c.asset_id = h.id
              cross join lateral unnest(to_tsvector('english', c.body)) l
             where l.lexeme = any(p_lex)) x
     group by x.id, x.t),
  -- document frequency per query lexeme, over visible assets only
  df as materialized (
    select t.t,
           (select count(*) from (
              select a.id
                from asset a
               where a.tsv @@ quote_literal(t.t)::tsquery
                 and a.discovery_scope in (select group_id from groups)
              union
              select c.asset_id
                from claim c
                join asset a on a.id = c.asset_id
               where to_tsvector('english', c.body) @@ quote_literal(t.t)::tsquery
                 and c.valid_to is null
                 and a.discovery_scope in (select group_id from groups)
                 and (not c.sensitive or a.source_ref in (select source_ref from readable))) d
           )::real as df
      from (select distinct unnest(p_lex) as t) t)
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
revoke all on function bm25_hits(tsquery, text[], int, real, real) from public;
grant execute on function bm25_hits(tsquery, text[], int, real, real) to homegrown_app;
