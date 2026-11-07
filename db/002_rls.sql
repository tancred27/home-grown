-- Row-level security.
--
-- The three visibility questions, enforced here rather than in application code:
--   1. Can I know it exists?  -> asset.discovery_scope is one of my groups
--   2. Can I read details?    -> I have source_acl on asset.source_ref
--   3. Can I use it?          -> application concern (access steps, approval)
--
-- The application connects as orgmem_app, which is NOT a superuser and is
-- explicitly NOBYPASSRLS. A superuser bypasses every policy below, so using the
-- bootstrap role at runtime would silently disable all of this.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'orgmem_app') then
    create role orgmem_app login password 'orgmem_app_dev_only' nobypassrls;
  end if;
end $$;

grant usage on schema public to orgmem_app;
grant select, insert, update on all tables in schema public to orgmem_app;
grant usage, select on all sequences in schema public to orgmem_app;
alter default privileges in schema public
  grant select, insert, update on tables to orgmem_app;

-- Helper: the caller, from a per-request setting. NULL when unset, which makes
-- every policy below fail closed rather than open.
create or replace function current_app_user() returns uuid as $$
  select nullif(current_setting('app.user_id', true), '')::uuid
$$ language sql stable;

create or replace function caller_groups() returns setof uuid as $$
  select group_id from membership where user_id = current_app_user()
$$ language sql stable;

create or replace function caller_can_read_source(p_source_ref text) returns boolean as $$
  select exists (
    select 1 from source_acl
    where user_id = current_app_user() and source_ref = p_source_ref)
$$ language sql stable;

-- ---------------------------------------------------------------- asset

alter table asset enable row level security;
alter table asset force row level security;

create policy asset_discover on asset for select to orgmem_app
  using (discovery_scope in (select caller_groups()));

-- ---------------------------------------------------------------- claim
-- A claim is visible if its asset is visible. A SENSITIVE claim additionally
-- requires read access to the source: this is how a private repo can be
-- discoverable without leaking what is inside it.

alter table claim enable row level security;
alter table claim force row level security;

create policy claim_read on claim for select to orgmem_app
  using (
    exists (select 1 from asset a where a.id = claim.asset_id)
    and (not sensitive or caller_can_read_source(
          (select source_ref from asset a where a.id = claim.asset_id)))
  );

-- ---------------------------------------------------------------- card_version
-- Rows are visible with the asset; `detail` is stripped by the view below.

alter table card_version enable row level security;
alter table card_version force row level security;

create policy card_read on card_version for select to orgmem_app
  using (exists (select 1 from asset a where a.id = card_version.asset_id));

-- The only thing the API should read. `detail` becomes '{}' unless the caller
-- can read the source. Keeping this in SQL means an application bug cannot leak it.
create or replace view card_visible as
  select cv.asset_id,
         cv.version,
         cv.fields,
         case when caller_can_read_source(a.source_ref)
              then cv.detail else '{}'::jsonb end as detail,
         caller_can_read_source(a.source_ref) as has_source_access,
         cv.claim_ids, cv.valid_from, cv.valid_to
    from card_version cv
    join asset a on a.id = cv.asset_id
   where cv.valid_to is null;

grant select on card_visible to orgmem_app;

-- ---------------------------------------------------------------- edge
-- An edge is only returned when BOTH ends are visible. The application renders
-- a placeholder ("depends on something you cannot see") for the hidden ones, so
-- the name of an invisible asset is never returned.

alter table edge enable row level security;
alter table edge force row level security;

create policy edge_read on edge for select to orgmem_app
  using (
    exists (select 1 from asset a where a.id = edge.src)
    and exists (select 1 from asset a where a.id = edge.dst)
  );

-- ---------------------------------------------------------------- embedding
-- Vectors of private text follow the same rules. No cross-scope index.

alter table embedding enable row level security;
alter table embedding force row level security;

create policy embedding_read on embedding for select to orgmem_app
  using (exists (select 1 from asset a where a.id = embedding.asset_id));

-- ---------------------------------------------------------------- outcomes

alter table reuse_outcome enable row level security;
alter table reuse_outcome force row level security;

create policy outcome_read on reuse_outcome for select to orgmem_app
  using (exists (select 1 from asset a where a.id = reuse_outcome.asset_id));

create policy outcome_insert on reuse_outcome for insert to orgmem_app
  with check (exists (select 1 from asset a where a.id = reuse_outcome.asset_id));

-- ---------------------------------------------------------------- questions
-- You can see your own questions, not other people's.

alter table question enable row level security;
alter table question force row level security;

create policy question_own on question for select to orgmem_app
  using (asker_id = current_app_user());
create policy question_insert on question for insert to orgmem_app
  with check (asker_id = current_app_user());
