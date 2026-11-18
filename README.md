# Chat Builder

Self-hosted dashboard for building scoped chat assistants on your own data.
See [OVERVIEW.md](OVERVIEW.md) for the design.

- **Your data:** upload PDFs (including scans), Word, text and Excel files, or
  connect SQL Server, MySQL, MariaDB, Postgres or SQLite. Documents are
  searched automatically; spreadsheets and databases are queried read-only.
- **Any model:** Claude, OpenAI, Gemini, or free local models through Ollama,
  chosen per app, with one switch to move local apps to the cloud.
- **Scoped and protected:** each app has a personality, topics it covers and
  refuses, a classifier that screens every question first (TypeSafe's Jev
  when you add a key), and rate and daily usage limits.
- **Measured:** thumbs up/down feedback, versioned prompts, test sets that
  catch regressions when you change a prompt or model, and an automatic
  satisfaction score for every conversation.
- **Low maintenance:** one-line install; updates itself within a day of each
  release.

## First steps

1. **Settings:** add an API key for Claude, OpenAI or Gemini, or use a local
   Ollama model (no key). Add a TypeSafe key to use Jev as the classifier and
   for satisfaction reviews. Check that System status is all green.
2. **Chat Apps → New app:** name, description, model, personality, what's in
   and out of scope, and the refusal message.
3. **Data sources:** upload files or connect a database (use a read-only
   login). For databases, open **Tables** to explain cryptic table and column
   names and hide tables. Tag documents with the SKUs they cover.
   Put business rules (like how part numbers relate) in **Product knowledge**
   on the Settings tab.
4. **Try it:** ask in-scope, off-topic and data questions.
5. **Quality:** add a few test questions and run them; run them again after
   any change to the prompt or model.
6. Turn on **Public chat page** to share the app's link. To check answers
   before visitors see them, turn on **Review answers to anonymous visitors**:
   they wait in the app's **Review** tab, which also shows how often answers
   go out unchanged, how many are waiting, and what the app costs.

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

## Behind a reverse proxy (Caddy)

To serve Chat Builder at its own address on a host that already runs Caddy,
give the installer the address (it keeps port 8080 private to the machine and
turns on secure cookies), then add to your Caddyfile:

```
chat.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy obtains the HTTPS certificate automatically. For an existing install,
set these in `~/chat-builder/.env` and run `chat-builder restart`:

```
PUBLIC_URL=https://chat.example.com
APP_BIND=127.0.0.1
FORWARDED_ALLOW_IPS=*
COOKIE_SECURE=true
```

## People

**People** (admins only) is where you invite people. An invite is a one-time
link you send; they choose their own password. **Admins** manage every app,
key and setting; **members** only chat with the apps you give them. People
signed in by an embedding site are added automatically, and anyone can be
blocked.

## Embed the chat on another site

In an app's **Deploy** tab, turn on embedding, list the sites allowed to show
it, and copy the `<script>` tag into those sites' pages. A chat button appears
in the bottom-right corner. People sign in one of three ways:

- **Signed in by your site** (for internal tools): your site's server signs a
  short-lived token (a JWT, HS256) naming the logged-in person, using the
  app's secret key. No second login. The Deploy tab includes Laravel code.
- **Chat Builder sign-in:** people sign in inside the widget with their
  Chat Builder account.
- **Public:** anonymous visitors.

**Try it on a preview page** in the Deploy tab shows the widget without
touching your site.

## Move to a new server

A **full backup** holds everything: apps, conversations, people, API keys,
documents and their search index (no reprocessing needed). It's one file,
encrypted with a passphrase you choose.

1. On the old server: **Settings → Back up and move**, or
   `chat-builder export backup.cbbackup`.
2. Install Chat Builder on the new server.
3. Copy the file over and run `chat-builder restore backup.cbbackup`. It asks
   for the passphrase, replaces everything in the new install, and starts it
   again. Sign in with your old accounts.

Saved keys and passwords are re-encrypted for the new install, so its `.env`
doesn't need the old `MASTER_KEY`. A backup restores into the same or a newer
Chat Builder version. If the new install uses a different embedding model,
reprocess documents afterwards.

Running from source? Use the same commands with `CB_HOME` set to the
repository folder: `CB_HOME=$PWD deploy/chat-builder restore backup.cbbackup`.

## Move or share an app

**Export** (in the app editor) downloads a `.zip` with the app's settings,
product knowledge, data sources (uploaded files, and database connections
with their table notes) and test set. **Import app** (on the Chat Apps page)
recreates it in any Chat Builder install. Conversations, feedback and the
workspace's provider API keys are not included.

Database passwords and the widget's signing secret are left out unless you
tick **Include database passwords and the widget's signing secret**, which is
meant for moving to a new server; keep that file safe. Without them, enter
the passwords after importing, and update embedding sites with the app's new
signing secret.

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
