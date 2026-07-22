# Newsletter Companion — Workshop Files
**IBM Women & Tech NL · July 3**

---

## What's in here

| File | What it is |
|------|------------|
| `app_bob.py` | **Start here if you're a participant.** Three functions stubbed out with TODO comments. Your job (with Bob) is to implement them. |
| `app.py` | The completed reference implementation. Shows what `app_bob.py` should look like when done — plus SSE streaming, token caching, and the sub-flow polling logic we had to figure out. Don't share this with participants before the lab. |
| `newsletter-companion-wireframe_4.html` | The full frontend. Five-page flow: set up → generating → review → send → sent. Served by both servers at `GET /`. |
| `LAB_GUIDE.md` | Participant-facing instructions. Explains the task, how to run the server, what to ask Bob, and three stretch goals for early finishers. |

---

## Dependencies

The server uses four packages — everything else in `venv` is pulled in by the IBM orchestrate CLI tooling and isn't needed to run the server itself.

| Package | Why |
|---------|-----|
| `fastapi` | Web framework — defines the routes |
| `uvicorn` | ASGI server — actually listens for HTTP connections and hands them to FastAPI |
| `httpx` | HTTP client — makes the outbound calls to IBM IAM and the wxO API |
| `pydantic` | Request validation — parses and validates the JSON body on `POST /pipeline` |

To install from scratch (if the `venv` isn't already set up):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Prerequisites

- Python 3.11+
- The `venv` directory already set up, or install from `requirements.txt` (see above)
- IBM watsonx Orchestrate CLI (`orchestrate`) with access to the workshop environment

---

## Running for the workshop (participant version)

```bash
source venv/bin/activate
orchestrate env activate workshop
uvicorn app_bob:app --port 8080 --reload
```

Open **http://localhost:8080** — the frontend loads but "Generate newsletter" returns a 501 until the participant implements the three functions.

---

## Running the reference version

```bash
source venv/bin/activate
orchestrate env activate workshop
uvicorn app:app --port 8080 --reload
```

This version works end-to-end immediately.

---

## How the two servers differ

`app_bob.py` uses the older sessions/turns API (`/v1/agents/{id}/sessions`) which is simpler and easier to explain. It's the right starting point for the lab.

`app.py` uses the newer runs API (`/v1/orchestrate/runs`) with SSE streaming — it's what we evolved to after hitting quirks with the live wxO Flow agent during testing. Participants who finish early and want to add streaming can use it as a reference.

---

## Editing the lab

- **Change the task difficulty:** edit the docstrings in `app_bob.py` — more or less detail in the hints changes how much Bob needs to infer
- **Change the stretch goals:** edit the "Finished early?" section in `LAB_GUIDE.md`
- **Change the frontend:** edit `newsletter-companion-wireframe_4.html` — it's a single self-contained file, no build step
