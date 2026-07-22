# AGENTS.md — Ask mode rules

This file provides guidance to agents when working with code in this repository.

## What lives where

- `app_bob_v2.py` — entire backend, all logic, one file. Seven numbered sections separated by `# ═══ N. Title ═══` banners. Jump by searching the banner text.
- `newsletter-companion-v2.html` — entire frontend, one file. All HTML, CSS, and JS inline.
- `LAB_GUIDE.md` — canonical description of the four TODO functions and the wxO API contract. Read this before answering questions about what the functions should do.
- `.env` — gitignored, not readable by tools. Refer to the env var names defined in Section 1 of `app_bob_v2.py`.

## Non-obvious architecture facts

- The frontend calls only `localhost:8080`. The backend holds all secrets and proxies to IBM wxO — the browser never touches wxO directly.
- There are **four** distinct wxO agents. Three are used in the fetch pipeline (topic expansion → news fetch → curation). One (the Flow agent) is used only for writing. They share `run_wxo_agent()` via an `agent_id` parameter — the default is `FLOW_AGENT_ID`.
- wxO runs are async: a POST starts a run, then you poll GET until `status == completed`. A single long-poll HTTP call is not possible.
- The IAM bearer token is cached in a module-level dict `_token_cache`. It is not per-request; it is shared across all concurrent requests until 60s before expiry.
- `poll_thread_for_newsletter` and `get_run_events_text` are two different strategies for extracting the agent's reply — threads are preferred, events are the fallback.

## Known gaps (do not describe as bugs unless asked to fix)

- `tone`, `length`, `audience` are forwarded to `/pipeline/fetch` but never included in the topic/fetch/curation prompts. Only `build_write_prompt` uses them.
- The `length` selection does not affect the article cap in `/pipeline/fetch` — it always uses `MAX_ARTICLES = 10`.
