# Lab Guide — Newsletter Companion Backend

---

## What you're building

You're going to build the Python backend that makes an AI-powered newsletter generator actually work end-to-end.

The HTML frontend is already done and running — it walks through five screens: a setup page where you describe your topic, a loading page while articles are fetched, an article-selection page where you pick what to include, a generation page while the newsletter is written, and a review screen where you can read the finished result.

Your job is to make the backend behind it real. The file `app_bob_v2.py` has two API endpoints already wired up — `POST /pipeline/fetch` (fetches and curates articles relevant to your topic) and `POST /pipeline/write` (writes the newsletter from the articles you selected). Both need to talk to **watsonx Orchestrate (wxO)** AI agents. The plumbing to make those calls — authenticating, starting a run, waiting for results — is currently four empty `TODO` functions. Once those four functions work, both endpoints come alive.

**You won't write the code by hand — Bob (your AI coding agent) will write it for you.** Your job is to understand what needs to happen, guide Bob to build it correctly, and review what it writes before approving.

---

## Architecture overview

Here's how the pieces fit together:

```
Browser (HTML frontend)
        │
        │  HTTP  (POST /pipeline/fetch, POST /pipeline/write)
        ▼
Python backend — app_bob_v2.py    (running on localhost:8080)
        │
        │  HTTPS  (REST API calls)
        ▼
watsonx Orchestrate (IBM Cloud)
        │
        ├── Topic agent      → expands your topic into search keywords
        ├── Fetch agent      → pulls recent news articles via Google News
        ├── Curation agent   → scores and filters articles by relevance
        └── Flow agent       → writes the newsletter from selected articles
```

**Why a local Python backend at all — why not call wxO directly from the browser?**
API keys must never go in a browser (they'd be visible to anyone). The backend sits in the middle: the browser calls it over `localhost`, and the backend calls wxO using a server-side secret. This is standard practice for any frontend that needs to call a protected API.

**Why four separate wxO agents?**
Each agent is a specialist. The topic agent is prompted to be good at keyword expansion; the fetch agent knows how to call Google News; the curation agent applies relevance scoring; the flow agent is tuned for newsletter writing. Chaining specialists tends to produce better results than asking one agent to do everything.

---

## What is Bob?

Bob is your AI coding agent. You'll find it as a chat or agent panel in your editor. You type what you want in plain English, Bob reads the file, proposes a set of code changes, and you approve or reject them before they're applied.

If you're not a developer: that's fine, that's the point. You don't need to understand every line Bob writes. Your job is to read its plan before approving, ask it to explain anything unclear, and tell it when something's wrong. Think of it like reviewing a junior engineer's pull request, not writing code yourself.

---

## Why localhost — and why this isn't live

When you run `uvicorn app_bob_v2:app --port 8080`, you're starting a web server **on your own laptop**. `localhost` is just a name that means "this machine" — so `http://localhost:8080` is a website that only exists on your computer, not on the internet.

That's why the app isn't live: there's no public URL, no hosting, no domain name. Anyone else who tried to visit your address would get nothing. To make it live you'd deploy to a cloud platform (like IBM Code Engine or AWS) and point a domain at it. For a workshop that's fine — we're building and testing locally.

---

## Time budget (~45–60 min)

| Min | What you're doing |
|-----|-------------------|
| 0–10 | Environment setup, credentials, and Exercise 0 (read the code) |
| 10–30 | Implement the four functions with Bob |
| 30–45 | Run end-to-end, fix anything that breaks |
| 45–60 | Finished-early tasks, or flag a facilitator if stuck |

**If you're still on setup past minute 15, stop and ask a facilitator.** If you're stuck on the same error for more than 10 minutes during implementation, do the same — there's a working reference implementation they can pull up if needed. Try to get there with Bob first.

---

## Step 1 — Set up watsonx Orchestrate ADK and fill in your credentials

Before writing any code you need six values from your wxO environment. Open `app_bob_v2.py` now and find the `Config & models` section near the top. You'll see six `FILL_IN_...` placeholders — this step walks you through getting every single one of them.

**What you need to fill in:**

| Constant | What it is | How to get it |
|---|---|---|
| `WXO_BASE` | Your wxO service instance URL | IBM Cloud resource page (see 1b) |
| `FLOW_AGENT_ID` | Newsletter-writer Flow agent | `orchestrate agents list` (see 1d) |
| `TOPIC_AGENT_ID` | Topic-expansion agent | `orchestrate agents list` (see 1d) |
| `FETCH_AGENT_ID` | Google News fetch agent | `orchestrate agents list` (see 1d) |
| `CURATION_AGENT_ID` | Curation / scoring agent | `orchestrate agents list` (see 1d) |
| `API_KEY` | IBM Cloud API key | wxO Settings → API details (see 1e) |

Work through 1a → 1f in order. Don't skip to the code until all six are filled in.

---

### 1a. Install the watsonx Orchestrate ADK (if not already installed)

The `orchestrate` CLI is part of the IBM watsonx Orchestrate ADK (Agent Development Kit). Install or upgrade it:

```bash
pip install --upgrade ibm-watsonx-orchestrate
```

Full installation guide: [developer.watson-orchestrate.ibm.com/getting_started/installing](https://developer.watson-orchestrate.ibm.com/getting_started/installing)

Verify it's working:

```bash
orchestrate --version
```

If you get `command not found`, your `PATH` may not include the pip scripts directory. Ask a facilitator.

---

### 1b. Find your WXO_BASE (Service Instance URL)

This is the base URL for all API calls the backend makes. It uniquely identifies your wxO instance.

**Where to find it — IBM Cloud resource page:**

1. Go to [cloud.ibm.com](https://cloud.ibm.com) and log in
2. Open the **Resource list** from the top-left menu
3. Find your watsonx Orchestrate instance (named something like `wxo-xxxxxxxx`) and click it
4. On the **Manage** tab you will see a **Credentials** section with:
   - **API key** — masked by default; click **Show credentials** or **Download** to reveal it
   - **URL** — this is your `WXO_BASE`. It looks like:
     `https://api.eu-de.watson-orchestrate.cloud.ibm.com/instances/a39e51d1-5a4a-478c-b585-...`

Copy the URL value into `WXO_BASE` in `app_bob_v2.py`.

> The region in the URL (`us-south`, `eu-de`, etc.) will match wherever your instance was provisioned. Use whatever URL is shown on your page — don't change the region.

---

### 1c. Register your wxO environment with the CLI

The `orchestrate` CLI also needs to know your instance URL. Register it once with `env add`, then authenticate:

```bash
orchestrate env add -n workshop -u <your-WXO_BASE-url> --type ibm_iam --activate
```

- `-n workshop` — a short label you choose; you'll use this name in subsequent `orchestrate` commands
- `-u` — the same URL you just copied into `WXO_BASE`
- `--type ibm_iam` — tells the CLI to use IBM Cloud IAM for authentication
- `--activate` — activates this environment immediately (saves running a separate `env activate` step)

The CLI will prompt you for your **API key** when you run this command. You'll get the API key in step 1e — you can come back and run this command then if you prefer to do them in order.

> **What this does:** `env add` saves the instance URL under a friendly name so you don't have to type the full URL every time. Think of it like `git remote add origin <url>` — you register it once, then refer to it by name.

---

### 1d. Activate the environment (if you didn't use `--activate` above)

If you didn't include `--activate` in the `env add` command, activate manually:

```bash
orchestrate env activate workshop
```

You'll need to re-run this in every **new terminal session**. It does not persist across shells. If you close your terminal and reopen it, run this again before starting the server.

---

### 1e. Get the agent IDs

With the environment active, list all agents in your instance:

```bash
orchestrate agents list
```

This prints a table with columns including **name**, **type**, and **ID**. The `ID` column is the value you need — it's a UUID that looks like `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.

Find the four agents for this workshop and copy their IDs into `app_bob_v2.py`:

| What to look for in the name column | Constant to fill |
|---|---|
| An agent containing "topic" or "keyword" | `TOPIC_AGENT_ID` |
| An agent containing "fetch" or "news" | `FETCH_AGENT_ID` |
| An agent containing "curate" or "curation" | `CURATION_AGENT_ID` |
| A Flow agent containing "writer" or "newsletter" | `FLOW_AGENT_ID` |

The agent names in your environment may differ slightly — match by purpose. If you're unsure which is which, ask a facilitator.

> **What is a Flow agent?** A Flow agent is a wxO agent that chains other agents or tools in a defined sequence. The newsletter-writer Flow agent runs several steps (generate a draft, format it, review it) as one orchestrated pipeline. The other three are simpler single-step agents.

---

### 1f. Get your API key (for `API_KEY` in the code)

The backend authenticates with IBM Cloud by exchanging an API key for a short-lived bearer token on each run. This is **not** the same as the CLI credential — it's a key scoped to your IBM Cloud account.

**Where to find it — wxO Settings:**

1. Open your watsonx Orchestrate instance (click **Launch watsonx Orchestrate** from the IBM Cloud resource page)
2. Click your **user icon** in the top-right corner → **Settings**
3. Go to the **API details** tab
4. Click **Generate API key** if you don't have one yet, then copy the value
5. The **Service instance URL** on this same page is your `WXO_BASE` (same value as step 1b — useful as a cross-check)

Copy the API key value into `API_KEY` in `app_bob_v2.py`.

> **Keep API keys private.** Never commit them to git, share them in a chat, or leave them in a file you hand off. Today's workshop keys have limited scope, but the habit matters.

---

### 1g. Confirm everything is filled in

Open `app_bob_v2.py`. The top of Section 1 should now look like this — no `FILL_IN_` strings remaining:

```python
WXO_BASE          = "https://api.us-south.watson-orchestrate.cloud.ibm.com/instances/abc123..."
FLOW_AGENT_ID     = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
TOPIC_AGENT_ID    = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
FETCH_AGENT_ID    = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
CURATION_AGENT_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
API_KEY           = "your-api-key-here"
```

If any `FILL_IN_` values remain, the server will start but every request will fail immediately. Fix them now before moving on.

> **Quick cross-check:** `WXO_BASE` should match the URL shown in both the IBM Cloud resource page (step 1b) and the wxO Settings → API details page (step 1f). If they don't match, use the one from IBM Cloud.

---

## Step 2 — Python environment check

Activate the virtual environment in your terminal:

```bash
source venv/bin/activate
```

Your terminal prompt should now start with `(venv)`. Running `ls` should show `app_bob_v2.py` and `newsletter-companion-v2.html` in the current folder. If either of those isn't true, ask for help before continuing.

---

## Exercise 0 — Read the code before touching anything

**Do this before asking Bob to write anything.** This exercise takes about 5 minutes and will make the rest of the lab much smoother — you'll understand what Bob is filling in and why, and you'll be able to catch mistakes.

Open `app_bob_v2.py`. Here's a map of what's in it (search for the section headers to jump around):

| Section | What it does |
|---------|--------------|
| **1. Config & models** | Constants (URLs, agent IDs, API key) and the request/response data shapes (`PipelineFetchRequest`, `PipelineWriteRequest`) |
| **2. wxO auth + HTTP helpers** | The `get_wxo_token()` function (currently TODO) that exchanges your API key for a bearer token |
| **3. Parsing agent responses** | Helpers that dig the actual text or JSON out of whatever shape wxO hands back — you don't need to touch these |
| **4. wxO run orchestration** | The three TODO functions (`start_wxo_run`, `poll_wxo_run`, `run_wxo_agent`) plus the already-implemented thread and event helpers |
| **5. Prompt builders** | Functions that assemble the plain-text prompts sent to each agent |
| **6. Article parsing** | Parses and normalises article tables or JSON, filters by date window |
| **7. Routes** | The two FastAPI endpoints: `/pipeline/fetch` and `/pipeline/write` |

**While you skim, ask yourself these questions** (you can ask Bob to answer them too):

1. In Section 7, `pipeline_fetch` calls `run_wxo_agent` three times with three different agent IDs. What does each call produce? Why three agents instead of one?
2. In Section 7, `pipeline_write` calls `run_wxo_agent` with no explicit agent ID. What does it default to?
3. In Section 2, what is `_token_cache` for? Why not just call IAM on every request?
4. In Section 4, why does `poll_wxo_run` need a loop rather than a single HTTP call?
5. What's the difference between `poll_thread_for_newsletter` and `get_run_events_text`? When would each be used?

You don't need to answer all of these perfectly — they're prompts to get you reading actively. When you're done skimming, move to Exercise 1.

---

## Exercise 1 — Implement the four functions with Bob

Start Bob in **Agent mode**. Use this opening prompt:

```
Open app_bob_v2.py. There are four functions marked TODO — read their
docstrings, understand what each one needs to do, then implement them all
so both /pipeline/fetch and /pipeline/write work end-to-end. Don't touch
anything outside those four functions unless I ask you to.
```

Let Bob read the file, plan, and write the code. **Read its plan before approving** — if something doesn't make sense, ask it to explain before accepting.

### What the four functions actually do

Bob will figure this out from the docstrings, but it helps to know it yourself:

**`get_wxo_token()`** — IBM Cloud doesn't accept API keys directly on wxO calls. You first exchange the key for a short-lived bearer token by POSTing to IBM IAM. The caching logic (skip the call if the token is still valid) is already there — you fill in the actual exchange. Why cache? IBM IAM has rate limits, and tokens are valid for an hour — there's no reason to re-fetch one on every single request.

**`start_wxo_run(token, prompt, agent_id)`** — Starts a new run with a wxO agent and sends it your prompt in the same call — there's no separate "open a session" step. wxO hands back a `run_id` (what you'll check on) and usually a `thread_id` (where the finished reply ends up). This function takes an `agent_id` parameter because the same function is used with all four specialist agents.

**`poll_wxo_run(token, run_id, thread_id)`** — The agent runs its pipeline asynchronously. It can take 30–90 seconds for a full fetch+curation pass. A single HTTP call can't block that long — you poll every few seconds until the run is `completed`, then call the already-implemented helpers to retrieve the reply. Why not use a webhook instead? That would require a public URL; polling works fine from localhost.

**`run_wxo_agent(token, prompt, agent_id)`** — The "wire it together" step. Call `start_wxo_run`, then `poll_wxo_run`, return the text. It's two lines once the two functions above work. Both routes call this — so implementing it unlocks everything at once.

---

## Exercise 2 — Run it end to end

Once Bob has implemented the functions, start the server:

```bash
uvicorn app_bob_v2:app --port 8080 --reload
```

Open **http://localhost:8080**. On the setup page, fill in a topic (e.g. "AI in healthcare") and click **Fetch articles**. You should see a spinner — this is your backend calling wxO three times in sequence. After 30–90 seconds, a page of articles with checkboxes should appear. Select a few and click **Generate newsletter** — another spinner, then the finished newsletter should appear on the review screen.

The `--reload` flag means the server restarts automatically when you save the file, so you can keep editing and re-testing without manually restarting.

---

## Guiding Bob — things to try

Bob works best when you're specific. Here are some things to ask as you go:

**If the server starts but a request returns a 502:**
```
The server is returning a 502. Here's the error from the terminal: [paste it].
What's wrong and how do I fix it?
```

**If you want to understand what Bob wrote:**
```
Explain what poll_wxo_run is doing and why we need a loop instead of a
single HTTP call.
```

**If it works and you want to improve it:**
```
The pipeline gives no feedback while it waits. Can you add basic logging so I
can see what's happening in the terminal while it polls?
```

**If you're curious about the architecture:**
```
Why does fetching articles use three different agent IDs (topic, fetch,
curation) instead of just one? Why not have the browser call wxO directly
instead of going through this server?
```

---

## Troubleshooting

Stuck on any of these for more than a few minutes? Flag a facilitator.

**A 502 whose message names a TODO function** (e.g. `fetch: TODO: implement start_wxo_run`) — expected until that function is implemented. It tells you exactly where to point Bob next.

**`502 auth: ...`** — Token exchange failed. Make sure you ran `orchestrate env activate workshop` in the same terminal as the server.

**`502 fetch: ...` or `502 write: ...` with something other than a TODO message** — the underlying wxO call itself failed. Ask Bob to check it's calling `/v1/orchestrate/runs` (not `/sessions`) and that `agent_id` is being passed through correctly — there are four different agent IDs in play, and `start_wxo_run` needs to use whichever one it was called with, not always `FLOW_AGENT_ID`.

**Something failed partway through polling** — a common cause is reading the reply straight off the run object instead of calling `poll_thread_for_newsletter` once the run is `completed`. That helper is already implemented — `poll_wxo_run` should call it, not reimplement it.

**`504 Timed out`** — The agent took longer than `POLL_TIMEOUT` (180s). Ask Bob to raise it, e.g. to 240.

**Page 3 (select articles) stays on "Fetching…"** — The server returned an error. Open the browser console (F12) and check the network tab for what `/pipeline/fetch` returned.

**`Connection refused`** — The server isn't running. Run `uvicorn app_bob_v2:app --port 8080`.

**`FILL_IN_...` in an error message** — One or more of the six placeholder values in `app_bob_v2.py` haven't been filled in yet. Go back to Step 1 and work through 1b–1g. The server will start fine either way, but every request will fail the moment it tries to use a placeholder value.

---

## What good looks like

✅ `uvicorn app_bob_v2:app --port 8080` starts with no errors  
✅ `GET http://localhost:8080/health` returns `{"status": "ok"}`  
✅ Filling in a topic and clicking **Fetch articles** shows a spinner, then a real list of articles with headlines and sources  
✅ Selecting articles and clicking **Generate newsletter** shows a spinner, then a finished newsletter on the review screen  
✅ The newsletter has real content — headlines, summaries, not placeholder text  

---

## Finished early?

Try changing the newsletter's voice without touching any logic — pick different values from the tone, length, and technical level options on the setup page and see how the output changes.

If you want to go further with code, pick one (or more) of these to add with Bob:

### Surface the time-window filter
The backend already drops articles older than the selected time window (and caps results at 10), but it does this silently — the user never finds out how many articles got filtered out.

```
The backend's _filter_by_time_window function drops out-of-range articles
silently and just logs how many it dropped. Surface that in the UI instead:
have /pipeline/fetch return a count of how many articles were filtered out,
and show a small message on the article-selection page like "3 articles
outside your selected time window were excluded."

Read the existing code first, then propose how you'd structure this before
making any changes.
```

### Fact checking
The agent writes newsletter content that could contain inaccuracies. Add a page that surfaces claims for the user to verify before approving:

```
Add a fact-checking step between the review screen and the send screen. After
the newsletter is generated, call an LLM to extract the key factual claims from
the text (names, statistics, dates, attributions) and display them as a checklist
so the user can mark each one as verified or flagged. The newsletter can only
proceed to send once the user has reviewed the list.

Read the existing code and HTML first, then propose your approach before making
any changes.
```

### Actually send the email
Right now the "Send newsletter" button on the send screen just skips straight to the confirmation screen — nothing actually gets sent. Make it real using [SendGrid](https://sendgrid.com) (free tier, no credit card needed):

```
The "Send newsletter" button goes straight to the confirmation screen without
calling any backend. Make it actually send an email.

Use the SendGrid API (https://sendgrid.com — free tier). The flow should be:
1. The button POSTs to a new /send endpoint on the server with the newsletter
   HTML and the recipient address(es)
2. The server calls the SendGrid API to send the email
3. On success the frontend navigates to the confirmation screen

The SendGrid API key will need to go in the server as a constant (I'll provide
it). Read the existing send page HTML and the server code first, then propose
what needs to change before touching anything.
```

For SendGrid, the key call is a POST to `https://api.sendgrid.com/v3/mail/send` with an `Authorization: Bearer <key>` header and a JSON body containing `to`, `from`, `subject`, and the newsletter HTML as the `html_content`. Bob knows the SendGrid API shape — just point it at the task.

---

The coding bonus tasks all have real design decisions — let Bob propose first, then push back or redirect if the plan doesn't feel right. That back-and-forth is the point.
