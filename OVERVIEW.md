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
   uploaded file), and use **Test connection**.
2. The app reads the schema and embeds a description of each table.

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

---

## 5. System 1 Classifier (Guardrails)

A fast, inexpensive model that checks every incoming question before the
main ("System 2") model sees it.

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

Measured on a 3B local model (two apps, 8 messages each, run twice): 16/16
correct on both.

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
  run and marked regressed, fixed, new or unchanged.

---

## 7. Chat App Settings

- **Basics:** name, description, welcome message
- **Model:** provider and model
- **Response style:** temperature (or the model's default) and seed
- **Personality and scope:** personality, in scope, out of scope, refusal
  message, strict mode (answer only from attached data)
- **Guardrails:** classifier on/off, strictness, model, behavior if it's down
- **Limits and sharing:** answer length, messages per minute, daily tokens,
  public chat page

---

## 8. Install, Updates and Releases

- **Install:** `curl -fsSL .../install.sh | bash` checks for Docker, asks for
  the admin login, generates secrets, installs the `chat-builder` command and
  starts the stack from published images.
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

## 9. Data Model

| Table                       | Holds                                                    |
|-----------------------------|----------------------------------------------------------|
| `users`, `sessions`         | Login accounts and active logins                         |
| `user_settings`             | Cloud switch and model prices                            |
| `provider_keys`             | Encrypted API keys                                       |
| `chat_apps`                 | Settings for each chat app                               |
| `prompt_versions`           | Numbered snapshots of answer-shaping settings            |
| `data_sources`              | Files and DB connections (encrypted credentials)         |
| `chunks`                    | Text chunks with 768-dimension vectors                   |
| `schema_docs`               | Table and column descriptions with vectors               |
| `conversations`, `messages` | Chat history, verdicts, prompt version, model, ratings and feedback |
| `test_cases`, `test_runs`, `test_results` | Test sets and their results            |
| `usage_events`              | Tokens per model call (chat, classifier, test)           |
| `service_heartbeats`        | Worker check-ins for the status panel                    |

Spreadsheet data lives in the separate `sheets` schema; the job queue's
tables are managed by Procrastinate.

---

## 10. Folder Layout

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
├── scripts/smoke-test.sh    # end-to-end check used by CI
├── .github/workflows/       # CI and release pipelines
├── backend/
│   ├── alembic/             # database migrations
│   ├── tests/
│   └── app/
│       ├── routers/         # API endpoints
│       ├── llm/             # chat engine, providers, prompt, sampling
│       ├── guardrails/      # classifier, rate limits
│       ├── rag/             # parsing, OCR, chunking, embeddings, retrieval
│       ├── connectors/      # spreadsheets, databases, SQL tool and safety
│       ├── evals/           # prompt versions, grading, test runner
│       ├── routing.py       # cloud switch
│       └── pricing.py       # cost estimates
└── frontend/src/            # React screens
```

---

## 11. Status

All planned features are built: foundation, chat apps, documents (with OCR),
spreadsheets, database connections, guardrails, usage and cost tracking,
cloud switch, response style, evaluation harness, installer, CLI and
automatic updates.

Not yet done:
- **First release:** the repository must be made public, and `v0.1.0`
  tagged, before installs and updates work for anyone else.
- **Multiple users:** there's a single admin login; the data model is ready
  for more users.

**Hardware:** plan for about 4 GB of RAM for the stack, plus whatever local
models need. EmbeddingGemma uses about 1–2 GB on CPU.
