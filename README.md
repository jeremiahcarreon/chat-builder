# Chat Builder

Self-hosted dashboard for building scoped, RAG-enabled chat apps.
See [OVERVIEW.md](OVERVIEW.md) for the design.

## Install

Requires Linux or macOS with Docker (the installer offers to install Docker on
Linux). Run:

```bash
curl -fsSL https://raw.githubusercontent.com/jeremiahcarreon/chat-builder/main/install.sh | bash
```

It asks for an admin email and password, installs to `~/chat-builder`
(override with `CB_HOME=/path`), starts everything, and prints the dashboard
address (default http://localhost:8080). The first start downloads the
EmbeddingGemma model (about 600 MB); **Settings → System status** shows when
it's ready.

Back up `~/chat-builder/.env`. Its `MASTER_KEY` decrypts saved API keys and
database passwords and can't be recovered if lost.

## Manage it

```bash
chat-builder status        # what's running
chat-builder logs api      # follow a service's logs
chat-builder stop | start | restart
chat-builder version       # installed and latest versions
chat-builder update        # install the latest release now
chat-builder backup        # database backup to ~/chat-builder/backups
chat-builder uninstall     # remove containers (keeps data; --purge deletes it)
```

`~/chat-builder/docker-compose.yml` is yours to customize (ports, volumes,
bind mounts). Updates never change it.

## Updates

New releases install automatically. The updater checks GitHub every 30
minutes; when a release is available the dashboard shows a banner with
**Update now**, and otherwise it installs within 24 hours. Each update backs up
the database first, and rolls back on its own if the new version doesn't start.
See [RELEASING.md](RELEASING.md) for how releases are published and kept
compatible.

## Run from source with Docker

```bash
cp .env.example .env       # fill in the values (see comments)
docker compose up -d --build
```

## Develop without Docker

Backend (needs a Postgres with pgvector, and Ollama):

```bash
cd backend
uv sync
export DATABASE_URL=postgresql+psycopg://user:pass@localhost/chatbuilder \
       MASTER_KEY=... ADMIN_EMAIL=... ADMIN_PASSWORD=... OLLAMA_URL=http://localhost:11434
uv run alembic upgrade head
uv run uvicorn app.main:app --reload   # API on :8000
uv run python -m app.worker            # in another terminal
uv run pytest
```

Front end (Node 20+), with API calls proxied to :8000:

```bash
cd frontend
npm install
npm run dev
```
