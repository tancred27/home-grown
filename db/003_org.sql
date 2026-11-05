-- Org directory, contributors and solution stage. See docs/design.md section 5 and the
-- collection agent in src/orgmem/collector/.
--
-- What the answering agent needs, beyond "does it exist": who built it, which team
-- and line of business (LOB) they are in, who their manager is, and whether the
-- solution is in dev or prod.
--
-- PUBLIC-DATA NOTE: public repositories have contributors but no org chart. The
-- directory is SYNTHETIC: people are pseudonymous (handles derived from a hash of the
-- source login), and teams, LOBs and managers are generated. No real person's login is
-- ever joined to a made-up manager or LOB in anything the app can read.

-- ---------------------------------------------------------------- directory

create table if not exists lob (
  id   uuid primary key default gen_random_uuid(),
  name text not null unique
);

alter table team add column if not exists lob_id uuid references lob(id);

create table if not exists person (
  id           uuid primary key default gen_random_uuid(),
  handle       text not null unique,          -- pseudonymous, e.g. eng-7f3a2c
  display_name text not null,
  title        text,
  team_id      uuid references team(id),
  manager_id   uuid references person(id),
  synthetic    boolean not null default true
);
create index if not exists person_team_idx on person (team_id);

-- source login -> person. Holds only a HASH of the login, and the app role cannot read
-- it at all: only ingestion resolves identities.
create table if not exists source_identity (
  source     text not null,                   -- github | confluence | notion | ...
  login_hash text not null,                   -- sha256(login), hex
  person_id  uuid not null references person(id),
  primary key (source, login_hash)
);

-- ---------------------------------------------------------------- who built what

create table if not exists asset_contributor (
  asset_id    uuid not null references asset(id) on delete cascade,
  person_id   uuid not null references person(id),
  role        text not null,                  -- author | maintainer | contributor
  commits     int  not null default 0,
  first_at    timestamptz,
  last_at     timestamptz,
  evidence_id uuid references evidence(id),
  primary key (asset_id, person_id),
  constraint contributor_role_ck check (role in ('author','maintainer','contributor'))
);

-- ---------------------------------------------------------------- solution stage
-- Inferred from evidence (releases, version, archived flag, status notices) and owner
-- confirmed. Code alone cannot prove something runs in production; a real deployment
-- would feed this from its deploy system.

alter table asset add column if not exists stage text not null default 'unknown';
alter table asset add column if not exists stage_confidence real;
alter table asset add column if not exists stage_evidence_id uuid references evidence(id);
alter table asset add column if not exists stage_confirmed boolean not null default false;
do $$ begin
  alter table asset add constraint asset_stage_ck check (stage in
    ('dev','beta','prod','deprecated','retired','unknown'));
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------- events
-- The collection agent's queue already exists (table `event`). Add what a worker
-- needs to claim events safely and to retry.

alter table event add column if not exists kind text;
alter table event add column if not exists attempts int not null default 0;
alter table event add column if not exists claimed_at timestamptz;
alter table event add column if not exists processed_at timestamptz;
alter table event add column if not exists outcome jsonb;

-- ---------------------------------------------------------------- security

grant select on lob, person to orgmem_app;
grant select, insert, update on asset_contributor to orgmem_app;
revoke all on source_identity from orgmem_app;   -- ingestion only

-- The directory is internal-public: any identified caller may read it; an
-- unidentified one sees nothing (fail closed, like every other policy).
alter table lob enable row level security;
alter table lob force row level security;
drop policy if exists lob_read on lob;
create policy lob_read on lob for select to orgmem_app
  using (current_app_user() is not null);

alter table person enable row level security;
alter table person force row level security;
drop policy if exists person_read on person;
create policy person_read on person for select to orgmem_app
  using (current_app_user() is not null);

-- Who built an asset is visible only if the asset is. Otherwise a contributor list
-- would reveal that an invisible asset exists.
alter table asset_contributor enable row level security;
alter table asset_contributor force row level security;
drop policy if exists contributor_read on asset_contributor;
create policy contributor_read on asset_contributor for select to orgmem_app
  using (exists (select 1 from asset a where a.id = asset_contributor.asset_id));
