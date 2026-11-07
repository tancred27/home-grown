-- Indexed keyword search under row-level security.
--
-- Postgres won't use a GIN index for `tsvector @@ tsquery` on an RLS-protected table:
-- the operator (ts_match_vq) is not LEAKPROOF, so the permission filter runs first and
-- the text match is then evaluated on every visible row. Measured: 55 ms at 3k claims
-- under RLS vs 1.2 ms with the index, and the gap grows with the org.
--
-- fts_hits() does the indexed lookups with RLS bypassed, then applies THE SAME
-- visibility rules as the policies in 002_rls.sql, explicitly:
--   asset visible   <=> asset.discovery_scope is one of the caller's groups
--   claim visible   <=> its asset is visible AND (not sensitive OR the caller can read
--                       the asset's source)
--   no caller        => no groups => nothing (fail closed)
-- tests/test_leakage.py runs against it, so the two copies of these rules can't drift
-- silently. It returns only asset ids and ranks; search.py joins them back to `asset`
-- UNDER RLS, a second check.
--
-- Least privilege: the function is owned by orgmem_search, a NOLOGIN role that may
-- bypass RLS but can only SELECT these four tables.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'orgmem_search') then
    create role orgmem_search nologin bypassrls;
  end if;
end $$;
grant usage on schema public to orgmem_search;
grant select on asset, claim, membership, source_acl to orgmem_search;

create or replace function fts_hits(p_query tsquery, p_limit int)
returns table (asset_id uuid, arank real, crank real)
language sql stable security definer
set search_path = public, pg_temp
as $$
  with me as (select nullif(current_setting('app.user_id', true), '')::uuid as u),
  groups as (select m.group_id from membership m, me where m.user_id = me.u),
  a_hits as (
    select a.id
      from asset a
     where a.tsv @@ p_query
       and a.discovery_scope in (select group_id from groups)),
  c_hits as (
    select c.asset_id, max(ts_rank(to_tsvector('english', c.body), p_query)) as r
      from claim c
      join asset a on a.id = c.asset_id
     where c.valid_to is null
       and to_tsvector('english', c.body) @@ p_query
       and a.discovery_scope in (select group_id from groups)
       and (not c.sensitive
            or exists (select 1 from source_acl s, me
                        where s.user_id = me.u and s.source_ref = a.source_ref))
     group by c.asset_id),
  hits as (select id from a_hits union select asset_id from c_hits)
  select h.id,
         ts_rank(a.tsv, p_query) as arank,
         coalesce(c.r, 0) as crank
    from hits h
    join asset a on a.id = h.id
    left join c_hits c on c.asset_id = h.id
   order by 2 desc, 3 desc
   limit p_limit
$$;

alter function fts_hits(tsquery, int) owner to orgmem_search;
revoke all on function fts_hits(tsquery, int) from public;
grant execute on function fts_hits(tsquery, int) to orgmem_app;
