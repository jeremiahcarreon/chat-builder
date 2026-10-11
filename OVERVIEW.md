# Chat Builder: Project Overview

A self-hosted dashboard for creating scoped chat apps. Each app has its own
LLM provider, personality, topic limits and data sources (documents, scanned
documents, spreadsheets, databases). Retrieval happens automatically, a
System 1 classifier screens every question before it reaches the main model,
and a built-in evaluation harness tracks answer quality across prompt
versions. Installs with one command and keeps itself up to date.

---

## 1. Language

**Python for all the logic:** the API, the background worker, retrieval,
file parsing and OCR, database connectors, guardrails and evaluation.

**A small React (TypeScript) front end for the screens.** Browsers only run
JavaScript, so the dashboard and chat pages are a thin React app that calls
the Python API. It contains no business logic.

**Bash for installation and updates** (`install.sh`, the `chat-builder`
CLI), so a host needs nothing but Docker.

---

## 2. Architecture

```
                        +------------------------------------------+
  Browser ------------->|  api  (FastAPI + built React app)        |
  (dashboard + chat)    |  - Dashboard and chat screens            |
                        |  - REST API      - Chat engine           |
                        |  - System 1 classifier (guardrails)      |
                        +-------+---------------+--------------+----+
                                |               |              |
          jobs (Procrastinate)  |               | SQL          | HTTPS
                                v               v              v
                        +--------------+ +-------------+ +------------------+
                        | worker       | | postgres    | | LLM providers    |
                        | - parse, OCR |>| + pgvector  | | Claude / OpenAI /|
                        | - chunk      | | app data,   | | Gemini           |
                        | - embed      | | vectors,    | +------------------+
                        | - test runs  | | job queue   |
                        +------+-------+ +-------------+ +------------------+
                               |                         | Your databases   |
                               v                         | SQL Server, MySQL|
                        +---------------+ api ---------->| MariaDB, Postgres|
                        | ollama        | (read-only)    | SQLite           |
                        | embeddinggemma|                +------------------+
                        | local models  |
                        +---------------+
                        +---------------+
                        | updater       | checks GitHub releases, installs
                        +---------------+ updates through the Docker socket
```

### Containers

| Container  | Image                                   | Role                                              |
|------------|-----------------------------------------|---------------------------------------------------|
| `api`      | `ghcr.io/jeremiahcarreon/chat-builder`   | API, screens, chat streaming, classifier; runs migrations on start |
| `worker`   | same image, different command           | Document ingestion and OCR, schema indexing, test runs |
| `postgres` | `pgvector/pgvector:pg17`                | App data, vectors, job queue, spreadsheet tables   |
| `ollama`   | `ollama/ollama`                         | EmbeddingGemma, and any local chat or classifier models |
| `updater`  | `ghcr.io/jeremiahcarreon/chat-builder-updater` | Checks for releases every 30 minutes and installs them |

**Persistent volumes:** database data, uploaded files, model weights, and
update status. There's no Redis: Postgres also runs the job queue.

---

## 3. Libraries

| Area             | Choice                                  | Why                                             |
|------------------|-----------------------------------------|-------------------------------------------------|
| API              | FastAPI + Uvicorn                       | Fast, typed, built-in streaming                 |
| Database access  | SQLAlchemy + pgvector                   | Standard Python ORM with vector support         |
| Migrations       | Alembic                                 | Versioned, reversible schema changes            |
| LLM layer        | Official `anthropic` and `openai` SDKs  | Anthropic SDK for Claude; OpenAI SDK for OpenAI, Gemini and local Ollama models (all OpenAI-compatible); a thin adapter gives one interface for streaming and tools |
| Embeddings       | Ollama + EmbeddingGemma                 | Free, local, runs on CPU                        |
| Job queue        | Procrastinate                           | Background jobs stored in Postgres              |
| PDF and Word     | pypdf + python-docx                     | Text and table extraction                       |
| Classifier, reviews | TypeSafe AI's Jev (`typesafe-sdk`)   | A "System One" model: typed decisions (choice, yes/no probability, score) in one fast pass, at $0.042 per million input tokens |
| OCR              | RapidOCR (ONNX Runtime) + pypdfium2     | Scanned PDFs and images; CPU-only, no system packages, models bundled in the image |
| Excel and CSV    | pandas + openpyxl                       | Sheets become queryable tables                  |
| SQL Server       | pymssql (bundles FreeTDS)               | No system drivers; supports encrypted connections |
| MySQL / MariaDB  | PyMySQL                                 | One driver covers both                          |
| Postgres         | psycopg                                 | Standard driver                                 |
| SQLite           | sqlite3 (built in)                      | File-based databases                            |
| SQL safety       | sqlglot                                 | Confirms queries are a single read-only SELECT, per dialect |
| Passwords        | argon2-cffi                             | Secure password hashing                         |
| Encryption       | cryptography (AES-256-GCM)              | Encrypts API keys and DB passwords              |
| Front end        | React + Vite + Tailwind CSS             | Dashboard and chat screens                      |

---

## 4. How It Works

### Uploading a document
1. The file is saved and a background job is queued.
2. **PDF, Word, text, Markdown, HTML:** text is extracted with page numbers.
   PDF pages without a text layer, and image files (PNG, JPG, TIFF), are read
   with OCR.
3. Text is split into roughly 500-token chunks with overlap, embedded with
   EmbeddingGemma (using its document prefix), and stored in pgvector tagged
   with the app.
4. **Excel / CSV:** each sheet becomes a real table in the sandboxed
   `sheets` schema; a description of each table (columns, types, sample
   values) is embedded so the right tables are found later.
5. The UI shows each file's status: processing, ready or failed.

### Connecting a database
1. Enter host/IP, port, username, password and database (SQLite uses an
   uploaded file), a query time limit, and for SQL Server whether to read
   without waiting on other users' changes. Use **Test connection**.
2. The app reads every table's columns, primary key, foreign keys and
   approximate row count from the database catalog, and embeds a description
   of each table, including what it joins to and what references it.
3. In the **Tables** panel, owners add notes to tables and columns
   (essential for cryptic ERP names) and hide tables the assistant must never
   use. Hidden tables can't be found or queried.
4. During a chat, the model can call `find_tables` and `describe_table` to
   look beyond the few tables it's shown for a question.

### Documents tagged by SKU
Documents can carry SKU tags, set automatically when the file name contains
part numbers ("RK-2 install instructions.pdf") and editable. When a question
names a SKU, documents tagged with it are searched first and documents tagged
only with other SKUs are left out; untagged documents are always searched.
Matching ignores case, spaces and dashes.

### Chatting
1. **Rate limits:** messages per minute per person, tokens per day per app.
2. **System 1 classifier:** see section 5. Blocked questions get the app's
   refusal message and never reach the main model.
3. **Retrieval:** matching document passages and table descriptions for this
   app only. Unrelated tables aren't sent, and the query tool is offered only
   when a relevant table exists.
4. **Prompt:** a hardened system prompt built from the app's settings (stable
   across requests, so it caches); retrieved context goes in the user turn.
5. **Model call with tools:** the model streams its answer and can call
   `sql_query`. Queries are validated as a single read-only SELECT on the
   source's own tables, regenerated from the parsed SQL, and run read-only
   with a timeout and row limit. Spreadsheet queries run as a per-source
   database role that can read only that source's tables.
6. **Recording:** each answer stores its prompt version, the model that
   produced it, sources used and data queries made; token usage is logged.

### Which model answers
- Each app picks a provider and model: Claude, OpenAI, Gemini, or a local
  Ollama model.
- **Cloud switch (Settings):** one toggle moves every local-model app, and its
  classifier, to a chosen cloud provider and model without editing the apps.
- **Temperature and seed** per app, sent only to models that accept them.
  Current Claude models and OpenAI reasoning models reject temperature; the
  editor says when a setting has no effect.
- **Automatic model choice (optional, per app):** the app lists up to four
  models, cheapest first, each with the effort levels to try (for example
  Haiku; Sonnet at low, then high effort; Opus). For every message, Jev (or
  the app's classifier model) judges the question's difficulty, and the
  answer starts at that tier's lowest effort. If the person's next message
  says the answer missed, the conversation moves up one step, first more
  effort and then a bigger model, and stays at least that high. A local app
  can list cloud models above its own to move up only when needed. Effort is
  sent as Claude's `output_config.effort` or OpenAI and Gemini's
  `reasoning_effort`; Claude's supported levels come from the Models API.
  Measured on 40 labeled parts-desk messages: Jev picked the right level for
  21 of 24 questions (all within one level) and caught 16 of 16 unhappy
  replies; a local 3B model scored 22/24 and 15/16.
- **Get a better answer:** under answers from below the top of the ladder,
  one click re-answers the same question one step up and replaces the answer.
  The old answer counts as "not helpful" in the Quality tab, with an optional
  one-tap reason (Wrong, Incomplete, Didn't answer my question).
- **Savings report (Usage):** per app, which models answered how often, how
  often conversations moved up, and the cost compared with sending every
  answer to the app's top model (routing checks included).
- **Ladder calibration (Quality):** runs the test set on each model in the
  ladder and suggests changes, like dropping a tier nothing needed.

---

## 5. System 1 Classifier (Guardrails)

A fast, inexpensive model that checks every incoming question before the
main ("System 2") model sees it.

**Which model:** Jev, TypeSafe AI's System One model, whenever the owner has a
TypeSafe key and the app hasn't chosen its own classifier; otherwise a small
model from the app's provider (or the app's local model). Jev answers with a
choice question (which topic, or "none") and a yes/no probability
(manipulation attempt); strictness sets how sure it must be before blocking.

Measured on 33 labeled messages across three apps, including a real catalog
app whose scope is written as a paragraph: Jev 33/33, the local 3B model 29/33
(it blocked four legitimate catalog questions). Jev's median latency was
119 ms, at about $0.00003 per message.

**How it decides:**
- Greetings and thanks are allowed without a model call.
- A message mentioning one of the owner's out-of-scope items is blocked
  without a model call (word match, so "warranty" matches "warranties").
- Otherwise the model answers two narrow questions: which topic from a
  numbered list (the app's scope items, its documents and tables, and
  greetings) the message is about, and whether it tries to manipulate the
  assistant. The verdict (allowed, off-topic, misuse) is derived in code;
  small models identify the topic well but mislabel verdicts when asked for
  them directly.
- A question that closely matches the app's own documents is never blocked
  as off-topic, unless it's explicitly out of scope.
- Recent conversation turns are included, so follow-ups aren't blocked.
- Runs at temperature 0 with a fixed seed where supported.

**Settings per app:** on/off, strictness, classifier provider and model, and
whether to let messages through or block them if the classifier is down.
Off-topic and misuse get the same reply, so probing reveals nothing.


---

## 6. Evaluation

Tools for an app's owner to raise answer quality and keep it there
(**Quality** tab):

- **Feedback:** people chatting rate each answer helpful or not; a thumbs
  down asks what was wrong.
- **Prompt versions:** a numbered snapshot of every setting that shapes
  answers, recorded whenever one changes (or the prompt template in code
  changes). Every answer is tied to the version that produced it. Each
  version shows what changed, its helpful rate and its test score.
- **Rated answers:** filter by helpful or not and by version; turn any rated
  answer into a test in one click (a helpful answer becomes the reference).
- **Test sets:** questions with expected outcomes: should be answered or
  refused, phrases the answer must mention, and an optional reference answer
  checked by a judge model at temperature 0.
- **Test runs:** run in the background through the same path as live chat,
  against the current version. Each result is compared with the previous
  run and marked regressed, fixed, new or unchanged. With Jev, the reference
  answer check is a Jev yes/no question.
- **Satisfaction review:** after a conversation has been quiet for 30
  minutes, Jev scores how satisfied the person seemed (0 to 100, from a
  five-level scale) and how likely their need was met. Refused misuse
  attempts aren't scored. The Quality tab shows the average, the least
  satisfied conversations with full transcripts, and satisfaction per prompt
  version; **Review now** scores conversations without waiting.

---

## 7. People and Access

- **One workspace.** Admins manage every app, provider key and setting.
  Members only chat with the apps they've been given.
- **Invites** are one-time links (valid 7 days) the admin sends; the person
  sets their own password. Password resets work the same way.
- **Blocking** someone signs them out everywhere, including widget sessions.
- Each person's conversations are private to them; admins can read any of
  them from the Quality tab.

## 8. Embeddable Widget

One `<script>` tag adds a chat button to any allowed site. The chat opens in
an iframe served by Chat Builder, so the host page's styles can't affect it
and the host page can't read it. Browsers only allow the iframe on the app's
allowed domains (Content-Security-Policy `frame-ancestors`), and the iframe
only accepts sign-in messages from them.

**Sign-in modes per app:**
- **Signed:** the host site's server signs a JWT (HS256, at most 24 hours,
  with `sub` = username, `email`, optional `name`, and `aud` = the app ID)
  with the app's secret. Chat Builder verifies it, creates or updates the
  person (the username is the stable identity), grants access to the app, and
  opens a 12-hour widget session. Blocked people are refused.
- **Login:** Chat Builder accounts, inside the widget.
- **Public:** anonymous visitors.

Widget sessions are bearer tokens of a separate kind: they work only for
chatting with that one app, never for the dashboard (even for admins).

### Connected systems (MCP)
- A data source can be an **MCP server**, like the ERP's: URL plus an optional
  bearer token (stored encrypted). Processing lists its tools; read-only tools
  are on by default, and tools that may change data stay off until an admin
  turns them on. The model sees allowed tools as `<source>__<tool>` next to
  `sql_query`, and Try it shows each lookup.
- The API's `context` (like `{"ebay_buyer": "sample_buyer"}`) stays with the
  thread, is shown to the model, and is sent with each tool call in `_meta`
  under `chat-builder/context`, so the server can return only that person's
  records. See docs/mcp-servers.md for what a server needs.

### API for other systems
- An admin creates **API keys** for one app in its Deploy tab. A key is shown
  once, stored as a hash, and reaches only that app's API (never the dashboard,
  other apps or settings). Deleting it stops it immediately.
- `POST /api/v1/apps/{app}/answer` with `{"message", "thread_id"}` returns a
  drafted answer as JSON (answer, blocked, message_id, model, effort, lookups).
  It runs the same guardrails, data queries and automatic model choice as the
  chat. Messages with the same `thread_id` (an eBay thread, a ticket) continue
  one conversation. Guardrail refusals return `blocked: true`; rate limits 429;
  provider failures 502.
- `POST /api/v1/apps/{app}/messages/{id}/outcome` with `sent`, `edited` (plus
  `final_text`) or `discarded` records what staff did with the draft. It shows
  as feedback in the Quality tab, and an edited reply becomes the reference
  answer when the answer is turned into a test.
- Built for the ERP: a queued job drafts a reply for each incoming eBay
  message, and staff check and send it.

---

## 9. Chat App Settings

- **Basics:** name, description, welcome message
- **Model:** provider and model
- **Response style:** temperature (or the model's default) and seed
- **Personality and scope:** personality, in scope, out of scope, refusal
  message, strict mode (answer only from attached data)
- **Product knowledge:** rules the assistant always needs (part-number
  relationships, what data fields mean), included in every prompt and
  versioned
- **Guardrails:** classifier on/off, strictness, model, behavior if it's down
- **Limits and sharing:** answer length, messages per minute, daily tokens,
  public chat page

---

## 10. Install, Updates and Releases

- **Install:** `curl -fsSL .../install.sh | bash` checks for Docker, asks for
  the admin login and (optionally) the public address behind a reverse proxy,
  generates secrets, installs the `chat-builder` command and starts the stack
  from published images.
- **CLI:** `chat-builder start | stop | restart | status | logs | open |
  version | check | update | backup | uninstall`.
- **Updates:** the updater checks GitHub releases every 30 minutes. The
  dashboard shows a banner with **Update now**; otherwise the update installs
  24 hours after the release is first seen. An update backs up the database,
  changes only `CB_VERSION` in `.env`, pulls the prebuilt images and
  recreates the `api` and `worker` containers from the owner's own compose
  file (ports, volumes and mounts untouched); migrations run on start; it
  rolls back automatically if the new version isn't healthy.
- **Releases:** tagging `vX.Y.Z` runs all checks, builds amd64 and arm64
  images, publishes them to GHCR and creates the GitHub release.
- **Compatibility:** rules in [RELEASING.md](RELEASING.md) (additive,
  reversible migrations; defaulted settings; no compose changes required),
  enforced by CI, including an upgrade test from the previous release.

---

## 11. Data Model

| Table                       | Holds                                                    |
|-----------------------------|----------------------------------------------------------|
| `users`, `sessions`         | People (admins, members, people from embedding sites) and their dashboard or widget sessions |
| `app_access`                | Which apps each member can use                           |
| `user_settings`             | Cloud switch and model prices                            |
| `provider_keys`             | Encrypted API keys                                       |
| `chat_apps`                 | Settings for each chat app, including embedding (mode, allowed sites, encrypted secret) |
| `prompt_versions`           | Numbered snapshots of answer-shaping settings            |
| `data_sources`              | Files and DB connections (encrypted credentials)         |
| `chunks`                    | Text chunks with 768-dimension vectors                   |
| `schema_docs`               | Table and column descriptions with vectors               |
| `conversations`, `messages` | Chat history, verdicts, prompt version, model, ratings, feedback and satisfaction scores |
| `test_cases`, `test_runs`, `test_results` | Test sets and their results            |
| `usage_events`              | Tokens per model call (chat, classifier, test, review)   |
| `service_heartbeats`        | Worker check-ins for the status panel                    |

Spreadsheet data lives in the separate `sheets` schema; the job queue's
tables are managed by Procrastinate.

---

## 12. Folder Layout

```
chat-builder/
├── install.sh               # one-line installer
├── VERSION                  # current release number
├── RELEASING.md             # release steps and compatibility rules
├── Dockerfile               # builds the React app, then the Python image
├── docker-compose.yml       # run from source (builds locally)
├── deploy/
│   ├── docker-compose.yml   # installs: published images + updater
│   └── chat-builder         # the CLI (also runs inside the updater)
├── updater/Dockerfile       # the updater image
├── scripts/smoke-test.sh    # quick health and ingestion check used by CI
├── scripts/e2e-test.py      # end-to-end tests used by CI (see RELEASING.md)
├── .github/workflows/       # CI and release pipelines
├── backend/
│   ├── alembic/             # database migrations
│   ├── tests/
│   └── app/
│       ├── routers/         # API endpoints
│       ├── llm/             # chat engine, providers, prompt, sampling
│       ├── guardrails/      # classifier (incl. Jev), rate limits
│       ├── rag/             # parsing, OCR, chunking, embeddings, retrieval
│       ├── connectors/      # spreadsheets, databases, SQL tool and safety
│       ├── evals/           # prompt versions, grading, test runner, reviews
│       ├── routing.py       # cloud switch
│       └── pricing.py       # cost estimates
└── frontend/src/            # React screens
```

---

## 13. Status

All planned features are built: foundation, chat apps, documents (with OCR),
spreadsheets, database connections, guardrails, usage and cost tracking,
cloud switch, response style, evaluation harness, installer, CLI and
automatic updates.

Not yet done:
- **First release:** the repository must be made public, and `v0.1.0`
  tagged, before installs and updates work for anyone else.

**Hardware:** plan for about 4 GB of RAM for the stack, plus whatever local
models need. EmbeddingGemma uses about 1–2 GB on CPU.
