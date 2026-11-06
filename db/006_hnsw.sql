-- Approximate vector index, used only when settings.vector_search = 'hnsw'.
--
-- Exact scan (the default) stays correct and fast enough to ~1M vectors: measured
-- 0.2-0.7 s at 1M under RLS (experiments/scale/ann_1m.txt). Past that, search uses this
-- index with pgvector 0.8's iterative scan to fetch a candidate POOL, then ranks the
-- pool exactly (search.py). In exact mode the index is simply never used: search()
-- disables index scans for its vector statement.
create index if not exists embedding_vec_hnsw on embedding
  using hnsw (vec vector_cosine_ops) with (m = 16, ef_construction = 64);
