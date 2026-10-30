# MeetingHub: AI-powered knowledge and task management

Teams lose context whenever work changes hands. MeetingHub captures what was discussed and decided, hands that context to the right people, and keeps following up on open items until they are closed.

**Stack:** Python 3.10+, FastAPI, SQLite (SQLModel), Jinja2 templates, HTMX, Tailwind (CDN). One codebase, no frontend build step.

---

## 1. Run it locally (VS Code)

```bash
# 1. open the folder in VS Code, then open a terminal (Ctrl+`)
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
cp .env.example .env              # Windows: copy .env.example .env
# edit .env and set AI_API_KEY (see section 3). The app also runs without a key.

uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. Interactive API docs are at `/docs`. The database (`data/app.db`) and the log file (`logs/app.log`) are created on first start, and demo data is seeded automatically.

## 2. What it does (mapped to the brief)

| Requirement | Where |
|---|---|
| Create and persist meetings or work transfers (agenda, participants, notes, decisions, action items) | "New meeting or handover" form; meeting page |
| Lifecycle (planned, held, closed) and action-item tracking (owner, due date, status) | Meeting page; item status `open`, `in_progress`, `done`, `dropped` |
| Carry open items forward, keeping history | "Carry open items to a follow-up" creates a linked meeting, moves open items, and writes a `carried_forward` history entry. `origin_meeting_id` is preserved |
| AI turns unstructured info into a recap | "Generate AI recap": summary, decisions, action items with owner and due date |
| Catch-up brief for someone who missed the meeting or is taking over | "Catch-up brief" panel: what happened, what you own, what to watch out for, next steps |
| Knowledge base linked with AI | Past meetings are auto-added to the knowledge base. "Link items to knowledge base" connects items to relevant documents with a one-line reason |
| Record the outcome of each item | Outcome is **required** to mark an item `done` or `dropped` |
| Validation and error handling | Server-side validation with friendly messages; custom 404, 422 and 500 pages that show the request ID |
| Handles missed or no-response attendees and people on leave | Per-participant attendance; bulk "Hand over open work" reassignment |

## 3. AI configuration

| Setting (`.env`) | Meaning | Default |
|---|---|---|
| `AI_PROVIDER` | `anthropic`, `openai`, `gemini` or `none` | `anthropic` |
| `AI_API_KEY` | API key (read from the environment only, never logged) | empty |
| `AI_MODEL` | Model name, e.g. `claude-sonnet-5-5`, `gpt-4o-mini` or `gemini-flash-lite-latest` | `claude-sonnet-5-5` |
| `AI_TIMEOUT_SECONDS` | Per-call timeout | `30` |
| `AI_RETRIES` | Extra attempts after a failure | `1` |

**Data flow.** The browser sends a form to FastAPI. A service gathers the relevant rows from SQLite, and `ai_service.py` is the only module that talks to the model. The prompt is sent over HTTPS, and the reply is parsed as JSON and validated with Pydantic. Validated data is saved and the page re-renders.

**Four AI features, each with its own prompt in `app/ai_service.py`:**

1. **Recap:** raw notes in, `{summary, decisions[], action_items[{title, owner, due_date}]}` out. Relative dates ("by Friday") are resolved against today's date. A malformed date is dropped instead of failing the whole recap.
2. **Catch-up brief:** built from a context object (meeting summary, decisions, the person's attendance status, their open items across all meetings, overdue items). The model may only use facts from that context.
3. **Knowledge-base linking:** a cheap keyword shortlist (top 3 documents per item) is built first, then **one** LLM call picks which links are genuinely relevant and gives a one-line reason. IDs returned by the model are checked against the shortlist, so it cannot invent links.
4. **Follow-up nudge** (special feature, below).

**Reliability.**
- User text sits inside `<notes>` / `<context>` tags and the system prompt says to treat it as data, which reduces prompt-injection risk.
- Output is extracted from the reply (code fences are tolerated), parsed, and validated against a Pydantic schema. On failure it retries once, then falls back.
- **Graceful fallback:** if there is no key, a timeout, an HTTP error or invalid JSON, the app switches to rule-based behaviour and tells the user. The raw notes are always saved first, so nothing is lost.
  - Recap fallback reads lines like `Decision: ...` and `Action: Rahul to fix X by 2026-11-10`.
  - Brief fallback assembles the summary, items and overdue list from the database.
  - KB fallback uses the keyword match.
  - Nudge fallback uses a template.
- AI-created items are flagged **AI-suggested** in the UI. Everything is editable, so a human reviews before relying on it.

## 4. Data source

All data is **my own seed data** (`app/seed.py`), loaded on first start when the database is empty. Set `SEED_DEMO_DATA=false` to disable it.

- **Sprint 42 planning:** a held meeting with four participants (one *missed*, one *no response*), raw notes, two decisions and four action items, including an overdue one, a stalled one and a completed one with a recorded outcome.
- **Handover: Meera to Dev:** a planned work transfer with Meera marked *on leave* and raw handover notes you can run through the recap.
- **Knowledge base:** five team documents (payments webhook runbook, onboarding PRD, release checklist, vendor evaluation criteria, leave and handover policy) plus the sprint meeting itself.

Persistence is SQLite. Tables: `meeting`, `participant`, `decision`, `actionitem`, `itemhistory`, `document`, `itemdoclink`.

## 5. Logging setup

- **Format:** one JSON object per line, written to `logs/app.log` (rotating, 2 MB x 3 files) and to the console.
- **Request/correlation ID:** middleware reads `X-Request-ID` or generates one, stores it in a `contextvar`, stamps it on every log line, and returns it in the `X-Request-ID` response header. Error pages show it too.
- **Levels:** `INFO` for normal events, `WARNING` for 4xx responses and AI failures, `ERROR` with a stack trace for unhandled exceptions. Set the level with `LOG_LEVEL`.
- **Key events logged:** `meeting.created`, `meeting.status_changed`, `item.created`, `item.reassigned`, `item.status_changed`, `item.closed`, `items.carried_forward`, `recap.applied`, `kb.linked`, `document.created`, `participant.attendance_set`, `seed.completed`, `app.started`.
- **AI calls:** every call logs `op`, `provider`, `model`, `attempt`, `latency_ms` and `outcome` (`success` or `failure` with the error type). Prompts and notes are never logged.
- **No secrets:** the key is never logged; a formatter also redacts `sk-...` tokens, `Bearer ...` values, the configured key itself, and any context field named like a secret.

Example trace (one request ID covers the request, the AI retry and the business events):

```json
{"ts":"2026-10-30T05:51:51.378+00:00","level":"WARNING","logger":"meetinghub.ai","request_id":"18d89238954d","message":"ai.call","op":"recap","provider":"anthropic","model":"claude-sonnet-5-5","attempt":1,"latency_ms":412,"outcome":"failure","error":"ValueError"}
{"ts":"2026-10-30T05:51:51.379+00:00","level":"INFO","logger":"meetinghub.ai","request_id":"18d89238954d","message":"ai.call","op":"recap","provider":"anthropic","model":"claude-sonnet-5-5","attempt":2,"latency_ms":1830,"outcome":"success"}
{"ts":"2026-10-30T05:51:51.385+00:00","level":"INFO","logger":"meetinghub.service","request_id":"18d89238954d","message":"recap.applied","meeting_id":2,"used_ai":true,"decisions":1,"action_items":1}
{"ts":"2026-10-30T05:51:51.385+00:00","level":"INFO","logger":"meetinghub.app","request_id":"18d89238954d","message":"request","method":"POST","path":"/meetings/2/recap","status":303,"duration_ms":1904}
```

Trace one request end to end: `grep 18d89238954d logs/app.log` (PowerShell: `Select-String 18d89238954d logs/app.log`).

## 6. Special feature: Smart Follow-up Assistant

The brief says to follow up on open items until they are closed. Most tools only *show* overdue work. This one helps you *act* on it.

- **Needs attention** (`/attention` and the dashboard) automatically surfaces open items that are **overdue** or **stalled** (no update for `STALL_DAYS`, default 5), sorted by urgency.
- **Draft follow-up** writes a short, friendly, context-aware message for the owner, based on the item, how late it is, and the meeting it came from. It is editable and you send it yourself. If the AI is down, a template is used.
- Every status change, reassignment and due-date change refreshes the item's `updated_at`, so the "stalled" signal reflects real activity.

**Why it adds value:** action items usually die quietly. This turns detection into a one-click, personalised nudge, which closes the loop the problem statement describes.

## 7. Approach and architecture

```
Browser (HTMX + Jinja pages)
   |
FastAPI  -- middleware: request ID, request log, error pages
   |-- routes.py       thin HTTP layer: validate, call a service, render
   |-- services.py     lifecycle, carry-forward, reassignment, attention, knowledge base
   |-- ai_service.py   the only LLM code: retry, JSON validation, latency log, fallbacks
   |-- models.py       SQLModel tables (SQLite)
```

Key decisions:
- **Meeting-centric model.** The meeting or handover is the main object; action items hang off it and move between meetings.
- **History is a first-class table** (`itemhistory`). Creation, reassignment, due-date change, status change and carry-forward are all recorded, which satisfies "keeping history".
- **AI proposes, humans confirm.** Everything AI creates is editable and labelled.
- **Shortlist, then LLM** for knowledge-base linking: no vector database is needed at this scale, and the model's output is constrained to real IDs.
- **One AI module** makes the system easy to test, swap providers in, and reason about when AI fails.

## 8. Project structure

```
app/
  main.py          app, lifespan, middleware, error handlers
  routes.py        pages and JSON API
  services.py      business logic
  ai_service.py    prompts, LLM calls, fallbacks
  schemas.py       Pydantic models for AI output
  models.py        database tables
  db.py  config.py  logging_setup.py  seed.py
  templates/       Jinja pages and HTMX partials
requirements.txt  .env.example  Dockerfile
```

## 9. Deployment

Any host that runs a container or a Python web process works (Render, Railway, Fly.io).

1. Push this folder to GitHub.
2. Create a Web Service from the repo using the included `Dockerfile` (or build command `pip install -r requirements.txt`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`).
3. Set environment variables: `AI_PROVIDER`, `AI_API_KEY`, `AI_MODEL`.
4. Attach a **persistent disk** mounted at `/data` (the Dockerfile sets `DATA_DIR=/data` and `LOG_DIR=/data/logs`) so SQLite and logs survive restarts.

## 10. Assumptions

- A single team uses one instance, and names are free text (an item owner is just a name).
- The user pastes notes or a transcript; there is no audio transcription or email and chat integration.
- "Missed the meeting or never responded to the invite" is modelled as a participant attendance status.
- Dates are plain calendar dates and times are stored without time zones.

## 11. Known limitations

- **No authentication or roles.** Anyone with the URL can edit. A production version needs login and per-team access.
- **SQLite** is fine for a demo and small teams, but not for heavy concurrent writes. Swap `DATABASE_URL` for Postgres to scale.
- **Nudges are drafted, not sent.** There is no email or Slack integration, and no automatic reminders (no scheduler).
- **Knowledge-base linking is keyword-shortlisted.** It can miss documents that are related but share no words. Embeddings would fix this.
- **No file upload** for documents or recordings; content is pasted as text.
- **Very long notes** are truncated to 20,000 characters for the AI call (the full notes are still saved).
- AI output can still be wrong. That is why AI-suggested items are labelled and editable.
- Automated tests are not included; I verified the flows with an end-to-end smoke script during development.

## 12. Suggested demo script (5 minutes)

1. **Dashboard:** show the "Needs attention" panel (overdue and stalled items).
2. **Handover meeting:** open *Handover: Meera to Dev*, click **Generate AI recap**, and show the extracted items and decisions.
3. **Catch-up brief:** enter "Meera" on *Sprint 42 planning* (she missed it) and show the brief.
4. **Lifecycle:** try to mark an item done without an outcome (validation), then add one and close it. Open its **History**.
5. **Handover:** reassign Meera's open items to Dev.
6. **Carry forward:** create a follow-up meeting and show the carried items with their history.
7. **Knowledge base:** click **Link items to knowledge base** and show the reasons.
8. **Special feature:** `/attention`, **Draft follow-up**.
9. **Logs:** `grep` one request ID in `logs/app.log` to show the full trace.
