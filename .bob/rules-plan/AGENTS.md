# AGENTS.md — Plan mode rules

This file provides guidance to agents when working with code in this repository.

## Architectural constraints

- **Single-process, single-file backend.** No workers, no queue, no database. All state is in-memory (token cache, article parsing). Concurrent requests during a fetch (which takes 30-90s) share the same process — design changes must not introduce shared mutable state outside `_token_cache`.
- **No build pipeline.** Changes to `newsletter-companion-v2.html` are live immediately on save. Changes to `app_bob_v2.py` trigger uvicorn auto-reload (`--reload` flag). No compilation, no transpilation.
- **All wxO calls must stay async.** The entire server runs on a single asyncio event loop. Any blocking I/O (synchronous httpx, time.sleep) stalls ALL concurrent requests.

## Sequence of agent calls in /pipeline/fetch

```
TOPIC_AGENT_ID  →  keyword_list (comma-separated text)
FETCH_AGENT_ID  →  raw_news_text (markdown table or JSON)
CURATION_AGENT  →  curated_text (scored/filtered articles)
```

Each call is sequential, not parallel. Parallelising them is not possible — each call's output is the next call's input.

## wxO reply extraction — two paths, same entry point

`poll_wxo_run` dispatches to one of two strategies:
1. `poll_thread_for_newsletter` — polls thread messages, skips placeholder/async messages
2. `get_run_events_text` — scans run events for `message.completed` or `message.delta`

Both are called from `poll_wxo_run` based on whether a `thread_id` is present. Any plan that changes reply extraction must account for both paths.

## Agent response formats are unpredictable

Agents can return plain text, JSON strings, nested JSON, or markdown tables. `parse_fetch_result()` handles all four shapes. `_parse_keyword_list()` has three fallback strategies. Plans that assume a fixed response format will be fragile.

## Frontend stages

7 numbered stages (pages). `goStage(n)` switches between them. `window._pipelineCtx` is the only cross-stage state. Plans adding a new stage must wire `goStage` calls in both directions and handle the back-navigation case.
