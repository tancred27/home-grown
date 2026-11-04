# Local Postgres + pgvector for experiment B

Tested on Ubuntu with apt packages `postgresql-16` and `postgresql-16-pgvector` (pgvector 0.6.0).

```bash
sudo apt-get install -y postgresql-16 postgresql-16-pgvector
B=/usr/lib/postgresql/16/bin; D=/var/tmp/pgdata
sudo mkdir -p $D && sudo chown postgres $D
sudo -u postgres $B/initdb -D $D -A trust
sudo -u postgres $B/pg_ctl -D $D -o '-p 5433 -k /tmp' -l /var/tmp/pg.log start
psql -h /tmp -p 5433 -U postgres -c 'create extension vector;'   # sanity check (inside the default db)
```

`expb.py` connects with `host=/tmp port=5433 user=postgres`, creates its own `expb` database and an `app` role (non-superuser, so row-level security applies — superusers bypass it).
`expb2.py` must run after `expb.py` (it adds 450,000 rows to the same database).

pgvector 0.8+ has iterative index scans, which may fix the filtered-HNSW problem. Not tested; try it.
