# Configuration

Settings come from environment variables with the `HOMEGROWN_` prefix, or from a `.env`
file in the working directory. Every setting is defined in
[`src/homegrown/config.py`](../src/homegrown/config.py).

## Database

| Variable | Default | |
|---|---|---|
| `HOMEGROWN_ADMIN_DSN` | `postgresql://homegrown:homegrown_dev_only@localhost:5433/homegrown` | Schema owner. Used for ingestion and sync, never for answering |
| `HOMEGROWN_APP_DSN` | `postgresql://homegrown_app:homegrown_app_dev_only@localhost:5433/homegrown` | Not a superuser, so row-level security applies. Used for every search |
| `HOMEGROWN_DB_POOL_SIZE` | `10` | Connections per pool |

The default passwords are for local development only. Change both roles' passwords
before exposing the database anywhere.

## Language model

| Variable | Default | |
|---|---|---|
| `HOMEGROWN_LLM_PROVIDER` | `anthropic` | `anthropic`, `groq`, `gemini`, `local` (any OpenAI-compatible server) or `disabled` |
| `HOMEGROWN_LLM_MODEL` | *(auto)* | Anthropic: `claude-opus-5-5`; Groq: `openai/gpt-oss-120b`; Gemini: newest stable flash model; `local`: required |
| `HOMEGROWN_LLM_BASE_URL` | `http://localhost:11434/v1` | For `local`, e.g. Ollama |
| `HOMEGROWN_VERDICT_MODE` | `rules` | `model` to have the LLM judge candidates. `rules` needs no model |
| `HOMEGROWN_QUERY_REWRITE` | `true` | Model mode only: restate the request before searching |
| `HOMEGROWN_VERDICT_SHORTLIST` | `5` | Model mode only: how many results the judge reads |

Keys:
- Anthropic reads `ANTHROPIC_API_KEY`.
- Groq and Gemini read `GROQ_API_KEY` / `GEMINI_API_KEY`, or a file named by
  `HOMEGROWN_GROQ_KEY_FILE` / `HOMEGROWN_GEMINI_KEY_FILE`.

The collector also uses the model, for purpose lines and for deciding whether a docs
page is worth keeping. If no model is available, purpose lines are skipped and pages
fall back to a keyword rule.

Free tiers of hosted providers may use your prompts for training. Don't send private
code to them; use a paid tier or `local`.

## Search

| Variable | Default | |
|---|---|---|
| `HOMEGROWN_EMBED_MODEL` | `BAAI/bge-base-en-v1.5` | Any fastembed model. Changing it needs new embeddings (`scripts/switch_embed_model.py`) |
| `HOMEGROWN_EMBED_DIM` | `768` | Must match the model |
| `HOMEGROWN_DEVICE` | `auto` | `auto`, `cpu` or `cuda` for embeddings |
| `HOMEGROWN_VECTOR_SEARCH` | `auto` | `exact`, `hnsw`, or `auto` (hnsw above the threshold) |
| `HOMEGROWN_HNSW_AUTO_THRESHOLD` | `60000` | Vectors (about 20k assets) |
| `HOMEGROWN_HNSW_POOL` | `400` | Candidates the index fetches before exact ranking |
| `HOMEGROWN_HNSW_EF_SEARCH` | `400` | HNSW search breadth |
| `HOMEGROWN_FUSION` | `interleave` | How keyword and vector results merge: `interleave` or `max` |
| `HOMEGROWN_RERANK_ENABLED` | `false` | Cross-encoder reranking. It lowered recall in our evaluation |
| `HOMEGROWN_RERANK_MODEL` | `jinaai/jina-reranker-v1-tiny-en` | |
| `HOMEGROWN_RERANK_DEPTH` | `30` | |
| `HOMEGROWN_ANSWER_RESULTS` | `5` | Results the answering agent shows |

## Sync

| Variable | Default | |
|---|---|---|
| `HOMEGROWN_GITHUB_TOKEN_FILE` | | Path to a file holding a GitHub token (read access only) |
| `HOMEGROWN_GITHUB_ORG` | `grafana` | Default org for the docs-page worker's repo metadata |
| `HOMEGROWN_SYNC_QUIET_S` | `120` | A changed repo is processed once it has been quiet this long... |
| `HOMEGROWN_SYNC_MAX_WAIT_S` | `480` | ...or has been waiting this long, whichever comes first |
| `HOMEGROWN_SYNC_LEASE_S` | `900` | How long a worker holds a repo |
| `HOMEGROWN_SYNC_POLL_S` | `300` | How often the poller lists changed repos |
| `HOMEGROWN_GITHUB_WEBHOOK_SECRET` | | Empty means every delivery is rejected |
| `HOMEGROWN_DOCS_WEBHOOK_SECRET` | | For the Confluence and Notion webhooks; empty rejects all |

## Collection behaviour

| Variable | Default | |
|---|---|---|
| `HOMEGROWN_OWNER_GATE` | `false` | `true`: ask the owner before publishing a new asset or applying a deprecation |
| `HOMEGROWN_DEFAULT_VISIBILITY` | `org-wide` | Scope for new assets when the owner isn't asked: `org-wide` or `team-only` |
| `HOMEGROWN_MIN_COMMENT_CHARS` | `0` | Drop discussion comments shorter than this |

## Who is asking

The CLI and MCP server take the user from `--user` or `HOMEGROWN_USER`. The HTTP API
takes it from the `X-Homegrown-User` header. Create users with
`homegrown add-user <login> [--team <team> ...]`. This is a stand-in for real sign-in;
see [production.md](production.md).
