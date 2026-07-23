"""
Newsletter Companion v2 — Backend Server  (WORKSHOP LAB STUB)
IBM Women & Tech NL Workshop, July 3

This is the participant version. Everything works except four functions,
marked TODO below, that handle talking to the watsonx Orchestrate Flow agent:

  1. get_wxo_token()   — exchange the API key for a bearer token (with caching)
  2. start_wxo_run()   — kick off a run with an agent, sending it your prompt
  3. poll_wxo_run()    — wait for the run to finish and get the reply
  4. run_wxo_agent()   — wire the two above together into one call

Once those four work, BOTH endpoints below start working at once — /pipeline/fetch
(keyword expansion, news fetch, curation) and /pipeline/write (writing the
newsletter from the articles you selected) — since both routes call
run_wxo_agent() under the hood. Everything else in this file (the HTML frontend,
article table parsing, time-window filtering, prompt builders, routes) is
already done. Don't touch it.

There's a fully working reference implementation of this same file — ask your
facilitator if your team is stuck. Try to get there with Bob first.

Endpoints
---------
GET  /               Serves the v2 HTML frontend
GET  /health         Liveness check
POST /pipeline/fetch Returns curated article candidates for human selection
POST /pipeline/write Generates the newsletter from selected articles

Run
---
    source venv/bin/activate
    orchestrate env activate workshop
    uvicorn app_bob_v2:app --port 8080 --reload

File layout (search for these headers to jump around)
-------------------------------------------------------
    1. Config & models
    2. wxO auth + HTTP helpers          <- TODO: get_wxo_token()
    3. Parsing agent responses (text/JSON extraction)
    4. wxO run orchestration            <- TODO: start_wxo_run(), poll_wxo_run(), run_wxo_agent()
    5. Prompt builders
    6. Article parsing & normalization (tables, dates, dedup)
    7. Routes
"""

import os
import re
import time
import asyncio
import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from typing import Literal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("newsletter-v2")

app = FastAPI(title="Newsletter Companion Backend v2 — Workshop Lab")
# C-1: Restrict CORS to the local dev origin only. Set ALLOWED_ORIGIN in .env for production.
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "http://localhost:8080")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ═══ 1. Config & models ═══════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────────────────────────
# PARTICIPANTS: fill in ALL SIX values below before running the server.
# See the lab guide (Step 1) for exactly how to find each one.
#
#   WXO_BASE          — your wxO service instance URL.
#                       Format: https://api.<region>.watson-orchestrate.cloud.ibm.com/instances/<id>
#                       Run: orchestrate env list  (shows the URL next to your environment name)
#
#   FLOW_AGENT_ID     — ID of the newsletter-writer Flow agent.
#   TOPIC_AGENT_ID    — ID of the topic-expansion agent.
#   FETCH_AGENT_ID    — ID of the Google News fetch agent.
#   CURATION_AGENT_ID — ID of the curation / relevance-scoring agent.
#                       Run: orchestrate agents list  (shows name + ID for every agent)
#
#   API_KEY           — your IBM Cloud API key (used to get a bearer token from IAM).
#                       Get it from cloud.ibm.com > Manage > Access (IAM) > API keys.
# ──────────────────────────────────────────────────────────────────────────────

WXO_BASE          = os.environ["WXO_BASE"]           # set in .env
FLOW_AGENT_ID     = os.environ["FLOW_AGENT_ID"]      # set in .env
TOPIC_AGENT_ID    = os.environ["TOPIC_AGENT_ID"]     # set in .env
FETCH_AGENT_ID    = os.environ["FETCH_AGENT_ID"]     # set in .env
CURATION_AGENT_ID = os.environ["CURATION_AGENT_ID"]  # set in .env
API_KEY           = os.environ["API_KEY"]            # set in .env
IAM_URL           = "https://iam.cloud.ibm.com/identity/token"

SMTP_HOST     = os.environ.get("SMTP_HOST", "smtp.hostinger.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "465"))
SMTP_FROM     = os.environ.get("SMTP_FROM", "orchestrate@txt-insight.com")
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

POLL_TIMEOUT = 180   # seconds to wait for a wxO run to finish
POLL_INTERVAL = 4    # seconds between polls
MAX_ARTICLES = 10    # hard cap on articles returned/used, regardless of agent output

TIME_WINDOW_DAYS = {
    "last 7 days": 7,
    "last 30 days": 30,
    "last 3 months": 90,
    "last year": 365,
}

_token_cache = {"value": None, "exp": 0}
_token_lock = asyncio.Lock()   # H-3: prevent concurrent IAM refresh races

_VALID_TONE      = ("Conversational", "Neutral", "Concise", "Formal")
_VALID_AUDIENCE  = ("Broad audience", "Working knowledge", "Technical / expert")
_VALID_WINDOW    = ("Last 7 days", "Last 30 days", "Last 3 months", "Last year")
_VALID_LENGTH    = ("Short", "Medium", "Long")
# H-2: simple email pattern — rejects newlines (header injection) and obvious non-addresses
_EMAIL_RE = re.compile(r'^[^@\s\r\n]+@[^@\s\r\n]+\.[^@\s\r\n]+$')


# H-1: Literal types + validators on all enum fields; intent is capped at 500 chars
class PipelineFetchRequest(BaseModel):
    intent: str
    keywords: list[str] = []
    tone: Literal["Conversational", "Neutral", "Concise", "Formal"] = "Conversational"
    audience: Literal["Broad audience", "Working knowledge", "Technical / expert"] = "Working knowledge"
    timeWindow: Literal["Last 7 days", "Last 30 days", "Last 3 months", "Last year"] = "Last 7 days"
    length: Literal["Short", "Medium", "Long"] = "Short"

    @field_validator("intent")
    @classmethod
    def intent_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("intent must not be empty")
        return v[:500]

    @field_validator("keywords", mode="before")
    @classmethod
    def cap_keywords(cls, v):
        return [str(k)[:100] for k in (v or [])[:20]]


class ArticleSelection(BaseModel):
    title: str
    source: str = ""
    date: str = ""
    snippet: str = ""
    relevance: str = "med"
    url: str = ""


class PipelineWriteRequest(BaseModel):
    intent: str
    keywords: list[str] = []
    tone: Literal["Conversational", "Neutral", "Concise", "Formal"] = "Conversational"
    audience: Literal["Broad audience", "Working knowledge", "Technical / expert"] = "Working knowledge"
    timeWindow: Literal["Last 7 days", "Last 30 days", "Last 3 months", "Last year"] = "Last 7 days"
    length: Literal["Short", "Medium", "Long"] = "Short"
    articles: list[ArticleSelection] = []

    @field_validator("intent")
    @classmethod
    def intent_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("intent must not be empty")
        return v[:500]

    @field_validator("keywords", mode="before")
    @classmethod
    def cap_keywords(cls, v):
        return [str(k)[:100] for k in (v or [])[:20]]


# ═══ 2. wxO auth + HTTP helpers ════════════════════════════════════════════════

async def get_wxo_token() -> str:
    """
    TODO: implement this.

    IBM Cloud doesn't accept the API key directly on wxO calls — exchange it
    for a short-lived bearer token first.

    The caching check below is already done for you (skip calling IAM if we
    already have a token that hasn't expired). You need to fill in the part
    that actually gets a fresh token when the cache is empty or stale:

      POST to IAM_URL, as x-www-form-urlencoded, with:
        grant_type = urn:ibm:params:oauth:grant-type:apikey
        apikey     = API_KEY

      The JSON response has "access_token" and "expires_in" fields. Store
      both in _token_cache (see the shape used in the read path above) so
      the next call can reuse it, then return the token.
    """
    # Fast path — no lock needed when cache is warm
    if _token_cache["value"] and time.time() < _token_cache["exp"]:
        return _token_cache["value"]

    # H-3: serialize concurrent refreshes so only one IAM call fires
    async with _token_lock:
        # Re-check inside the lock — another coroutine may have refreshed already
        if _token_cache["value"] and time.time() < _token_cache["exp"]:
            return _token_cache["value"]

        async with httpx.AsyncClient() as client:
            r = await client.post(
                IAM_URL,
                data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": API_KEY},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        r.raise_for_status()
        body = r.json()
        _token_cache["value"] = body["access_token"]
        _token_cache["exp"] = time.time() + body["expires_in"] - 60   # 60 s safety margin
        return _token_cache["value"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ═══ 3. Parsing agent responses ════════════════════════════════════════════════
# wxO can hand back plain text, nested JSON, or SSE-style events depending on the
# agent and whether the call was sync/async. These helpers dig the actual text or
# JSON payload out of whatever shape comes back.

def _json_if_possible(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return x
    return x


def _extract_json_object(text: str):
    """Find the first balanced {...} object anywhere in a string."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break
        start = text.find("{", start + 1)
    return None


def _extract_text_from_content(content) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                for key in ("text", "value", "content"):
                    val = block.get(key)
                    if isinstance(val, str) and val.strip():
                        parts.append(val.strip())
        return "\n".join(parts).strip()

    if isinstance(content, dict):
        for key in ("text", "value", "content", "message"):
            val = content.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    return ""


def _collect_texts(obj) -> list[str]:
    """Recursively pull every plausible text field out of an event/message payload."""
    obj = _json_if_possible(obj)

    if isinstance(obj, str):
        s = obj.strip()
        return [s] if s else []

    if isinstance(obj, list):
        out = []
        for item in obj:
            out.extend(_collect_texts(item))
        return out

    if isinstance(obj, dict):
        out = []
        for key in ("text", "delta", "value", "output", "response", "answer", "generated_text"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
            elif isinstance(val, (dict, list)):
                out.extend(_collect_texts(val))
        for key in ("content", "message", "result", "data"):
            if key in obj:
                out.extend(_collect_texts(obj[key]))
        return out

    return []


def _looks_like_original_prompt(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith("create newsletter\nkeyword:") or t.startswith("create newsletter keyword:")


def _is_system_noise(text: str) -> bool:
    t = text.strip().lower()
    return any(p in t for p in (
        "a new flow has started",
        "this chat session is currently dedicated to the flow",
        "will resume once the flow is complete",
    ))


def _is_async_placeholder(msg: dict) -> bool:
    props = msg.get("additional_properties", {}) or {}
    display = props.get("display_properties", {}) or {}
    if display.get("is_async") or display.get("skip_render"):
        return True
    return _is_system_noise(_extract_text_from_content(msg.get("content", "")))


# ═══ 4. wxO run orchestration ══════════════════════════════════════════════════
# Flow: start_wxo_run() kicks off a run -> poll_wxo_run() waits for it to finish
# and pulls the assistant's reply out of the thread (or, failing that, the raw
# run events). run_wxo_agent() is the one-call convenience wrapper routes use.

async def start_wxo_run(token: str, prompt: str, agent_id: str = FLOW_AGENT_ID) -> dict:
    """
    TODO: implement this.

    Starts a new run with a wxO agent and sends it the prompt in the same
    call (there's no separate "create a session" step in this API).

    POST to:  {WXO_BASE}/v1/orchestrate/runs

    Body (already built for you below as `payload`):
        {
          "message": {
            "role": "user",
            "content": [{"id": "1", "response_type": "text", "text": prompt}]
          },
          "agent_id": agent_id
        }

    Known quirk: this instance occasionally returns 422 if "content" is sent
    as a list. If that happens, retry once with content set to the plain
    prompt string instead (payload["message"]["content"] = prompt).

    Return the parsed JSON response. It contains a "run_id" (what you'll
    poll) and usually a "thread_id" (where the finished reply ends up).
    """
    url = f"{WXO_BASE}/v1/orchestrate/runs"
    payload = {
        "message": {
            "role": "user",
            "content": [{"id": "1", "response_type": "text", "text": prompt}],
        },
        "agent_id": agent_id,
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, headers=_headers(token), timeout=60)
        if r.status_code == 422:
            payload["message"]["content"] = prompt
            r = await client.post(url, json=payload, headers=_headers(token), timeout=60)
    log.info("start_wxo_run agent_id=%s status=%s", agent_id, r.status_code)
    log.debug("start_wxo_run body=%.2000s", r.text)   # C-2: body (may contain token) gated to DEBUG
    r.raise_for_status()
    return r.json()


async def _get_thread_messages(token: str, thread_id: str):
    async with httpx.AsyncClient() as client:
        for url in (
            f"{WXO_BASE}/v1/orchestrate/threads/{thread_id}/messages",
            f"{WXO_BASE}/api/v1/threads/{thread_id}/messages",
        ):
            r = await client.get(url, headers=_headers(token), timeout=30)
            log.info("get_thread_messages thread_id=%s status=%s", thread_id, r.status_code)
            log.debug("get_thread_messages body=%.4000s", r.text)   # C-2: body gated to DEBUG
            if r.status_code == 200:
                body = r.json()
                return body.get("messages", body) if isinstance(body, dict) else body
    return None


async def poll_thread_for_newsletter(token: str, thread_id: str) -> str:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        messages = await _get_thread_messages(token, thread_id)
        if messages is None:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        for msg in reversed(messages or []):
            if msg.get("role") != "assistant" or _is_async_placeholder(msg):
                continue
            text = _extract_text_from_content(msg.get("content", ""))
            if text and not _is_system_noise(text) and not _looks_like_original_prompt(text):
                return text

        await asyncio.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Timed out waiting for newsletter in thread {thread_id}")


async def get_run_events(token: str, run_id: str):
    url = f"{WXO_BASE}/v1/orchestrate/runs/{run_id}/events"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=_headers(token), timeout=30)
    log.info("get_run_events run_id=%s status=%s", run_id, r.status_code)
    log.debug("get_run_events body=%.4000s", r.text)   # C-2: body gated to DEBUG
    r.raise_for_status()
    body = r.json()
    return body.get("events", body) if isinstance(body, dict) else body


async def get_run_events_text(token: str, run_id: str) -> str:
    """Fallback for when a run has no thread: pull assistant text straight out of events."""
    for attempt in range(10):
        events = await get_run_events(token, run_id)
        log.info("get_run_events_text attempt=%s run_id=%s event_count=%s", attempt + 1, run_id, len(events or []))

        completed_texts, delta_texts = [], []
        for ev in events or []:
            event_type = ev.get("event") or ev.get("type") or ""
            data = _json_if_possible(ev.get("data", {}))

            role = data.get("role") if isinstance(data, dict) else None
            if isinstance(data, dict) and isinstance(data.get("message"), dict):
                role = data["message"].get("role", role)
            if role == "user":
                continue

            texts = [t for t in _collect_texts(data) if t and not _looks_like_original_prompt(t) and not _is_system_noise(t)]
            if event_type == "message.completed" or event_type in ("run.completed", "summary", "done"):
                completed_texts.extend(texts)
            elif event_type == "message.delta":
                delta_texts.extend(texts)

        if completed_texts:
            return "\n".join(completed_texts).strip()
        if delta_texts:
            return "".join(delta_texts).strip()

        await asyncio.sleep(3)

    raise RuntimeError("Run completed, but no assistant text was found in run events")


async def poll_wxo_run(token: str, run_id: str, thread_id: str | None = None) -> str:
    """
    TODO: implement this.

    The agent runs its internal pipeline (keyword expansion, news fetch,
    curation, writing) asynchronously, so a single HTTP call can't just wait
    for the result — you need to check back every few seconds.

    Loop until POLL_TIMEOUT seconds have passed:
      1. GET {WXO_BASE}/v1/orchestrate/runs/{run_id}
      2. Look at the "status" field:
           - "completed" or "async_completed" -> the run is done. If you have
             a thread_id, call poll_thread_for_newsletter(token, thread_id)
             (already implemented below) to get the reply. Otherwise fall
             back to get_run_events_text(token, run_id) (also already done).
           - "failed" / "cancelled" / "expired" -> raise an error, don't
             keep polling
           - "requires_input" -> raise an error, this backend expects a
             one-shot run with no follow-up needed
           - anything else (e.g. "running") -> sleep POLL_INTERVAL and
             check again

    If POLL_TIMEOUT is exceeded, raise TimeoutError.
    """
    url = f"{WXO_BASE}/v1/orchestrate/runs/{run_id}"
    deadline = time.time() + POLL_TIMEOUT
    # H-4: create a fresh client per iteration — avoids idle connection expiry
    # across the up-to-180 s poll window.
    while time.time() < deadline:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, headers=_headers(token), timeout=30)
        r.raise_for_status()
        body = r.json()
        status = body.get("status", "")
        log.info("poll_wxo_run run_id=%s status=%s", run_id, status)

        if status in ("completed", "async_completed"):
            if thread_id:
                return await poll_thread_for_newsletter(token, thread_id)
            return await get_run_events_text(token, run_id)

        if status in ("failed", "cancelled", "expired"):
            raise RuntimeError(f"Run {run_id} ended with status '{status}'")

        if status == "requires_input":
            raise RuntimeError(f"Run {run_id} requires additional input — not supported")

        await asyncio.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Timed out after {POLL_TIMEOUT}s waiting for run {run_id}")


async def run_wxo_agent(token: str, prompt: str, agent_id: str = FLOW_AGENT_ID) -> str:
    """
    TODO: implement this — the "wire it all together" step.

    Start a run with start_wxo_run(), then wait for it with poll_wxo_run(),
    and return the finished text. Two lines once the two functions above
    work. Both /pipeline/fetch and /pipeline/write call this, so once it
    works, both endpoints start working at once.
    """
    run = await start_wxo_run(token, prompt, agent_id)
    return await poll_wxo_run(token, run["run_id"], run.get("thread_id"))


# ═══ 5. Prompt builders ════════════════════════════════════════════════════════

def _time_window_days(time_window: str) -> int:
    return TIME_WINDOW_DAYS.get((time_window or "").strip().lower(), 7)


def build_topic_prompt(ctx: dict) -> str:
    intent = ctx.get("intent", "")
    return (
        f"Expand the following topic into 5 related search keywords suitable for a news search.\n"
        f"Topic: {intent}\n"
        "Return only a comma-separated list of keywords, nothing else."
    )


def build_fetch_agent_prompt(keyword_list: list[str], time_window: str) -> str:
    days = _time_window_days(time_window)
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=days)
    return (
        "Fetch Google News articles for these keywords.\n"
        f"keyword_list: {', '.join(keyword_list)}\n"
        f"Use queries, language en-US, maxItems {MAX_ARTICLES}, waitSecs 30.\n"
        f"Today's date is {today.isoformat()}. Only include articles published on or after "
        f"{cutoff.isoformat()} (the last {days} days, matching the '{time_window}' window the user "
        "selected). Exclude anything published before that date, even if it is otherwise relevant.\n"
        "Return the full raw output from the fetch and dataset retrieval steps."
    )


def build_direct_curation_prompt(keyword_list: list[str], news_content: str, time_window: str) -> str:
    days = _time_window_days(time_window)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    return (
        "Please curate these articles.\n"
        f"keyword_list: {', '.join(keyword_list)}\n"
        "threshold_score: 50\n"
        f"Only keep articles published on or after {cutoff} (the '{time_window}' window). "
        "Drop any article older than that, regardless of its relevance score.\n"
        f"news_content: {news_content}"
    )


def build_write_prompt(ctx: dict) -> str:
    article_lines = [
        f"{i}. {a.get('title', '')} | {a.get('source', '')} | {a.get('date', '')}\n   {a.get('snippet', '')}"
        for i, a in enumerate(ctx.get("articles", []), start=1)
    ]
    return (
        "write newsletter from selected articles\n"
        f"topic: {ctx.get('intent', '')}\n"
        f'"tone": "{ctx.get("tone", "Conversational")}", '
        f'"length": "{ctx.get("length", "Short")}",\n'
        f'technical level: {ctx.get("audience", "Working knowledge")}\n'
        "selected articles:\n"
        f"{chr(10).join(article_lines)}\n\n"
        "Formatting rules: write in plain prose only. Do not use markdown "
        "(no **bold**, no #, no tables, no bullet lists), do not use em dashes "
        "or double-hyphen dashes, and do not use emoji or decorative symbols."
    )


# ═══ 6. Article parsing & normalization ════════════════════════════════════════

# Header names an agent's markdown table might use, mapped to the field they mean.
# Parsing the header lets us place values correctly no matter what order the
# agent's table columns come in (instead of assuming a fixed position).
_COLUMN_ALIASES = {
    "date": "date", "pub date": "date", "published": "date", "published_at": "date", "publish date": "date",
    "title": "title", "headline": "title",
    "snippet": "snippet", "summary": "snippet", "description": "snippet",
    "source": "source", "publisher": "source", "outlet": "source",
    "url": "url", "link": "url",
    "relevance": "relevance", "score": "relevance",
}
_HEADER_TOKENS = set(_COLUMN_ALIASES) | {"#", "no."}

_DATE_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %Z",   # Fri, 28 Nov 2025 08:00:00 GMT
    "%Y-%m-%dT%H:%M:%S%z",        # 2026-06-30T08:00:00+00:00
    "%Y-%m-%dT%H:%M:%SZ",         # 2026-06-30T08:00:00Z
    "%Y-%m-%d",                   # 2026-06-30
    "%d %b %Y",                   # 30 Jun 2026
    "%d %B %Y",                   # 30 June 2026
    "%B %d, %Y",                  # June 30, 2026
    "%b %d, %Y",                  # Jun 30, 2026
    "%m/%d/%Y",                   # 06/30/2026
)


def _is_header_leak(title: str, source: str, date: str, snippet: str) -> bool:
    """True if this "article" is actually a markdown table header row that slipped through."""
    values = [v.strip().lower() for v in (title, source, date, snippet) if v and v.strip()]
    return bool(values) and all(v in _HEADER_TOKENS for v in values)


def _normalize_article(article: dict) -> dict:
    log.debug("_normalize_article keys=%s", list(article.keys()))
    return {
        "title": article.get("title") or article.get("headline") or article.get("name") or "Untitled article",
        "source": article.get("source") or article.get("publisher") or article.get("outlet") or "",
        "date": article.get("date") or article.get("published_at") or article.get("published") or "",
        "snippet": (
            article.get("snippet") or article.get("summary") or article.get("description")
            or article.get("body") or article.get("content") or article.get("text")
            or article.get("excerpt") or article.get("abstract") or ""
        ),
        "relevance": str(article.get("relevance") or article.get("score") or "med").lower(),
        "url": article.get("url") or article.get("link") or "",
    }


def _normalize_and_filter(raw_articles: list) -> list[dict]:
    articles = [_normalize_article(a) for a in raw_articles if isinstance(a, dict)]
    return [a for a in articles if not _is_header_leak(a["title"], a["source"], a["date"], a["snippet"])]


def _article_from_row(cells: list[str], column_map: list[str | None] | None) -> dict:
    values: dict[str, str] = {}
    if column_map:
        for i, val in enumerate(cells):
            field = column_map[i] if i < len(column_map) else None
            if field and field not in values:
                values[field] = val.strip()

    # If the header couldn't be matched to known column names, fall back to the
    # conventional [date, title, snippet, source, url] positional order.
    if not values.get("title"):
        padded = list(cells) + [""] * max(0, 5 - len(cells))
        values.setdefault("date", padded[0])
        values.setdefault("title", padded[1])
        values.setdefault("snippet", padded[2])
        values.setdefault("source", padded[3])
        values.setdefault("url", padded[4])

    snippet = values.get("snippet", "")
    if snippet.strip().lower() == "no summary provided":
        snippet = ""

    return {
        "date": values.get("date", ""),
        "title": values.get("title") or "Untitled article",
        "snippet": snippet,
        "source": values.get("source", ""),
        "url": values.get("url", ""),
        "relevance": (values.get("relevance") or "med").lower(),
    }


def _extract_news_results_table(raw_text: str) -> list[dict]:
    """Parse a markdown table of articles, using the header row to map columns
    (rather than assuming a fixed order) and dropping the header itself."""
    rows: list[tuple[list[str], list[str | None] | None]] = []
    block_open = False
    column_map: list[str | None] | None = None

    for line in (l.strip() for l in raw_text.splitlines()):
        is_table_line = bool(line) and line.startswith("|") and line.endswith("|")
        if not is_table_line:
            block_open = False
            column_map = None
            continue
        if set(line.replace("|", "").strip()) == {"-"}:
            continue  # separator row between header and body

        cells = [part.strip() for part in line.strip("|").split("|")]
        if not block_open:
            block_open = True
            column_map = [_COLUMN_ALIASES.get(c.lower()) for c in cells]
            continue
        rows.append((cells, column_map))

    articles = [_article_from_row(cells, cmap) for cells, cmap in rows if len(cells) >= 2]
    return [a for a in articles if not _is_header_leak(a["title"], a["source"], a["date"], a["snippet"])]


def parse_fetch_result(raw_text: str) -> dict:
    """Normalize a fetch/curation agent's reply (JSON object, JSON list, or
    markdown table) into {keyword_list, news_content, curated_articles}."""
    parsed = _json_if_possible(raw_text)
    if isinstance(parsed, str):
        parsed = _extract_json_object(parsed) or parsed

    if isinstance(parsed, list):
        articles = _normalize_and_filter(parsed)
        return {"news_content": articles, "curated_articles": articles}

    if isinstance(parsed, dict):
        news_content = parsed.get("news_content") or parsed.get("articles") or []
        curated_articles = parsed.get("curated_articles") or parsed.get("approved_articles") or news_content
        if isinstance(news_content, dict):
            news_content = news_content.get("articles") or []
        if isinstance(curated_articles, dict):
            curated_articles = curated_articles.get("articles") or []
        if curated_articles:
            log.info("parse_fetch_result: first curated article keys=%s sample=%s",
                     list(curated_articles[0].keys()) if isinstance(curated_articles[0], dict) else "non-dict",
                     str(curated_articles[0])[:300])
        return {
            "keyword_list": parsed.get("keyword_list") or [],
            "news_content": _normalize_and_filter(news_content),
            "curated_articles": _normalize_and_filter(curated_articles),
        }

    articles = _extract_news_results_table(raw_text)
    if articles:
        return {"news_content": articles, "curated_articles": articles}

    raise RuntimeError("Agent did not return fetch-stage JSON")


def _parse_article_date(date_str: str) -> datetime | None:
    """Best-effort parse across the date formats agents/news sources return.
    Returns None when unparseable, so we never guess an article in or out of range."""
    if not date_str or not date_str.strip():
        return None
    s = date_str.strip()

    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)  # last resort: bare YYYY-MM-DD anywhere in the string
    if match:
        try:
            return datetime(*map(int, match.groups()), tzinfo=timezone.utc)
        except ValueError:
            return None

    return None


def _filter_by_time_window(articles: list[dict], time_window: str) -> list[dict]:
    """Drop articles confidently outside the selected window. Unparseable dates are
    kept, since an unverifiable date beats silently discarding a relevant article."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_time_window_days(time_window))
    kept, dropped = [], 0
    for a in articles:
        parsed = _parse_article_date(a.get("date", ""))
        if parsed is not None and parsed < cutoff:
            dropped += 1
            continue
        kept.append(a)
    if dropped:
        log.info("time window filter: dropped %s article(s) older than %s (window=%s)", dropped, cutoff.date(), time_window)
    return kept


# ═══ 7. Routes ══════════════════════════════════════════════════════════════════

@app.get("/")
def index():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "newsletter-companion-v2.html")
    return FileResponse(path, media_type="text/html")


@app.get("/health")
def health():
    return {"status": "ok"}


class ExpandRequest(BaseModel):
    intent: str


def _parse_keyword_list(text: str) -> list[str]:
    """Extract clean keywords from the topic-expansion agent's response.

    The agent may return any of these formats:
      a) keyword_list: kw1, kw2, kw3
      b) 1. kw1\\n2. kw2\\n3. kw3   (numbered list, possibly under a heading)
      c) kw1, kw2, kw3               (bare comma-separated line)

    In all cases we ignore markdown formatting, emoji, heading lines, and
    status messages like "Keywords stored in keyword_list — ready for fetching."
    """
    def _clean(token: str) -> str:
        """Strip markdown bold/italic markers, leading list numbers, and emoji."""
        t = token.strip()
        t = re.sub(r'\*+', '', t)           # remove ** bold markers
        t = re.sub(r'^\d+[\.\)]\s*', '', t) # remove leading "1." or "1)"
        t = re.sub(r'[^\x00-\x7F]', '', t)  # remove non-ASCII (emoji, checkmarks)
        t = t.strip(' -–—:')
        return t

    def _is_noise(token: str) -> bool:
        """Return True for heading lines, status lines, and empty strings."""
        t = token.lower()
        return (
            not token
            or 'keyword' in t and ('stored' in t or 'expanded' in t or 'ready' in t or 'fetching' in t)
            or t.startswith('here are')
            or t.startswith('below are')
            or t.startswith('the following')
        )

    # ── Strategy 1: value on a "keyword_list: ..." line ──────────────────────
    m = re.search(r'keyword[_ ]list\s*[:\-]\s*(.+)', text, re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        # Cut off at any sentence boundary followed by a capital (status suffix)
        raw = re.split(r'\.\s+[A-Z]', raw)[0]
        candidates = [_clean(k) for k in raw.split(",")]
        keywords = [k for k in candidates if k and not _is_noise(k)]
        if keywords:
            log.info("_parse_keyword_list: strategy 1 (keyword_list var) → %s keywords", len(keywords))
            return keywords

    # ── Strategy 2: numbered list items ─────────────────────────────────────
    numbered = re.findall(r'^\s*\d+[\.\)]\s*(.+)', text, re.MULTILINE)
    if numbered:
        keywords = [k for k in (_clean(k) for k in numbered) if k and not _is_noise(k)]
        if keywords:
            log.info("_parse_keyword_list: strategy 2 (numbered list) → %s keywords", len(keywords))
            return keywords

    # ── Strategy 3: comma-separated, skip noise lines ────────────────────────
    log.info("_parse_keyword_list: strategy 3 (comma fallback)")
    keywords = []
    for line in text.splitlines():
        line = line.strip()
        if not line or _is_noise(line):
            continue
        parts = [_clean(k) for k in line.split(",")]
        keywords.extend(k for k in parts if k and not _is_noise(k))
    return keywords


@app.post("/expand-keywords")
async def expand_keywords(req: ExpandRequest):
    try:
        token = await get_wxo_token()
        text = await run_wxo_agent(token, build_topic_prompt({"intent": req.intent}), agent_id=TOPIC_AGENT_ID)
        keywords = _parse_keyword_list(text)
        return {"keywords": keywords}
    except Exception as e:
        raise HTTPException(502, f"expand-keywords: {e}")


class SendRequest(BaseModel):
    to: list[str]           # recipient addresses
    subject: str
    html_body: str
    text_body: str = ""

    # H-2: validate addresses — reject malformed and newline-injected values
    @field_validator("to")
    @classmethod
    def validate_addresses(cls, v):
        if not v:
            raise ValueError("at least one recipient required")
        if len(v) > 50:
            raise ValueError("too many recipients (max 50)")
        for addr in v:
            if not _EMAIL_RE.match(addr):
                raise ValueError(f"invalid email address: {addr!r}")
        return v


@app.post("/send-newsletter")
async def send_newsletter(req: SendRequest):
    if not req.to:
        raise HTTPException(400, "At least one recipient address is required")
    if not SMTP_USER or not SMTP_PASSWORD:
        raise HTTPException(503, "SMTP credentials not configured — set SMTP_USER and SMTP_PASSWORD in .env")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = req.subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = ", ".join(req.to)

    # Plain-text fallback first, HTML second (preferred by clients)
    if req.text_body:
        msg.attach(MIMEText(req.text_body, "plain", "utf-8"))
    msg.attach(MIMEText(req.html_body, "html", "utf-8"))

    # C-3: SMTP_SSL is blocking I/O — run it in a thread pool so the event loop
    # is not frozen for the duration of the SMTP handshake + send (up to 30 s).
    msg_str = msg.as_string()

    def _send_blocking():
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, req.to, msg_str)

    try:
        await asyncio.get_event_loop().run_in_executor(None, _send_blocking)
        log.info("send_newsletter: sent to %s", req.to)
        return {"sent_to": req.to}
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(502, "SMTP authentication failed — check SMTP_USER and SMTP_PASSWORD in .env")
    except Exception as e:
        raise HTTPException(502, f"SMTP error: {e}")


@app.post("/pipeline/fetch")
async def pipeline_fetch(req: PipelineFetchRequest):
    ctx = req.model_dump()
    time_window = ctx.get("timeWindow", "Last 7 days")
    try:
        token = await get_wxo_token()

        # Skip keyword expansion when the caller already supplies a keyword list
        # (e.g. user edited tags on stage 3 and clicked Re-fetch).
        if ctx.get("keywords"):
            keyword_list = ctx["keywords"]
            log.info("pipeline/fetch: using provided keyword_list (%d terms), skipping topic agent", len(keyword_list))
        else:
            topic_text = await run_wxo_agent(token, build_topic_prompt(ctx), agent_id=TOPIC_AGENT_ID)
            keyword_list = _parse_keyword_list(topic_text)
            if not keyword_list:
                keyword_list = [ctx.get("intent", "")]

        raw_news_text = await run_wxo_agent(token, build_fetch_agent_prompt(keyword_list, time_window), agent_id=FETCH_AGENT_ID)
        parsed_news = parse_fetch_result(raw_news_text)

        curated_text = await run_wxo_agent(
            token, build_direct_curation_prompt(keyword_list, raw_news_text, time_window), agent_id=CURATION_AGENT_ID
        )
        curated_result = parse_fetch_result(curated_text)
        curated_articles = curated_result.get("curated_articles") or curated_result.get("news_content") or parsed_news.get("news_content", [])

        news_content = _filter_by_time_window(parsed_news.get("news_content", []), time_window)[:MAX_ARTICLES]
        curated_articles = _filter_by_time_window(curated_articles, time_window)[:MAX_ARTICLES]

        return {"keyword_list": keyword_list, "news_content": news_content, "curated_articles": curated_articles}
    except Exception as e:
        raise HTTPException(502, f"fetch: {e}")


@app.post("/pipeline/write")
async def pipeline_write(req: PipelineWriteRequest):
    if not req.articles:
        raise HTTPException(400, "At least one article is required")

    ctx = req.model_dump()
    ctx["articles"] = [a.model_dump() for a in req.articles][:MAX_ARTICLES]

    try:
        token = await get_wxo_token()
        raw_text = await run_wxo_agent(token, build_write_prompt(ctx))
        return {"raw_text": raw_text}
    except TimeoutError as e:
        raise HTTPException(504, str(e))
    except Exception as e:
        raise HTTPException(502, f"write: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)