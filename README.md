# AI Job Application Agent

An end-to-end agentic pipeline that autonomously applies to jobs on behalf of a candidate. Given a queue of job URLs, the system tailors a resume, generates a cover letter, opens a real browser session, detects the ATS platform, fills every form field using a multi-tier inference engine, handles ambiguous fields via human-in-the-loop escalation, submits the application, and records the outcome to a PostgreSQL database — with no manual intervention required.

---

## Architecture Overview

The agent is orchestrated using LangGraph. The state machine defines four sequential nodes:

```
fetcher --> marker --> automator --> recorder
                |
           [empty queue] --> END
```

- `fetcher` — queries the database for the next `pending` job and loads the full candidate context into state.
- `marker` — immediately sets the job status to `running` in the database to prevent duplicate processing.
- `automator` — handles the entire browser session: navigation, ATS detection, job validation, document generation, form filling, HITL escalation, and submission.
- `recorder` — writes the final status (`submitted`, `failed`, `insufficient_knowledge`, or `backlog`) and any unanswered fields back to the database.

The main loop in `main.py` calls `app.ainvoke()` repeatedly until the queue is empty.

---

## Tech Stack

| Concern | Technology |
|---|---|
| Agent orchestration | LangGraph, LangChain |
| Language | Python 3.11+ |
| Browser automation | Playwright (persistent Chromium context) |
| LLM | Google Gemini 2.5 Flash via `google-generativeai` |
| Database | PostgreSQL via `psycopg2` |
| PDF generation | `fpdf2` |
| PDF parsing | `pypdf` |
| Environment config | `python-dotenv` |

---

## How to Run the Demo

### Prerequisites

- Python 3.11 or higher
- PostgreSQL running locally or remotely
- Google Gemini API key
- A logged-in Chrome session (see browser session note below)

### Step 1 — Clone and install dependencies

```bash
git clone <repo-url>
cd ai-job-agent
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Step 2 — Configure environment variables

Create a `.env` file in the project root:

```env
DB_NAME=job_agent
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432
GEMINI_API_KEY=your_gemini_api_key
```

### Step 3 — Create the database

```bash
psql -U postgres -c "CREATE DATABASE job_agent;"
```

### Step 4 — Seed the database

```bash
python seed.py
```

This creates all tables and inserts the demo candidate profile, pre-filled custom answers, and three job listings.

### Step 5 — Authenticate your browser session

The agent uses a persistent Chromium profile stored in `./browser_session`. Before the first run, launch the browser once and log in to LinkedIn (and any other platforms in the job queue):

```bash
python -c "
import asyncio
from playwright.async_api import async_playwright

async def login():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context('./browser_session', headless=False, channel='chrome')
        await ctx.pages[0].goto('https://www.linkedin.com/login')
        input('Log in manually, then press Enter to save session...')
        await ctx.close()

asyncio.run(login())
"
```

### Step 6 — Run the agent

```bash
python main.py
```

The agent will process each pending job in the queue sequentially. For any field it cannot resolve, it will prompt for input in the terminal with a 120-second timeout. Final status for each job is printed to the console and written to the database.

---

## Candidate Database Structure

The schema consists of three tables.

### `candidates`

Stores the candidate's complete profile. Only one row is used per agent run.

| Column | Type | Description |
|---|---|---|
| `id` | SERIAL | Primary key |
| `full_name` | VARCHAR | Used to derive `first_name` and `last_name` at runtime |
| `email` | VARCHAR | Contact email |
| `phone` | VARCHAR | Contact phone number |
| `resume_path` | TEXT | Absolute or relative path to the base resume PDF |
| `portfolio_url` | TEXT | Personal website or portfolio link |
| `github_url` | TEXT | GitHub profile URL |
| `work_history` | JSONB | Array of role objects with `company`, `title`, `start_date`, `end_date`, `description` |
| `education` | JSONB | Array of education objects with `institution`, `degree`, `graduation_year` |
| `skills` | JSONB | Array of skill strings |

### `custom_answers`

A key-value store for answers that do not appear on any resume: compensation expectations, demographic questions, availability, and similar fields unique to application forms.

| Column | Type | Description |
|---|---|---|
| `id` | SERIAL | Primary key |
| `question_key` | VARCHAR (UNIQUE) | Normalised form question label |
| `answer_text` | TEXT | The candidate's answer |

Any new entry inserted into this table is automatically used on all future runs without any code changes. During a HITL session, answers provided by the user are written back to this table via an upsert so they are never asked again.

### `jobs`

Tracks every job in the processing queue.

| Column | Type | Description |
|---|---|---|
| `id` | SERIAL | Primary key |
| `job_url` | TEXT (UNIQUE) | The job posting URL |
| `company_name` | VARCHAR | Company name for context |
| `ats_type` | VARCHAR | Detected platform: LinkedIn, Workday, Greenhouse, Lever, etc. |
| `status` | VARCHAR | `pending`, `running`, `submitted`, `failed`, `insufficient_knowledge`, `backlog` |
| `retry_count` | INT | Number of retry attempts |
| `failure_reason` | TEXT | Populated when status is not `submitted` |
| `unanswered_fields` | JSONB | Array of field labels the agent could not resolve |
| `logs` | TEXT | Freeform execution log |
| `created_at` | TIMESTAMP | Queue insertion time |

### Extending the candidate profile

To add a new standing answer (for example, a preferred work arrangement):

```sql
INSERT INTO custom_answers (question_key, answer_text)
VALUES ('preferred work arrangement', 'Remote');
```

No code changes are required. The agent performs a fuzzy match against all keys in this table during field resolution, so the new entry will be picked up automatically on the next run.

---

## ATS Detection

Detection runs in `browser/automation.py` inside the `detect_ats()` method, immediately after navigating to the job URL. The approach uses two layers.

**Layer 1 — URL pattern matching**

The final URL after any redirects is checked against known platform substrings:

| Pattern | Platform |
|---|---|
| `myworkdayjobs.com` | Workday |
| `greenhouse.io` | Greenhouse |
| `lever.co` | Lever |
| `linkedin.com` | LinkedIn |
| `indeed.com` | Indeed |
| `unstop.com` | Unstop |
| `naukri.com` | Naukri |

**Layer 2 — DOM fingerprint (fallback)**

If the URL does not match any known pattern, the page DOM is inspected for platform-specific structural markers. For example, Workday injects elements with `data-automation-id` attributes; Greenhouse renders a root element with the ID `greenhouse-app`. This ensures detection generalises to employer-hosted subdomains and custom career portals that do not expose the platform name in the URL.

The detected platform is stored in the `ats_type` column of the `jobs` table and used downstream to select the appropriate field extraction strategy. LinkedIn, for instance, scopes all form field queries to the Easy Apply modal (`div[role="dialog"]`) rather than the full page DOM.

---

## Form Field Mapping

Field resolution in `form_filler.py` follows a strict four-tier precedence chain. The agent never leaves a field blank if it has any basis to infer a value.

### Tier 1 — Candidate profile (database)

The agent normalises each form field label using a predefined alias table (`FIELD_ALIASES`). This maps surface variations such as "First Name", "firstname", and "Your first name" to a single canonical key `first_name`, which is then looked up directly in the candidate profile loaded from the `candidates` table. This tier handles all standard identity, contact, and background fields without any LLM call.

### Tier 2 — Custom answers (database)

If the canonical key is not found in the candidate profile, the agent searches the `custom_answers` table using both exact and fuzzy key matching. This tier covers all non-resume fields: salary expectation, notice period, sponsorship requirement, demographic disclosures, and similar.

### Tier 3 — LLM inference (Gemini)

If neither database tier resolves the field, the full candidate context, all custom answers, and the job description are passed to Gemini with the field label. For `select` fields, the available option texts are included so the model can return a value that matches a valid option. The model is instructed to return `UNABLE_TO_INFER` for any field it cannot answer with confidence — sensitive legal, financial, or highly role-specific questions.

### Tier 4 — Human-in-the-loop escalation

Fields that return `UNABLE_TO_INFER` are collected across the entire form step and presented to the operator in a single batch prompt. See the HITL section below for full behaviour.

### What gets logged

Any field the agent cannot fill — whether due to LLM uncertainty, HITL timeout, or exhausted HITL budget — is recorded in the `unanswered_fields` JSONB column of the `jobs` row. The terminal output at the end of each job run lists these fields explicitly, directing the operator to add the missing answers to the `custom_answers` table before the next run.

---

## Human-in-the-Loop (HITL)

### When it triggers

HITL is triggered only when Tier 3 (LLM inference) returns `UNABLE_TO_INFER` for one or more fields on a given form step. It does not trigger for fields resolved by the database. It does not trigger on every step — only when the agent genuinely cannot produce a confident answer.

### How the timeout works

All unresolved fields from a given step are presented to the operator at once in the terminal. For each field, the agent waits up to 120 seconds for a typed response. If the operator provides an answer, it is used to fill the field immediately and is also saved to the `custom_answers` table via an upsert, so the same question is never escalated again on future runs.

If the operator provides no input within the 120-second window, the agent treats the timeout as a hard stop for that HITL session. It does not continue waiting for subsequent fields in the same batch. Those fields are added to the `unanswered_fields` log and the agent proceeds to the next form step.

### HITL budget

Each job is allocated a maximum of three HITL rounds across all form steps. If the budget is exhausted, further unknown fields are logged directly without prompting the operator. This prevents the agent from blocking indefinitely on a single application.

### Backlog behaviour

If a job's form cannot be completed due to unanswered fields — whether because of timeout or budget exhaustion — and the application was not submitted, the job is marked `insufficient_knowledge`. The `unanswered_fields` column records exactly which fields were missing. The operator can add those answers to `custom_answers` and reset the job status to `pending` for a subsequent run:

```sql
UPDATE jobs SET status = 'pending', failure_reason = NULL WHERE id = <job_id>;
```

Submission is never attempted if the form is incomplete on a required field. The agent will only click the submit button after all visible fields on the final review step have been processed.

---

## Scaling

### Multiple candidates

The current schema supports one active candidate. To extend for multiple users, add a `candidate_id` foreign key to both the `jobs` table and the `custom_answers` table. Each agent run would be parameterised with a `candidate_id`, and all database queries would be scoped accordingly. The `SmartFiller` class already accepts `candidate_data` and `custom_answers` as arguments rather than loading them globally, so the change is contained to the data layer.

### Concurrent agents

The current architecture processes jobs sequentially in a single async loop. To run multiple jobs concurrently:

- Replace the sequential `while` loop in `main.py` with `asyncio.gather()` over a pool of worker coroutines, each pulling from the queue independently.
- Add a `SELECT ... FOR UPDATE SKIP LOCKED` clause to the job fetch query to prevent two concurrent workers from claiming the same job. The `mark_running` node already sets status to `running` immediately after fetch, but `SKIP LOCKED` eliminates the race condition at the database level.
- Limit concurrency with an `asyncio.Semaphore` to control the number of simultaneous browser instances, as each Playwright context is memory-intensive.

```python
# Example: bounded concurrent processing
semaphore = asyncio.Semaphore(3)  # max 3 browsers at once

async def process_one(job_id):
    async with semaphore:
        await app.ainvoke(...)

await asyncio.gather(*[process_one(jid) for jid in pending_job_ids])
```

### Job queue infrastructure

For production scale, replace the PostgreSQL polling loop with a dedicated task queue:

- **Redis + RQ or Celery** — lightweight option for a single-server deployment. Workers pull job tasks from a Redis queue; each worker manages one browser session.
- **AWS SQS + ECS** — for cloud-scale deployments. Jobs are enqueued to SQS; containerised workers on ECS Fargate consume messages, process the application, and write results back to RDS PostgreSQL. Dead-letter queues handle repeated failures automatically.
- **Temporal or Prefect** — for durable workflow orchestration with built-in retry policies, visibility into step-level failures, and human-in-the-loop pause/resume primitives that replace the current stdin-based HITL implementation.

The LangGraph graph definition requires no changes regardless of the queue backend — only the job dispatch and worker management layers change.

---

## Environment Variables Reference

| Variable | Description |
|---|---|
| `DB_NAME` | PostgreSQL database name |
| `DB_USER` | PostgreSQL user |
| `DB_PASSWORD` | PostgreSQL password |
| `DB_HOST` | Database host (e.g. `localhost`) |
| `DB_PORT` | Database port (default `5432`) |
| `GEMINI_API_KEY` | Google Gemini API key |

Credentials are loaded exclusively from `.env` via `python-dotenv`. They are never referenced directly in source code. The `.env` file must not be committed to version control.

---

## References

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Playwright Python Documentation](https://playwright.dev/python/)
- [Google Gemini API](https://ai.google.dev/gemini-api/docs)
- [Jobright](https://jobright.ai)
- [AutoApplier](https://www.autoapplier.com)
- [AI Apply](https://aiapply.co)
