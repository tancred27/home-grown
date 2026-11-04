# Experiment results (sandbox run, 2026-11-04/05)

## A. Unit detection (units.py + gt.py)
10 public repos, manifest-based detector, path-noise filter.
| repo | manifests | filtered as noise | kept |
|---|---|---|---|
| babel | 209 | 46 | 163 |
| backstage | 279 | 35 | 244 |
| flask | 4 | 3 | 1 |
| grafana | 100 | 2 | 98 |
| kustomize | 57 | 16 | 41 |
| langchain | 22 | 0 | 22 |
| opentelemetry-collector-contrib | 365 | 7 | 358 |
| superset | 36 | 1 | 35 |
| tokio | 13 | 1 | 12 |
| turborepo | 1245 | 1134 | 111 |

Recall vs declared workspace members: babel 0.96, backstage 1.00, tokio 0.90, turborepo 0.98.
Extras: root dir, .devcontainer, per-platform binary packages (turborepo), fuzz dirs, contrib examples.
Takeaway: finding manifests is easy; deciding which units are worth a card is the real problem.
Rough token cost of reading README + description for kept units: babel ~34K, backstage ~105K, otel-contrib ~460K.

## B. Permissions in Postgres (expb.py, expb2.py)
50,000 assets, 200 groups, 100 users, each user in 3 groups (~1.5% visible).
- Leak test (vector, full-text, edge-join, count queries as each user): 1,511 rows checked, 0 leaks.
- HNSW + RLS: mean recall@10 0.42; mean 0.57 of 10 results returned; 56 of 100 users got zero results (silent).
- Exact filtered scan: 1.3 ms p50 at 50K rows; 75.6 ms p50 / 88.5 ms p95 at 500K rows.
- FTS under RLS: 19.3 ms p50 / 22.7 ms p95 at 50K rows.
Caveats: synthetic clustered vectors (64-dim), pgvector 0.6.0, random ACLs.

## D. Retrieval with different wording (expd.py, expd2.py)
998 npm package descriptions, 25 paraphrased queries with no topic keywords. Labels = topic cluster the package came from (keyword search ranked, so noisy and lexically biased).
| method | precision@10 | MRR | queries with 0 relevant in top 10 |
|---|---|---|---|
| TF-IDF | 0.20 | 0.56 | 6/25 |
| LSA k=50 | 0.28 | 0.27 | 13/25 |
| LSA k=150 | 0.24 | 0.45 | 10/25 |
| hybrid TF-IDF + LSA150 | 0.21 | 0.51 | 9/25 |
With agent-style keyword expansion appended (written by the experimenter knowing the topics, so optimistic):
| TF-IDF | 0.87 | 0.94 | 0/25 |
| LSA k=50 | 0.98 | 1.00 | 0/25 |
| LSA k=150 | 0.96 | 1.00 | 0/25 |
| hybrid | 0.94 | 1.00 | 0/25 |
Not tested: real pretrained embeddings (Hugging Face was blocked in the sandbox).

## Not run
Experiment C (test-based fit verification across near-duplicate libraries), claim-accuracy evaluation, agent adherence, duplicate detection quality, cost per event.
