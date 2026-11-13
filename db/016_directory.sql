-- Minimal company-directory sync (src/homegrown/directory_sync.py).
--
-- A real directory feed (HR / SCIM / Okta / Entra, via `homegrown directory import`)
-- creates people with synthetic = false. Nothing raw is stored beyond what person
-- already held (display name, team, manager): emails and logins live only as sha256
-- hashes in source_identity, which the app role still cannot read.

alter table person add column if not exists external_id text unique;  -- directory id; null for synthetic people
alter table person add column if not exists active boolean not null default true;
alter table person add column if not exists synced_at timestamptz;

-- Which person the asking user is, so "who to ask" can skip the asker. Set by the
-- directory sync when the user's login matches a synced GitHub login; null otherwise.
alter table app_user add column if not exists person_id uuid references person(id);
