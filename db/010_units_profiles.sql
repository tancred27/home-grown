-- Code-reading crawler (units and profiles) and per-answer traces.
--
-- Units: a repo is split into units with standalone reuse value (services, libraries,
-- tools, pipelines, integrations, shared standards). Each unit points at its repo through
-- asset.parent_id. Small features are detail inside a unit, never a unit of their own.
--
-- Areas: groups of source folders. Each area has a short model-written summary, cached by
-- the hash of the extracted text, so a re-crawl only re-summarises areas that changed.
--
-- Profiles: one per unit, written from the README, the folder list and the area summaries.
-- Code is the source of truth; the README is one input.
--
-- Traces: every answer can be explained step by step (search scores, fusion, judge
-- verdicts, timings, where each matched field came from). A trace is readable by the
-- person who asked; operators read them through the owner connection.

-- ---------------------------------------------------------------- hierarchy

alter table asset add column if not exists parent_id uuid references asset(id);
create index if not exists asset_parent_idx on asset (parent_id);

alter table asset drop constraint if exists asset_kind_ck;
alter table asset add constraint asset_kind_ck check (kind in
  ('library','service','dataset','job','ui','tool','script','solved_problem',
   'repo','integration','standard'));

-- ---------------------------------------------------------------- areas

create table if not exists area (
  id            uuid primary key default gen_random_uuid(),
  repo          text not null,                 -- owner/name
  path          text not null,                 -- area path, at most 3 levels
  content_hash  text not null,                 -- hash of the extracted text summarised
  commit_sha    text,
  files         text[] not null default '{}',
  summary       text,                          -- null until summarised
  model         text,
  prompt_version text,
  asset_id      uuid references asset(id) on delete set null,   -- the unit it belongs to
  updated_at    timestamptz not null default now(),
  unique (repo, path)
);
create index if not exists area_asset_idx on area (asset_id);

-- ---------------------------------------------------------------- profiles

create table if not exists profile (
  asset_id          uuid primary key references asset(id) on delete cascade,
  purpose           text not null,
  capabilities      text[] not null default '{}',
  search_terms      text[] not null default '{}',
  example_questions text[] not null default '{}',
  distinct_from     text,
  areas             text[] not null default '{}',   -- area paths the unit covers
  model             text not null,
  prompt_version    text not null,
  commit_sha        text,
  input_hash        text not null,                  -- what the compiler saw; skip if unchanged
  updated_at        timestamptz not null default now()
);

-- ---------------------------------------------------------------- traces

create table if not exists query_trace (
  question_id  uuid primary key references question(id) on delete cascade,
  asker_id     uuid references app_user(id),
  trace        jsonb not null,
  created_at   timestamptz not null default now()
);
create index if not exists query_trace_created_idx on query_trace (created_at);

-- ---------------------------------------------------------------- row-level security

alter table area enable row level security;
alter table area force row level security;
drop policy if exists area_read on area;
create policy area_read on area for select to homegrown_app
  using (asset_id is not null and exists (select 1 from asset a where a.id = area.asset_id));

alter table profile enable row level security;
alter table profile force row level security;
drop policy if exists profile_read on profile;
create policy profile_read on profile for select to homegrown_app
  using (exists (select 1 from asset a where a.id = profile.asset_id));

alter table query_trace enable row level security;
alter table query_trace force row level security;
drop policy if exists trace_own on query_trace;
create policy trace_own on query_trace for select to homegrown_app
  using (asker_id = current_app_user());
drop policy if exists trace_insert on query_trace;
create policy trace_insert on query_trace for insert to homegrown_app
  with check (asker_id = current_app_user());

grant select on area, profile to homegrown_app;
grant select, insert on query_trace to homegrown_app;

-- ---------------------------------------------------------------- keyword text
-- A unit's keyword document also carries its profile's search terms and example questions
-- (capabilities are already claims). The crawler touches the asset after writing a profile.

create or replace function asset_tsv_update() returns trigger as $$
begin
  new.tsv := to_tsvector('english',
      coalesce(new.name,'') || ' ' ||
      coalesce(array_to_string(new.aliases,' '),'') || ' ' ||
      coalesce(new.summary,'') || ' ' ||
      coalesce((select array_to_string(p.search_terms || p.example_questions, ' ')
                  from profile p where p.asset_id = new.id), ''));
  new.updated_at := now();
  return new;
end $$ language plpgsql;

-- ---------------------------------------------------------------- explainability

-- The text behind each vector, so a trace can show which field matched and what it said.
alter table embedding add column if not exists text text;

-- Per-term breakdown of bm25_hits() for given assets: the same formula, visibility rules
-- and corpus statistics (only what the caller can discover). Used by answer traces.
create or replace function bm25_explain(p_query tsquery, p_lex text[], p_ids uuid[],
                                        p_k1 real default 1.2, p_b real default 0.75)
returns table (asset_id uuid, term text, tf_text real, tf_claims real, df real, idf real,
               score real, doc_len real, avgdl real, n real)
language sql stable security definer
set search_path = public, pg_temp
as $$
  with me as (select nullif(current_setting('app.user_id', true), '')::uuid as u),
  groups as (select m.group_id from membership m, me where m.user_id = me.u),
  readable as (select s.source_ref from source_acl s, me where s.user_id = me.u),
  vclaim as materialized (
    select c.asset_id, c.body, c.lex_len
      from claim c join asset a on a.id = c.asset_id
     where c.valid_to is null
       and a.discovery_scope in (select group_id from groups)
       and (not c.sensitive or a.source_ref in (select source_ref from readable))),
  doclen as materialized (
    select a.id, a.lex_len + 0.5 * coalesce(cl.s, 0) as len
      from asset a
      left join (select asset_id, sum(lex_len) as s from vclaim group by asset_id) cl
             on cl.asset_id = a.id
     where a.discovery_scope in (select group_id from groups)),
  stats as materialized (select count(*)::real as n, greatest(avg(len), 1e-9)::real as avgdl
                           from doclen),
  ids as materialized (select d.id from doclen d where d.id = any(p_ids)),
  tfa as materialized (
    select i.id, l.lexeme as t, sum(coalesce(array_length(l.positions, 1), 1))::real as f
      from ids i join asset a on a.id = i.id cross join lateral unnest(a.tsv) l
     where l.lexeme = any(p_lex) group by i.id, l.lexeme),
  tfc as materialized (
    select i.id, l.lexeme as t, sum(coalesce(array_length(l.positions, 1), 1))::real as f
      from ids i join vclaim c on c.asset_id = i.id
     cross join lateral unnest(to_tsvector('english', c.body)) l
     where l.lexeme = any(p_lex) group by i.id, l.lexeme),
  df as materialized (
    select t.t,
           (select count(*) from (
              select a.id from asset a
               where a.tsv @@ quote_literal(t.t)::tsquery
                 and a.discovery_scope in (select group_id from groups)
              union
              select c.asset_id from claim c join asset a on a.id = c.asset_id
               where to_tsvector('english', c.body) @@ quote_literal(t.t)::tsquery
                 and c.valid_to is null
                 and a.discovery_scope in (select group_id from groups)
                 and (not c.sensitive or a.source_ref in (select source_ref from readable))) d
           )::real as df
      from (select distinct unnest(p_lex) as t) t)
  select i.id, df.t, coalesce(tfa.f, 0), coalesce(tfc.f, 0), df.df,
         ln(1 + (s.n - df.df + 0.5) / (df.df + 0.5))::real,
         (case when coalesce(tfa.f, 0) + 0.5 * coalesce(tfc.f, 0) = 0 then 0 else
           ln(1 + (s.n - df.df + 0.5) / (df.df + 0.5))
           * (coalesce(tfa.f, 0) + 0.5 * coalesce(tfc.f, 0)) * (p_k1 + 1)
           / ((coalesce(tfa.f, 0) + 0.5 * coalesce(tfc.f, 0))
              + p_k1 * (1 - p_b + p_b * dl.len / s.avgdl)) end)::real,
         dl.len::real, s.avgdl, s.n
    from ids i
   cross join df
   cross join stats s
    join doclen dl on dl.id = i.id
    left join tfa on tfa.id = i.id and tfa.t = df.t
    left join tfc on tfc.id = i.id and tfc.t = df.t
   where coalesce(tfa.f, 0) + coalesce(tfc.f, 0) > 0
$$;

alter function bm25_explain(tsquery, text[], uuid[], real, real) owner to homegrown_search;
revoke all on function bm25_explain(tsquery, text[], uuid[], real, real) from public;
grant execute on function bm25_explain(tsquery, text[], uuid[], real, real) to homegrown_app;
