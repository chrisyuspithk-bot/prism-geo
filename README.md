# prism — AI visibility tracking (GEO / AEO)

A from-scratch, single-process rewrite of the concept behind
[elmohq/elmo](https://github.com/elmohq/elmo): track how AI answer engines
(ChatGPT, Perplexity, Gemini, …) mention, cite, and rank your brand, so you can
do **Generative Engine Optimization** — the practice of growing your presence
inside AI-generated answers.

Not a fork. Same core loop, ~95% less code:

> **track prompts → parse mentions & citations → measure visibility / share of
> voice → find the prompts and sources worth acting on**

## Why visibility tracking enables GEO

You can't optimize what you can't measure. GEO closes this loop:

1. **Track** — a curated set of buyer-style prompts is run against answer
   engines on a recurring cadence.
2. **Measure** — each response is parsed for *brand mentions* (who shows up,
   in what order) and *citations* (which URLs the answer grounded on).
3. **Act** — get your brand covered on the domains engines actually cite,
   close opportunity prompts where competitors appear and you don't, and watch
   citation churn as a leading indicator.

## Stack

- **FastAPI** + server-rendered Jinja (Tailwind + Chart.js) — no SPA build step
- **SQLite** — zero-infra storage
- **OpenAI-compatible provider** — any chat-completions endpoint (DeepSeek,
  OpenAI, OpenRouter, local vLLM). Elmo uses scrapers for the real consumer
  UIs plus model APIs; prism keeps the API path only.
- Pure-Python analysis core (`extract.py`, `stats.py`) — unit-tested, no IO

## Quick start

```bash
pip install fastapi uvicorn jinja2 httpx pytest

# optional: seed the Nike demo workspace (~1.5k synthetic runs)
python -c "from prism.seed import seed; seed()"

# optional: live engine for the "Run now" button
export PRISM_API_KEY=sk-...            # or DEEPSEEK_API_KEY
export PRISM_API_BASE=https://api.deepseek.com   # default
export PRISM_MODEL=deepseek-chat                 # default

python -m uvicorn prism.app:app --port 12000
```

Pages: **Overview** · **Visibility** (per-prompt scores + trends) ·
**Share of Voice** (brand mention share, avg position) · **Citations**
(top domains/pages, source-type mix, stability score) · **Opportunities**
(prompts where competitors show but you don't, ranked by winnability) ·
prompt detail with full engine responses · JSON API at `/api/*`.

## Tests

```bash
python -m pytest tests/
```

Covers the analysis core (mention ranking, word-boundary matching, citation
dedup/categorization), the stats (visibility, SoV, stability, opportunity
score), and the run-storage pipeline (live extraction into the DB).

## Layout

```
prism/
  app.py        FastAPI routes (HTML + JSON API)
  db.py         SQLite schema + connection helpers
  extract.py    mention/citation extraction from answer text (pure)
  stats.py      visibility / SoV / stability / opportunity math (pure)
  queries.py    SQL aggregations backing each page
  runner.py     live engine query + run persistence pipeline
  seed.py       deterministic Nike demo workspace
  templates/    Jinja pages
tests/          pytest suite for the analysis core
```
