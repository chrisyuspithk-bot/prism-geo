# Repo notes for agents

## What this is
`prism` — a from-scratch FastAPI rewrite of the GEO/AI-visibility-tracking
concept from elmohq/elmo (not a fork). Tracks how answer engines mention/cite
a brand across tracked prompts.

**Multi-tenant**: prism is the GEO provider — one deployment hosts many clients.
A *tenant* is one client workspace = the tracked brand + its competitors + its
prompt set. Everything (brands, prompts, runs, and via runs the mentions and
citations) is scoped by `tenant_id`. Engine API keys are global — set once by
the operator at /settings/keys and shared across all clients. The sidebar has a
client switcher; `app._tenant(request)` resolves the active tenant from
`?tenant=` or the `prism_tenant` cookie and is threaded through every query.

## Run
- Serve: `python -m uvicorn prism.app:app --host 0.0.0.0 --port 12000`
- Tests: `python -m pytest tests/`
- Live runs need an engine key (set at /settings/keys, or `DEEPSEEK_API_KEY` etc.
  as env fallback). The old synthetic Nike seed (`prism/seed.py`) was removed —
  the app runs on real data only.

## Gotchas learned
- Starlette >=1.0: `templates.TemplateResponse(request, "name.html",
  context={...})` — request is the first positional arg; the old
  `(name, context)` order raises "missing 1 required positional argument: 'name'".
- `sqlite3.Row` is NOT JSON-serializable and unhashable for Jinja's template
  cache — always convert to `dict` in the read layer (`queries._dicts`) before
  handing data to templates or `JSONResponse`.
- sqlite FK constraints are on (`PRAGMA foreign_keys = ON` in db.connect), so
  tests inserting runs must seed a prompt row first.
- Citations dedup key canonicalizes URLs (lowercase, strip www.) — keep that
  or the same page counts multiple times.
- Tenancy: every `queries.*` read and every write takes a `tenant_id`.
  `run_prompt` resolves it from the prompt's own `tenant_id`. `tenants.owner_id`
  is scaffolding for per-user auth, to be added. Migration note: SQLite can't
  `ALTER TABLE ... ADD COLUMN ... REFERENCES` with a non-NULL default, so the
  migrated `tenant_id` columns carry no FK (new tables from `SCHEMA` do), and
  `idx_runs_tenant` is created in `_migrate()` after the column exists.

## Keys & background jobs
- Engine keys live in the global `settings` table (set at /settings/keys) and
  take priority over env vars. The set is the major answer engines (ChatGPT,
  Claude, Gemini, Perplexity, DeepSeek) plus a generic custom OpenAI-compatible
  slot. `keystore.active_engines()` returns every engine that has both a key and
  its `enabled` toggle on — evaluations fan out prompt × engine, so cross-engine
  visibility is real, not just one engine. Keys are operator-level: one set,
  shared by all tenants.
- Runs are recorded under the engine's display name (ChatGPT, Claude, …) so
  per-model dashboard filters stay consistent.
- Evaluations run as background jobs (`jobs` table + one asyncio worker started
  on FastAPI startup via `jobs.ensure_worker()`). POST /run-all and
  /prompts/{id}/run just insert a job row and 303 to /jobs/{id} — never block
  the request. Progress is polled from /api/jobs/{id}; cancel is cooperative.
- schema changes: add to `SCHEMA` for fresh DBs AND to `_migrate()` for existing
  ones (both run on every startup).

## SQLite + async worker (hard-won)
- NEVER hold a `connect()` connection across an `await` (e.g. an engine HTTP
  call). It keeps a SQLite write transaction open for the whole await; N
  concurrent coroutines then block on that one lock and the worker deadlocks at
  ~CONCURRENCY runs. Read in one short `connect()`, do the network call with NO
  connection held, write in a second short `connect()`. (This was the run-all
  hang — `run_prompt` was restructured for exactly this.)
- `connect()` sets `journal_mode=WAL`, `busy_timeout`, and retries on
  SQLITE_BUSY. Readers + the single writer can proceed concurrently; a burst of
  writers still serializes, so the retry is what absorbs it. Keep WAL on.
- `jobs.recover_stale_jobs()` runs at startup to fail any job left
  queued/running by a previous crash (single-process worker → nothing can
  still be running after a restart).
