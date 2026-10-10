# Perfect Engine — Backend

The Perfect Engine is an orchestration layer. It stores **project configuration only**: LLM prompts, LLM settings, and node definitions. It executes nodes in JavaScript, Python, or HTTP runtimes and routes chat turns through them.

Business data never lives here. A project that needs data (doctors, tokens, orders) calls its own business API through an `http` node and reads connection values from server environment variables.

## Layout

```
AI-ENGINE-BACKEND/
├── app/
│   ├── main.py              # FastAPI application and lifespan
│   ├── config.py            # Environment-driven configuration
│   ├── schemas.py           # Request/response models
│   ├── services.py          # Projects, nodes, chat orchestration
│   ├── services_llm_providers.py
│   ├── database.py          # MongoDB access
│   ├── auth.py / core/security.py
│   ├── api/routes/          # HTTP routes
│   ├── core/                # LLM catalog, tool router, secrets, rate limit, metrics
│   └── runtime/             # JavaScript pool, Python worker, HTTP executor
├── llm/                     # LiteLLM provider adapter
├── scripts/                 # Operational scripts
├── tests/                   # pytest suite
├── examples/                # Sample project packages (configuration only)
├── env.example              # Environment template
├── Dockerfile
├── requirements.txt
└── start.py                 # Interactive launcher
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy env.example .env
```

Edit `.env`. At minimum set `MONGODB_URL`, `DATABASE_NAME`, and an LLM provider key. See the table below.

## Run

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Or use the interactive launcher:

```powershell
python start.py
```

Health check: `GET /health`. OpenAPI docs: `http://localhost:<port>/docs`.

The Dockerfile and `start.py` default to port 8000. Pick any free port and use it consistently in clients.

## Environment

| Variable | Purpose |
|----------|---------|
| `MONGODB_URL` | Engine database connection |
| `DATABASE_NAME` | Engine database name |
| `LLM_PROVIDER` | `litellm` (default) or `openai` |
| `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN` | Provider keys (set the ones you use) |
| `LLM_KEY_ENCRYPTION_KEY` | Encrypts provider keys stored in the database (falls back to `JWT_SECRET_KEY`) |
| `AUTH_ENABLED`, `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` | Authentication |
| `DEFAULT_ADMIN_EMAIL`, `DEFAULT_ADMIN_PASSWORD`, `DEFAULT_ADMIN_NAME` | Bootstrap admin user |
| `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` | Request rate limiting |
| `REDIS_URL` | Shared orchestration backend (in-memory when unset) |
| `NODE_RUNTIME_POOL_SIZE`, `PYTHON_RUNTIME_POOL_SIZE`, `RUNTIME_IDLE_TTL_SECONDS` | Runtime worker pools |
| `NODE_EXECUTION_TIMEOUT_MS`, `NODE_EXECUTION_MAX_RETRIES`, `NODE_EXECUTION_CACHE_TTL_SECONDS` | Node execution behavior |
| `LITELLM_TIMEOUT_SECONDS`, `LITELLM_MAX_RETRIES`, `LITELLM_NUM_FALLBACKS`, `LITELLM_CACHE_TTL_SECONDS` | LLM gateway behavior |
| `CONVERSATION_MESSAGE_LIMIT` | Messages kept per conversation |
| `ALLOWED_ORIGINS` | CORS origins (`*` in development only) |
| `ENVIRONMENT`, `HOST`, `PORT`, `RELEASE_VERSION` | Server settings |

In production, `app/config.py` refuses to boot unless `AUTH_ENABLED`, `JWT_SECRET_KEY`, `REDIS_URL`, and `ALLOWED_ORIGINS` are set appropriately.

## Business connectors (HTTP nodes)

An `http` node calls an external business API. Its URL, headers, and body can reference:

- `{{env:VAR}}`: resolved from the engine's server environment. Use this for base URLs and API keys. A missing variable fails the call closed.
- `{{input_name}}`: filled from the node's inputs. Path segments are URL-encoded. Body or query fields whose value is not supplied are omitted.

Example node configuration:

```json
{
  "method": "GET",
  "url": "{{env:HOSPITAL_API_BASE_URL}}/doctors/{{doctor_id}}/schedule",
  "headers": { "X-API-Key": "{{env:HOSPITAL_API_KEY}}" },
  "body_template": { "date": "{{date}}" }
}
```

The engine stores this configuration only. The connector's database schema, rows, and business rules belong to the business project.

## Main endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness and runtime status |
| POST | `/api/auth/login`, `/api/auth/register` | Accounts (when auth is enabled) |
| GET/POST | `/api/projects` | List or create projects |
| GET/PUT/DELETE | `/api/projects/{project_api_id}` | Read, update, or delete a project |
| GET | `/api/projects/{project_api_id}/export` | Export a project package |
| GET | `/api/projects/{project_api_id}/nodes` | List nodes |
| POST | `/api/nodes` | Create a node |
| PUT/DELETE | `/api/nodes/{node_id}` | Update or delete a node |
| POST | `/api/execute-node` | Run one node with given inputs |
| POST | `/api/chat` | Chat turn through a project's LLM and nodes |
| GET | `/api/llm-providers`, `/api/llm-providers/catalog` | LLM provider management |
| GET | `/api/stats`, `/metrics` | Metrics |

Full, interactive reference: `/docs`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

`tests/simple_test.py` is a manual script that needs a running server on port 8000. It times out when no server is running on that port.

## Docker

```powershell
docker build -t perfect-engine-backend .
docker run -p 8000:8000 --env-file .env perfect-engine-backend
```

## Notes

- `services.py` is large (about 2,800 lines) and is the next candidate for splitting by responsibility.
- Historical build reports and the previous README are archived outside the repository.
