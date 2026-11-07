-- Org Capability Memory: schema.
-- Runs automatically on first container start (docker-entrypoint-initdb.d).

create extension if not exists vector;
create extension if not exists pg_trgm;

-- Embedding dimension is set by the model chosen at the retrieval gate.
-- Current: BAAI/bge-base-en-v1.5 -> 768. Changing the model means a new
-- embedding.model value and a re-embed, never an in-place overwrite.

-- ---------------------------------------------------------------- identity

create table team (
  id   uuid primary key default gen_random_uuid(),
  name text not null unique
);

create table app_user (
  id      uuid primary key default gen_random_uuid(),
  login   text not null unique,
  team_id uuid references team(id)
);

create table membership (
  user_id  uuid not null references app_user(id),
  group_id uuid not null references team(id),
  primary key (user_id, group_id)
);

-- Which sources a user may READ (synced from the source system, e.g. GitHub).
-- Distinct from membership: knowing a thing exists is not reading it.
create table source_acl (
  user_id    uuid not null references app_user(id),
  source_ref text not null,
  primary key (user_id, source_ref)
);

-- ---------------------------------------------------------------- what exists

create table asset (
  id              uuid primary key default gen_random_uuid(),
  kind            text not null,
  name            text not null,
  aliases         text[] not null default '{}',
  status          text not null default 'active',
  owner_team      uuid references team(id),
  support_channel text,
  source_ref      text not null,              -- repo + path; gates `detail`
  unit_id         text not null unique,        -- stable across renames
  discovery_scope uuid references team(id),    -- who may see the redacted card
  summary         text,                        -- one line, layer 1
  tsv             tsvector,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint asset_kind_ck check (kind in
    ('library','service','dataset','job','ui','tool','script','solved_problem')),
  constraint asset_status_ck check (status in
    ('experimental','active','deprecated','retired'))
);

create table rename_map (
  old_unit_id text primary key,
  new_unit_id text not null
);

-- ---------------------------------------------------------------- why we believe it

create table evidence (
  id                uuid primary key default gen_random_uuid(),
  source            text not null,
  ref               text not null,       -- file path, test name, or comment URL
  source_version    text not null,       -- commit sha or page version
  content_hash      text not null,       -- detects source drift
  excerpt           text,                -- the lines the claim came from
  extractor_version text not null,
  captured_at       timestamptz not null default now(),
  constraint evidence_source_ck check (source in
    ('github_code','github_test','github_discussion','github_commit',
     'confluence','elicited','reported'))
);
create index on evidence (ref);

-- ---------------------------------------------------------------- what we claim

create table claim (
  id          uuid primary key default gen_random_uuid(),
  asset_id    uuid not null references asset(id) on delete cascade,
  layer       smallint not null,
  kind        text not null,
  body        text not null,
  -- NOT NULL on purpose: a claim without evidence is rejected by design.
  evidence_id uuid not null references evidence(id),
  extractor   text not null,
  confidence  real not null default 0.5,
  sensitive   boolean not null default false,   -- true => detail layer only
  verified_at timestamptz,
  valid_from  timestamptz not null default now(),
  valid_to    timestamptz,                      -- null = current
  constraint claim_layer_ck check (layer between 1 and 3),
  constraint claim_kind_ck check (kind in
    ('capability','limit','refusal','scale','pitfall','outcome','access','lifecycle')),
  constraint claim_conf_ck check (confidence between 0 and 1)
);
create index on claim (asset_id, layer) where valid_to is null;
create index on claim (kind) where valid_to is null;

-- ---------------------------------------------------------------- the card

create table card_version (
  asset_id    uuid not null references asset(id) on delete cascade,
  version     int  not null,
  fields      jsonb not null,     -- REDACTED: visible within discovery_scope
  detail      jsonb not null default '{}'::jsonb,  -- needs source_acl on source_ref
  claim_ids   uuid[] not null default '{}',
  approved_by uuid references app_user(id),
  valid_from  timestamptz not null default now(),
  valid_to    timestamptz,
  primary key (asset_id, version)
);

-- ---------------------------------------------------------------- relations

create table edge (
  id          uuid primary key default gen_random_uuid(),
  src         uuid not null references asset(id) on delete cascade,
  dst         uuid not null references asset(id) on delete cascade,
  type        text not null,
  visibility  text not null default 'inherit',
  confidence  real not null default 0.5,
  evidence_id uuid references evidence(id),
  valid_from  timestamptz not null default now(),
  valid_to    timestamptz,
  constraint edge_type_ck check (type in
    ('depends_on','supersedes','duplicates','part_of','solves','owned_by'))
);
create index on edge (src) where valid_to is null;
create index on edge (dst) where valid_to is null;

-- ---------------------------------------------------------------- outcomes & demand

create table reuse_outcome (
  id            uuid primary key default gen_random_uuid(),
  asset_id      uuid not null references asset(id) on delete cascade,
  team_id       uuid references team(id),
  intent        text not null,
  outcome       text not null,
  reason        text,
  effort_hours  real,
  reported_by   uuid references app_user(id),
  reporter_kind text not null,
  at            timestamptz not null default now(),
  constraint ro_outcome_ck check (outcome in ('reused','extended','rejected','abandoned')),
  constraint ro_reporter_ck check (reporter_kind in ('person','agent'))
);

-- Every question asked. Drives elicitation, the adherence metric and the eval.
create table question (
  id            uuid primary key default gen_random_uuid(),
  asker_id      uuid references app_user(id),
  intent        text not null,
  keywords      text[] not null default '{}',
  route         text not null,
  results       jsonb,
  answered_well boolean,
  at            timestamptz not null default now(),
  constraint question_route_ck check (route in ('cli','mcp','ui','hook','test'))
);

-- Outstanding asks to owners, created by demand, never by a sweep.
create table elicitation (
  id          uuid primary key default gen_random_uuid(),
  asset_id    uuid not null references asset(id) on delete cascade,
  owner_team  uuid references team(id),
  question_id uuid references question(id),
  gap         text not null,
  prompt      text not null,
  status      text not null default 'pending',
  asked_at    timestamptz,
  answered_at timestamptz,
  constraint elicitation_status_ck check (status in
    ('pending','asked','answered','declined','expired'))
);

-- ---------------------------------------------------------------- plumbing

create table event (
  id          uuid primary key default gen_random_uuid(),
  source      text not null,
  idem_key    text not null unique,     -- source + object + version
  payload     jsonb not null,
  status      text not null default 'received',
  error       text,
  received_at timestamptz not null default now(),
  constraint event_status_ck check (status in
    ('received','deduped','filtered','drafted','pending_owner',
     'applied','rejected','ignored','failed'))
);
create index on event (status) where status not in ('applied','ignored','rejected');

create table embedding (
  asset_id uuid not null references asset(id) on delete cascade,
  field    text not null,
  model    text not null,
  vec      vector(768) not null,
  primary key (asset_id, field, model)
);

create table audit (
  id     uuid primary key default gen_random_uuid(),
  actor  uuid,
  action text not null,
  target uuid,
  reason text,
  at     timestamptz not null default now()
);

-- ---------------------------------------------------------------- search support

create or replace function asset_tsv_update() returns trigger as $$
begin
  new.tsv := to_tsvector('english',
      coalesce(new.name,'') || ' ' ||
      coalesce(array_to_string(new.aliases,' '),'') || ' ' ||
      coalesce(new.summary,''));
  new.updated_at := now();
  return new;
end $$ language plpgsql;

create trigger asset_tsv_trg before insert or update on asset
  for each row execute function asset_tsv_update();

create index asset_tsv_idx on asset using gin(tsv);
create index asset_name_trgm on asset using gin(name gin_trgm_ops);
create index claim_body_tsv on claim using gin(to_tsvector('english', body));
