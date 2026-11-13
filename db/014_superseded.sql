-- Manifest units covered by crawler units.
--
-- Without the crawler, a unit is a folder with a package manifest. With the crawler on,
-- the crawler's units (db/010) describe the same code from the source, so each manifest
-- unit in a crawled repo points at the crawler unit covering its folder and search skips
-- it. Nothing is deleted: turning the crawler off or clearing the column brings it back.

alter table asset add column if not exists superseded_by uuid
  references asset(id) on delete set null;
create index if not exists asset_superseded_idx on asset (superseded_by)
  where superseded_by is not null;
