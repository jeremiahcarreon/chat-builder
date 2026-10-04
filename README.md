# Chat Builder

Self-hosted dashboard for building scoped, RAG-enabled chat apps.
See [OVERVIEW.md](OVERVIEW.md) for the design.

## Run it

Requires Docker with the Compose plugin.

```bash
cp .env.example .env
# Fill in .env: ADMIN_EMAIL, ADMIN_PASSWORD, and generate the two secrets:
openssl rand -hex 24      # -> POSTGRES_PASSWORD
openssl rand -base64 32   # -> MASTER_KEY (back this up)

docker compose up -d --build
```

Open http://localhost:8080 and sign in with your admin email and password.
On first start the worker downloads EmbeddingGemma (about 600 MB); the
status panel on the Settings page shows when it's ready.

Back up `MASTER_KEY`. Without it, saved API keys and database passwords
can't be decrypted.

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
