# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WeRSS is a multi-platform RSS subscription assistant. It scrapes WeChat MP articles and Xueqiu (Snowball) user posts, generating RSS feeds with a web management UI.

- **Backend**: Python 3.13+ with FastAPI (entry point `main.py` → `web:app`)
- **Frontend**: Vue 3 + Vite (source in `web_ui/`, built assets served from `static/`)
- **Database**: SQLAlchemy ORM with SQLite (default), MySQL, or PostgreSQL
- **Browser automation**: Playwright (driver in `driver/`) for scraping and anti-crawler evasion
- **Supported platforms**: WeChat Official Accounts (`source_type: wechat`) and Xueqiu (`source_type: xueqiu`)

## Common Commands

### Backend Development
```bash
# Install dependencies
pip install -r requirements.txt

# Copy and edit configuration
cp config.example.yaml config.yaml

# Run the server with jobs and initialization
python main.py -job True -init True

# Run without jobs (API only)
python main.py
```

### Frontend Development
```bash
cd web_ui
yarn install
yarn dev          # Dev server on port 3000, proxies /api to backend
yarn build        # Outputs to dist/, run build.sh to copy into ../static/
```

**Note**: Do not include frontend build steps in Docker or CI workflows. The maintainer prefers compiled assets be copied to `static/` and committed directly rather than rebuilt in CI (see Dockerfile comment).

### Testing
```bash
# Article parser smoke test
python test_article.py

# Xueqiu API smoke test
python test_xueqiu_api.py

# Template parser unit tests
cd core/lax && python -m unittest test_template_parser.py
```

### Docker
```bash
# Development with auto-reload
docker compose -f compose/docker-compose.dev.yaml up -d --force-recreate

# Production SQLite setup
docker compose -f compose/docker-compose-sqlite.yaml up -d
```

## Architecture

### Application Bootstrap (`main.py`)
- Parses `-job True` and `-init True` CLI flags.
- Initializes the database and auth service (`driver.auth`).
- If `cascade.enabled` and `node_type == child`, starts background threads for cascade sync and task polling.
- Otherwise starts the parent-level `cascade_schedule_service`.
- Starts scheduled job threads if `server.enable_job` is true.
- Starts the FastAPI server via `uvicorn.run("web:app", ...)` with auto-reload watching `apis`, `core`, `driver`, `jobs`, `schemas`, `tools`, `views`, `web_ui`.
- **Windows special case**: `asyncio.WindowsProactorEventLoopPolicy` is set before any other imports in both `main.py` and `web.py`, and uvicorn reload is force-disabled because reload breaks Playwright's subprocess requirements.

### Web Layer (`web.py`)
- Creates the FastAPI app with CORS and a custom `AKMiddleware` that captures `AK-SK` Authorization headers into `request.state.ak_auth`.
- Mounts static directories: `/assets`, `/static`, `/files`.
- All API routes are under `/api` (prefix defined by `API_BASE` in `core.base`).
- SPA fallback: unmatched non-API/non-static routes serve `static/index.html`.

### API Layer (`apis/`)
Each module exports a FastAPI `APIRouter` mounted in `web.py`:
- `auth.py`, `user.py` — login/session management
- `mps.py` — WeChat official account (Feed) CRUD and scraping triggers
- `article.py` — article listing, content fetching, favorites
- `rss.py`, `feed_router` — RSS/Atom feed generation
- `message_task.py` — webhook message tasks
- `cascade.py` — parent-child node task distribution
- `filter_rule.py` — HTML content filtering rules (global and per-MP)
- `export.py` — export to md/docx/pdf/json
- `config_management.py` — runtime config editing
- `xueqiu.py` — Xueqiu user subscription CRUD and timeline triggers

### Authentication Stack
Requests are authenticated in the following priority order (all handled in `core/auth.py` and `apis/auth.py`):
1. **AK-SK** (`Authorization: AK-SK {ak}:{sk}`) — stored in `request.state.ak_auth` by `AKMiddleware` in `web.py`; used for API integrations with granular permissions (read/write/delete/admin).
2. **Cascade node** (`X-Cascade-Api-Key` / `X-Cascade-Api-Secret`) — for parent-child node RPC.
3. **JWT Bearer** (`Authorization: Bearer {token}`) — for web UI sessions; default expiry is 3 days (`token_expire_minutes`).

Login failure lockout: 5 failed attempts locks the account for 30 minutes.

### Multi-Platform Gather Architecture
All platform collectors inherit from `BaseGather` in `core/gather/base.py`. It defines the shared lifecycle:

```
Start(mp_id) → get_Articles() → Over(CallBack)
```

Shared behavior includes article deduplication (`HasGathered` / `RecordAid`), RSS cache clearing, and feed sync-time updates. Subclasses only implement `get_Articles()`.

| Platform | Class | Source file | Mode/Behavior |
|----------|-------|-------------|---------------|
| WeChat | `WxGather` | `core/wx/base.py` | Factory selects `api` / `web` / `app` mode |
| Xueqiu | `XueqiuGather` | `core/xueqiu/base.py` | Delegates to `XueqiuApi.get_timeline_pages()` |

The factory `_create_gather(source_type)` in `jobs/mps.py` instantiates the correct collector. Adding a new platform means: (1) create `core/<platform>/base.py` inheriting `BaseGather`, (2) implement `get_Articles()`, (3) register in `_create_gather()`.

### Core Layer (`core/`)
- `config.py` — `Config` class loads YAML, substitutes `${VAR:-default}` environment variables, and exposes `cfg.get("dotted.key", default)`. **Not thread-safe for writes**; use `cfg.set()` / `save_config()` carefully.
- `db.py` — `Db` class wraps SQLAlchemy engine with connection pooling, SQLite `text_factory` handling for invalid UTF-8, and scoped sessions for thread safety. Global instance is `DB`.
- `models/` — SQLAlchemy declarative models (`Article`, `Feed`, `User`, `MessageTask`, `FilterRule`, `CascadeNode`, etc.).
- `rss.py` — RSS XML generation with configurable title, description, cover, CDATA, pagination.
- `cache.py` / `redis_client.py` — optional Redis caching layer; can also spawn an embedded Redis server (`tools/redis_server.py`).
- `async_utils.py` — `run_async(coro, timeout)` for running async code from sync contexts. **Do not use for Playwright operations** (creates a new thread/loop and deadlocks); use `XueqiuBrowserManager.fetch_sync()` instead.

### Driver Layer (`driver/`)
- `wx.py` — `Wx` class manages WeChat MP login state, QR code generation, and token extraction via Playwright.
- `playwright_driver.py` — `PlaywrightController` wraps Playwright browser lifecycle (launch, context, page, stealth injection, graceful shutdown).
- `wxarticle.py` / `wx_api.py` — article scraping implementations: web page parsing and internal API calls.
- `anti_crawler_*.js` / `anti_crawler_config.py` — stealth/anti-detection scripts injected into Playwright pages.
- `auth.py` — background service that monitors WeChat login state and refreshes tokens.
- `store.py` — `BaseCookieStore` abstract class providing Redis-priority + encrypted-file-fallback cookie persistence.
- `wx_store.py` — `WxCookieStore(BaseCookieStore)` with WeChat-specific cookie filtering.
- `xueqiu_store.py` — `XueqiuCookieStore(BaseCookieStore)` with `.xueqiu.com` domain defaults and auth checks.
- `xueqiu_browser.py` — `XueqiuBrowserManager` singleton that runs Playwright in a dedicated background event-loop thread. Exposes `fetch_sync()` for safe synchronous access (uses `asyncio.run_coroutine_threadsafe`), avoiding cross-event-loop deadlock.

### WeChat Collection Modes (`core/wx/`)
`WxGather` uses a factory to select one of three modes based on `gather.model`:
| Mode | Class | Source file | Behavior |
|------|-------|-------------|----------|
| `api` | `MpsApi` | `core/wx/model/api.py` | HTTP calls to `/cgi-bin/appmsg`; returns **temporary** article links; fastest |
| `web` | `MpsWeb` | `core/wx/model/web.py` | Browser + `/cgi-bin/appmsgpublish`; returns **permanent** publish links |
| `app` | `MpsAppMsg` | `core/wx/model/app.py` | Same Web interface; fetches only the latest messages |

Collection flow: `WxGather.Start(mp_id)` → load Token/Cookie → choose mode → paginate article list → deduplicate (`HasGathered` / `RecordAid`) → optional content fetch → `UpdateArticle` callback → webhook → clear RSS cache.

### Jobs Layer (`jobs/`)
Scheduled tasks run in daemon threads started from `main.py`:
- `mps.py` — periodic feed syncing, article collection, and content auto-check. Uses `_create_gather()` to support both WeChat and Xueqiu feeds.
- `cascade_task_dispatcher.py` — parent: queues and dispatches tasks to child nodes; child: polls parent for tasks and executes them locally.
- `cascade_sync.py` — child node heartbeat and data synchronization back to parent.
- `webhook.py` — sends article notifications to DingTalk/WeChat Work/Feishu/custom webhooks.

### Task Scheduling & Queues
APScheduler (`core/task/`) triggers `MessageTask` definitions on Cron expressions. Two queues are involved:
1. **TaskQueue** (`core/queue/`) — main article-collection queue. `do_job()` runs `WxGather`/`XueqiuGather` → `UpdateArticle` → webhook → `MessageTaskTracker`.
2. **ContentTaskQueue** — background queue that backfills missing article bodies via `sync_article_content()`.

Queue features: Redis-backed persistence, exponential backoff retry (max 3x), deduplication, task timeout protection, and WebSocket status broadcasts via `core/ws_manager.py`.

### Views Layer (`views/`)
Legacy server-rendered page handlers (Jinja2-like templates in `public/templates/`) alongside the Vue SPA.

## Important Conventions

### Database
- Use `DB.get_session()` to obtain a new SQLAlchemy session. The global `DB` instance uses `scoped_session` for thread safety.
- Article IDs are composite: `f"{mp_id}-{original_id}".replace("MP_WXS_", "")`.
- `Article.has_content` flag tracks whether the body was successfully scraped.
- `Feed` has `source_type` (`wechat` | `xueqiu`) and `extinfo` (JSON text for platform-specific metadata).

**Data status enum** (`core/models/base.py` `DATA_STATUS`):
| Value | Meaning |
|-------|---------|
| 1 | ACTIVE |
| 2 | INACTIVE |
| 3 | PENDING |
| 4 | COMPLETED |
| 5 | FAILED |
| 6 | FETCHING |
| 1000 | DELETED |

### Configuration
- `config.yaml` supports environment variable interpolation: `${VAR:-default}`. Secrets should be injected via env vars, not hardcoded.
- `config.example.yaml` is the template; never commit real `config.yaml` or `.env`.

### Browser / Playwright
- `BROWSER_TYPE` env var selects the Playwright browser (`firefox`/`edge`/`webkit`). Default is `firefox`.
- On Linux containers, `start.sh` launches Xvfb when `HEADLESS != true` or `ENABLE_XVFB = true`.
- Playwright browsers are installed via `install.sh` using `playwright install $BROWSER_TYPE --with-deps`.
- **Cross-event-loop safety**: Playwright browser objects are bound to the loop that created them. Never use `core/async_utils.run_async()` for Playwright calls. For Xueqiu, always use `XueqiuBrowserManager.fetch_sync()`.

### Frontend
- `web_ui/vite.config.ts` proxies `/api`, `/rss`, `/feed`, `/views`, `/static`, `/files`, `/proxy` to the backend target defined by `VITE_PROXY_TARGET` or `VITE_API_BASE_URL`.
- After building, run `web_ui/build.sh` to copy `dist/` into `static/` so the FastAPI static file mount serves them.
- Shared form-submission logic (validate → loading → Message → redirect) is abstracted in `web_ui/src/composables/useSubscriptionSubmit.ts`.

**Route permissions** (enforced in `web_ui/src/router/`):
| Route | Required Permission |
|-------|-------------------|
| `/wechat/mp`, `/wechat-status`, `/filter-rules` | `wechat:manage` |
| `/message-tasks*` | `message_task:view` / `message_task:edit` |
| `/tags` | `tag:view` |
| `/task-queue`, `/sys-info`, `/access-keys`, `/cascade*`, `/env-exception` | `admin` |
| `/configs`, `/export/records` | `config:view` |

### Cascade (Parent-Child Nodes)
- Enabled via `cascade.enabled: True`. `node_type` is either `parent` or `child`.
- **Parent side**: `CascadeScheduleService` (cron trigger) → `CascadeTaskDispatcher` splits tasks by Feed and pushes to `CascadeTaskAllocation`. Parent also exposes endpoints for child polling.
- **Child side**: `cascade_sync_service` sends heartbeats (60s) and pulls Feed/Task config from parent; `start_child_task_worker` polls for tasks every 30s (`task_poll_interval`), claims tasks atomically via DB locking, executes scraping, then uploads article data back to parent.
- Node health timeout: 3 minutes without heartbeat marks the node offline.
- Cascade auth uses `api_key` + `api_secret` headers.

## Contributing Conventions
(from `AGENTS.md`)
- Use Angular-style commits: `feat:`, `fix:`, `refactor:`, etc. Keep the body as `-` prefixed lines without blank lines.
- Create fresh working branches; for Codex-assisted work use `codex/YYYY-MM-DD`.
- Keep `origin` pointed at your fork and `upstream` pointed at `https://github.com/rachelos/we-mp-rss`. Before pushing or opening a PR, fetch upstream and merge the latest `upstream/main`.

## Security Notes
- Do not commit credentials, cookies, `config.yaml`, or `data/` contents.
- Review `SECURITY.md` before modifying auth, webhooks, or access-key flows.
- For deployments where WeChat blocks datacenter IPs, use the `compose/singbox` sidecar and set a single `PROXY_URL=` in `.env` rather than modifying host proxy settings.
