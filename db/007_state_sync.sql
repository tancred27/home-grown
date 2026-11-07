-- State-based sync: the memory follows each repo's DEFAULT BRANCH state, eventually.
--
-- Intake (a poller by default, webhooks optionally) only marks a repo dirty. A worker
-- later claims a due repo under a lease, reads the default branch's current tree,
-- compares Git's own fingerprints (tree SHA per unit folder, blob SHA per file the
-- collector reads) with the last processed state, and re-processes only units whose
-- fingerprint changed. Merges, rebases, force-pushes and reverts are just new states.

create table if not exists repo_state (
  repo               text primary key,          -- "org/name"
  default_branch     text,
  dirty              boolean not null default false,
  first_pending_at   timestamptz,               -- oldest unprocessed change seen
  last_seen_at       timestamptz,               -- newest change seen
  last_processed_sha text,
  last_processed_at  timestamptz,
  head_ci            text,                      -- success | failure | pending | none
  lease_owner        text,
  lease_until        timestamptz,
  failures           int not null default 0,
  last_error         text,
  daily_due_at       timestamptz                -- next contributors/stage refresh
);
create index if not exists repo_state_due on repo_state (first_pending_at) where dirty;

-- Fingerprint of each unit as last processed: the unit folder's tree SHA, and the blob
-- SHA of every file the collector reads in it (manifests, READMEs, tests).
create table if not exists unit_fingerprint (
  unit_id    text primary key,
  repo       text not null,
  tree_sha   text not null,
  files      jsonb not null default '{}',
  updated_at timestamptz not null default now()
);
create index if not exists unit_fingerprint_repo on unit_fingerprint (repo);

-- CI result of the default-branch head, kept as information on the asset (never a gate).
alter table asset add column if not exists ci_status text;
alter table asset add column if not exists ci_sha text;
alter table asset add column if not exists ci_checked_at timestamptz;

-- Skip re-embedding unchanged text: the hash of the text a vector was computed from.
alter table embedding add column if not exists text_hash text;
