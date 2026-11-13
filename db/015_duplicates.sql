-- Duplicates across teams (behind HOMEGROWN_DUPLICATES_ENABLED, off by default).
--
-- Admin-only data: capability labels, judged pairs and proposed groups of units owned by
-- different teams that do the same job. Nothing here is read through as_user(): the
-- application role has no grant, and row-level security with no policy denies it anyway.
-- The admin batch job (src/homegrown/duplicates.py) and the admin API use the owner
-- connection. Nothing here deletes or deprecates a unit.

create table if not exists capability_label (
  asset_id   uuid not null references asset(id) on delete cascade,
  label      text not null,
  model      text,
  created_at timestamptz not null default now(),
  primary key (asset_id, label)
);

create table if not exists dup_edge (
  anchor     uuid not null references asset(id) on delete cascade,
  other      uuid not null references asset(id) on delete cascade,
  relation   text not null check (relation in ('same','overlapping','different')),
  reason     text,
  capability text,
  model      text,
  created_at timestamptz not null default now(),
  primary key (anchor, other)
);

create table if not exists capability_group (
  id          uuid primary key default gen_random_uuid(),
  capability  text,
  status      text not null default 'proposed'
              check (status in ('proposed','confirmed','rejected')),
  reviewed_by text,
  reviewed_at timestamptz,
  created_at  timestamptz not null default now()
);

create table if not exists capability_group_member (
  group_id uuid not null references capability_group(id) on delete cascade,
  asset_id uuid not null references asset(id) on delete cascade,
  primary key (group_id, asset_id)
);

do $$
declare t text;
begin
  foreach t in array array['capability_label','dup_edge','capability_group',
                           'capability_group_member'] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('revoke all on %I from homegrown_app', t);   -- default privileges granted it
  end loop;
end $$;
