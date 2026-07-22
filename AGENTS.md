# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

Single-file FastAPI backend (`app_bob_v2.py`) + single-file HTML/JS/CSS frontend (`newsletter-companion-v2.html`). No build step. No test suite. Python 3.11+.

## Run

```bash
source venv/bin/activate
orchestrate env activate workshop   # must re-run in every new terminal session
uvicorn app_bob_v2:app --port 8080 --reload
```

Syntax check only: `venv/bin/python -m py_compile app_bob_v2.py && echo OK`

## Credentials

All secrets live in `.env` (gitignored). Six vars are **required** at startup (`os.environ["KEY"]` — will `KeyError` if missing): `WXO_BASE`, `API_KEY`, `FLOW_AGENT_ID`, `TOPIC_AGENT_ID`, `FETCH_AGENT_ID`, `CURATION_AGENT_ID`. Five SMTP vars use `os.environ.get()` with defaults (optional).

## wxO API — non-obvious quirks

- **Auth**: API key → IAM bearer token via `POST https://iam.cloud.ibm.com/identity/token` with `x-www-form-urlencoded`. Token is cached in `_token_cache` (module-level dict). Never call IAM per-request.
- **Start run**: `POST {WXO_BASE}/v1/orchestrate/runs` with `agent_id` + message body. If the server returns **422**, retry once with `content` as a plain string instead of a list (documented quirk).
- **Poll**: `GET {WXO_BASE}/v1/orchestrate/runs/{run_id}` until `status` is `completed` or `async_completed`. Terminal statuses: `failed`, `cancelled`, `expired`, `requires_input`.
- **Get reply**: prefer `poll_thread_for_newsletter(token, thread_id)` when a `thread_id` exists; fall back to `get_run_events_text(token, run_id)`. Both are already implemented — do not reimplement them.
- **All I/O must be async**: every `httpx` call uses `httpx.AsyncClient` + `await`. Every sleep uses `await asyncio.sleep()`. Using blocking `httpx.get/post` or `time.sleep()` freezes the entire uvicorn event loop.

## Architecture — four agents, called in sequence

`/pipeline/fetch` calls three agents in order: `TOPIC_AGENT_ID` → `FETCH_AGENT_ID` → `CURATION_AGENT_ID`.  
`/pipeline/write` calls `FLOW_AGENT_ID` (the default in `run_wxo_agent`).  
All four share `run_wxo_agent(token, prompt, agent_id)`.

## Known parameter gap

`tone`, `length`, and `audience` are passed to `/pipeline/fetch` by the frontend but are **not forwarded** to the topic/fetch/curation prompt builders — only `build_write_prompt()` uses them. Intentional omission; do not silently add them to fetch prompts without being asked.

## Frontend ↔ backend field name mismatch

The JS sends `audience: ctx.tech` (UI variable is `tech`, JSON key is `audience`). Pydantic models use `audience`. Do not rename either side without updating both.

## Code style

- Module-level `log = logging.getLogger("newsletter-v2")` — use `log.info(...)` not `print()`.
- stdlib imports first, then blank line, then `load_dotenv()`, then third-party imports.
- Section headers use `# ═══ N. Title ═══` banners — maintain them when adding new sections.
- Pydantic v2 (`model_dump()`, not `.dict()`).
- No type annotations on internal helpers — only on public-facing functions.
