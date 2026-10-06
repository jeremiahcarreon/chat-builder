# Releasing and compatibility

Every installation updates itself to the latest release within a day of it
being published (or immediately, from the **Update now** button or
`chat-builder update`). Installs may jump several versions at once. These
rules keep that safe.

## How an update works

1. The updater container checks GitHub for a newer release every 30 minutes.
2. The dashboard shows a banner; the update installs when the admin clicks
   **Update now**, or automatically 24 hours after the release was first seen.
3. The updater backs up the database, sets `CB_VERSION` in `.env`, pulls the
   new prebuilt images, and recreates the `api` and `worker` containers from
   the owner's own `docker-compose.yml`.
4. The api container runs database migrations on start.
5. If the new version isn't healthy within 4 minutes, `CB_VERSION` is set back
   and the previous version is started again.

Nothing is built on the user's machine, and the user's `docker-compose.yml`
and `.env` customizations are never modified, apart from `CB_VERSION`.

## Publishing a release

1. Make sure CI is green on `main`.
2. Update `VERSION` (semantic versioning: `MAJOR.MINOR.PATCH`) and commit.
3. Tag and push:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

The Release workflow runs all CI checks, builds the `amd64` and `arm64`
images, pushes them to `ghcr.io/jeremiahcarreon/chat-builder` and
`ghcr.io/jeremiahcarreon/chat-builder-updater`, and creates the GitHub
release. Installs see it on their next check.

Never move or delete a published tag: installs may already be running it.

## Compatibility rules

Any release must work for an install that is several versions behind, and the
previous release must keep working if an update is rolled back.

**Database**

- Migrations only add: new tables, new nullable columns, or columns with a
  server default. Never rename or drop a column or table in the same release
  that stops using it.
- To remove something: stop using it in one release, and drop it no sooner
  than two minor releases later (expand, then contract).
- Never edit a migration that has been released. Fix forward with a new one.
- Every migration has a working `downgrade()`. CI checks this.
- Long data migrations must be safe to interrupt and re-run.

**Configuration**

- New settings must have defaults in code (`app/config.py`). Existing `.env`
  files don't get new variables.
- A release must never require changes to `docker-compose.yml`. Installs keep
  the compose file from their original install, possibly customized. New
  behavior belongs in the image, configured through defaults.
- Keep the service names `api`, `worker`, `postgres`, `ollama` and `updater`,
  and the health endpoint `/api/health`. The update procedure in older
  releases depends on them.

**Background jobs and stored data**

- Don't rename background task names; jobs queued before an update still run
  after it.
- Stored JSON (message metadata, data source config, settings) must stay
  readable: add keys, don't change the meaning of existing ones.

**Updater**

- `deploy/chat-builder` runs inside the updater of the *previous* release when
  installing a new one. Changes to the update steps take effect one release
  later, so keep them backward compatible too.

## What CI checks

On every push to `main`, on pull requests, and on every release tag. Day-to-day
work happens on branches, which CI doesn't run on until a pull request is opened.

- Backend tests and the frontend build.
- Migrations apply to an empty database, match the models (`alembic check`),
  and the latest one downgrades and re-applies cleanly.
- Shell scripts pass ShellCheck.
- The real Docker images build, the full stack starts, and a document goes
  through ingestion (Postgres, Ollama, EmbeddingGemma, worker).
- **Upgrade test:** the previous published release is started with data, then
  upgraded to the new build; the data must survive and the stack must pass the
  smoke test.
- **End-to-end tests** (`scripts/e2e-test.py`), through the HTTP API with
  scripted stand-in models (`TEST_MODELS=true`) so answers are predictable and
  no API keys are needed: answers from an uploaded document; a live Postgres
  "ERP" connected with a read-only login and queried by the model; writes and
  forbidden functions refused; out-of-scope and manipulative messages blocked;
  feedback saved; the widget's frame policy, signed sign-in (automatic
  accounts; wrong-secret, expired, wrong-app, over-long and incomplete tokens
  rejected) and blocking; members limited to their apps; export and import.
- A full encrypted backup restores into the same install with its data intact.

To run the end-to-end tests against any running install started with
`TEST_MODELS=true` (never set that on a real install):

```sh
psql -U postgres -f scripts/e2e-erp.sql     # the sample ERP, on a server the install can reach
scripts/e2e-test.py http://localhost:8080 ADMIN_EMAIL ADMIN_PASSWORD --erp-host HOST --erp-port 5432
```
