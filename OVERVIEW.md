# Chat Builder: Project Overview

A self-hosted dashboard for creating scoped chat apps. Each app has its own
LLM provider, personality, topic limits and data sources (documents,
spreadsheets, databases). RAG happens automatically behind the scenes, and a
System 1 classifier screens every question before it reaches the main model.

---

## 1. Language

**Python for all the logic:** the API, the background worker, RAG, file
parsing, database connectors and guardrails.

**A small React (TypeScript) front end for the screens.** Browsers only run
JavaScript, so the dashboard and chat pages are a thin React app that calls
the Python API. It contains no business logic.

Why Python: the hardest parts of this project are document parsing,
spreadsheets, SQL Server connections, SQL validation and LLM work, and
Python has the strongest tools for each. It's also the language the owner
already uses for chat apps.

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
                        | - parse files|>| + pgvector  | | Claude / OpenAI /|
                        | - chunk      | | app data,   | | Gemini           |
                        | - embed      | | vectors,    | +------------------+
                        | - index      | | job queue   |
                        +------+-------+ +-------------+ +------------------+
                               |                         | Your databases   |
                               v                         | SQL Server, MySQL|
                        +---------------+ api ---------->| MariaDB, Postgres|
                        | ollama        | (read-only)    | SQLite           |
                        | embeddinggemma|                +------------------+
                        +---------------+
```

### Containers (one `docker compose up`)

| Container  | Image                         | Role                                             |
|------------|-------------------------------|--------------------------------------------------|
| `api`      | built from our code           | API, screens, chat streaming, classifier         |
| `worker`   | same image, different command | Processes uploads in the background              |
| `postgres` | `pgvector/pgvector`           | All app data, vectors and the job queue          |
| `ollama`   | `ollama/ollama`               | Serves EmbeddingGemma (and a local classifier model, if used) |

**Persistent volumes:** database data, uploaded files and model weights.

There's no Redis: Postgres also runs the job queue (Procrastinate).

---

## 3. Libraries

| Area             | Choice                                  | Why                                             |
|------------------|-----------------------------------------|-------------------------------------------------|
| API              | FastAPI + Uvicorn                       | Fast, typed, built-in streaming                 |
| Database access  | SQLAlchemy + pgvector                   | Standard Python ORM with vector support         |
| Migrations       | Alembic                                 | Versioned schema changes                        |
| LLM layer        | Official `anthropic` and `openai` SDKs  | Anthropic SDK for Claude; OpenAI SDK for OpenAI, Gemini and local Ollama models (all OpenAI-compatible). Thin in-house adapter gives one interface for streaming and tools |
| Embeddings       | Ollama + EmbeddingGemma                 | Free, local, runs on CPU                        |
| Local chat models| Ollama                                  | Optional free, local chat or classifier models  |
| Job queue        | Procrastinate                           | Background jobs stored in Postgres              |
| PDF and Word     | Docling                                 | Handles complex PDFs and tables; OCR available  |
| Excel and CSV    | pandas + openpyxl                       | Load and query spreadsheets                     |
| SQL Server       | pyodbc + Microsoft ODBC driver          | Most reliable SQL Server connection             |
| MySQL / MariaDB  | PyMySQL                                 | One driver covers both                          |
| Postgres         | psycopg                                 | Standard driver                                 |
| SQLite           | sqlite3 (built in)                      | File-based databases                            |
| SQL safety       | sqlglot                                 | Confirms queries are SELECT-only, per dialect   |
| Passwords        | argon2-cffi                             | Secure password hashing                         |
| Encryption       | cryptography (AES-256-GCM)              | Encrypts API keys and DB passwords              |
| Front end        | React + Vite + Tailwind CSS             | Dashboard and chat screens                      |

---

## 4. How It Works

### Uploading a document
1. The file is saved and a background job is queued.
2. **PDF / Word / text:** the worker extracts the text, splits it into
   roughly 500-token chunks, embeds them with EmbeddingGemma, and stores
   them in pgvector tagged with the app's ID.
3. **Excel / CSV:** each sheet becomes a real table in a sandboxed schema,
   so the model can query it accurately (sums, filters, and so on).
4. The UI shows each file's status: processing, ready or failed.

### Connecting a database
1. Enter host/IP, port, username, password and database (SQLite uses a file).
2. Use **Test connection**.
3. The app reads the schema and embeds a description of each table, so the
   right tables are found later without sending the whole schema every time.

### Chatting
1. **Rate limit check:** messages per minute and daily tokens per app.
2. **System 1 classifier:** see section 5. Blocked questions get the app's
   refusal message and never reach the main model.
3. **Retrieval:** pull the matching document chunks and table descriptions
   for this app only.
4. **Prompt assembly:** a hardened system prompt is built from the app's
   personality, scope and strict-mode settings, plus the retrieved context.
5. **Model call with tools:** the model streams its answer and can call
   `query_database` or `query_spreadsheet`. Queries must be SELECT-only and
   get row limits and timeouts. They run over read-only connections.
6. **Logging:** token usage (classifier and main model) is recorded for the
   usage dashboard.

---

## 5. System 1 Classifier (Guardrails)

A fast, inexpensive model that checks every incoming question before the
main ("System 2") model sees it. It's the main defense against people using
an app for things it wasn't built for.

**What it decides, per message:**
- **Allowed:** in scope for this app, so it goes on to the main model.
- **Off-topic:** outside the app's scope, so it gets the refusal message.
- **Abuse:** a jailbreak attempt, prompt injection, or harmful request, so
  it's refused and flagged.

**What it checks against:** each app's in-scope topics, out-of-scope
topics and personality, plus short descriptions of its data sources. It
also looks at recent conversation turns, so follow-ups like "what about
last year?" aren't wrongly blocked.

**Design points:**
- Structured output (verdict, category, confidence, short reason), so
  decisions are consistent and can be reported on.
- Configurable per app: on/off, which classifier model to use, and how
  strict to be.
- Every verdict is saved with the message, so blocked questions can be
  reviewed and the scope tuned.
- Can run on a small local model in Ollama (no API cost) or a small, cheap
  hosted model.
- The model the owner named for this role still needs to be confirmed.

---

## 6. Chat App Settings

- **Name and personality:** tone and style
- **In scope:** topics it should answer
- **Out of scope:** topics or behaviors it must refuse
- **Refusal message:** what it says to off-topic questions
- **Strict mode:** answer only from the attached data, never guess
- **Classifier:** on/off and strictness
- **Provider and model:** Claude, OpenAI, Gemini, or a local model in Ollama
- **Limits:** maximum answer length, messages per minute, daily tokens

---

## 7. Data Model

| Table                       | Holds                                                    |
|-----------------------------|----------------------------------------------------------|
| `users`, `sessions`         | Login accounts and active logins                         |
| `provider_keys`             | Encrypted API keys                                       |
| `chat_apps`                 | Name, provider, personality, scope, classifier, limits   |
| `data_sources`              | Files and DB connections (encrypted credentials)         |
| `chunks`                    | Text chunks with 768-dimension vectors                   |
| `schema_docs`               | Table and column descriptions with vectors               |
| `conversations`, `messages` | Chat history, including classifier verdicts              |
| `usage_events`              | Tokens and cost per request (classifier and chat)        |
| `service_heartbeats`        | Worker check-ins for the status panel                    |

---

## 8. Folder Layout

```
chat-builder/
├── docker-compose.yml
├── Dockerfile               # builds the React app, then the Python image
├── .env.example             # master key, admin login, DB password
├── backend/
│   ├── pyproject.toml
│   ├── alembic/             # database migrations
│   └── app/
│       ├── main.py          # FastAPI app
│       ├── config.py        # settings from .env
│       ├── models.py        # database tables
│       ├── security.py      # passwords, sessions
│       ├── crypto.py        # secret encryption
│       ├── providers.py     # LLM providers
│       ├── worker.py        # background jobs
│       ├── routers/         # API endpoints
│       ├── llm/             # (step 2+) chat engine, prompt builder
│       ├── guardrails/      # (step 6) System 1 classifier, rate limits
│       ├── rag/             # (step 3) chunking, embedding, retrieval
│       └── connectors/      # (steps 4-5) files, spreadsheets, databases
└── frontend/
    └── src/                 # React screens
```

---

## 9. Build Order

1. **Foundation:** Docker Compose, database, login, settings page for API keys
2. **Chat apps:** create and edit apps, with a working chat on the chosen provider
3. **Documents:** upload, ingestion, EmbeddingGemma, hidden RAG
4. **Spreadsheets:** Excel and CSV loaded into tables, plus the query tool
5. **Database connectors:** SQL Server, MySQL, MariaDB, Postgres, SQLite
6. **Guardrails and usage:** System 1 classifier, rate limits, usage dashboard

Each step leaves you with something that runs.

**Hardware:** plan for about 4 GB of RAM for the whole stack (more if the
classifier runs locally). EmbeddingGemma uses about 1–2 GB on CPU.
