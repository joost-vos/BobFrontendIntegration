# AGENTS.md — Agent mode rules

This file provides guidance to agents when working with code in this repository.

## Do not touch

- Everything outside the four TODO functions (`get_wxo_token`, `start_wxo_run`, `poll_wxo_run`, `run_wxo_agent`) is considered stable. The docstring at the top of `app_bob_v2.py` says so explicitly.
- `newsletter-companion-v2.html` is a single self-contained file — no build step, no bundler. Edit it directly with targeted diffs only.

## Required async pattern — breaking this stalls the server

```python
# CORRECT
async with httpx.AsyncClient() as client:
    r = await client.post(url, ...)
await asyncio.sleep(POLL_INTERVAL)

# WRONG — freezes entire uvicorn event loop
r = httpx.post(url, ...)
time.sleep(POLL_INTERVAL)
```

Every route handler and every function it calls must be `async def`.

## Reuse existing helpers — do not reimplement

- `poll_thread_for_newsletter(token, thread_id)` — reads finished reply from thread messages
- `get_run_events_text(token, run_id)` — fallback when no thread_id
- `parse_fetch_result(raw_text)` — normalises JSON / markdown table agent output
- `_parse_keyword_list(text)` — three-strategy parser for topic-agent output (keyword_list var → numbered list → comma fallback)
- `_headers(token)` — builds `Authorization: Bearer` + `Content-Type` dict

## 422 retry on start_wxo_run

The wxO runs endpoint occasionally rejects a list-valued `content` field with 422. Retry once with `content` set to the plain prompt string. This is a documented quirk — keep the retry logic when editing `start_wxo_run`.

## Frontend state

`window._pipelineCtx` holds the full setup context across stages. The JS renames `tech` → `audience` when building the JSON body for both API calls. Do not rename without updating both sides.

## Validation command

`venv/bin/python -m py_compile app_bob_v2.py && echo OK` — run after every backend change.
