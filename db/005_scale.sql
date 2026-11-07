-- Scale: let the write path touch only what changed.
--
-- package_name: which unit publishes which package/module name (go.mod `module`,
-- package.json `name`). With it stored, refreshing ONE repo's dependencies needs only
-- that repo's manifests: "imports github.com/grafana/dskit" resolves by lookup instead of
-- by re-reading every manifest in the org (collector/usage.refresh).
-- Writer-side only: no grant to homegrown_app.

create table if not exists package_name (
  name       text primary key,
  unit_id    text not null,
  repo       text not null,
  updated_at timestamptz not null default now()
);
create index if not exists package_name_repo on package_name (repo);
