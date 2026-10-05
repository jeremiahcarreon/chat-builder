#!/usr/bin/env bash
# End-to-end check of a running stack: health, login, system status, and a
# document going through ingestion (Postgres, Ollama, EmbeddingGemma, worker).
#
#   scripts/smoke-test.sh http://localhost:8080 admin@example.com password [--seed|--verify]
#
# --seed   also creates a test app with a document (used before an upgrade)
# --verify checks that the seeded app and document still exist (after an upgrade)

set -euo pipefail

BASE="$1"; EMAIL="$2"; PASSWORD="$3"; MODE="${4:-}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

step() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }
api() { curl -fsS -b "$JAR" -c "$JAR" -H 'content-type: application/json' "$@"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

step "Waiting for the API"
for _ in $(seq 1 60); do curl -fs "$BASE/api/health" >/dev/null 2>&1 && break; sleep 5; done
curl -fsS "$BASE/api/health" || fail "API never became healthy"

step "Logging in"
api -X POST "$BASE/api/auth/login" -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" >/dev/null || fail "login"
curl -fsS -o /dev/null "$BASE/" || fail "dashboard page"

step "Waiting for database, worker and embedding model"
for _ in $(seq 1 90); do
  status="$(api "$BASE/api/system/status")"
  echo "$status" | json "all(s['ok'] for s in d)" | grep -q True && break
  sleep 10
done
echo "$status" | json "'\n'.join(f\"{'ok ' if s['ok'] else 'NOT'} {s['name']}: {s['detail']}\" for s in d)"
echo "$status" | json "all(s['ok'] for s in d)" | grep -q True || fail "services not healthy"

if [ "$MODE" = "--verify" ]; then
  step "Checking data from before the upgrade"
  app_id="$(api "$BASE/api/apps" | json "[a['id'] for a in d if a['name']=='Smoke Test'][0]")" || fail "seeded app missing"
  api "$BASE/api/apps/$app_id/sources" | json "[s['status'] for s in d]" | grep -q ready || fail "seeded document missing"
  echo "Seeded app and document survived the upgrade."
  exit 0
fi

step "Creating an app and uploading a document"
app_id="$(api -X POST "$BASE/api/apps" -d '{"name":"Smoke Test","provider":"ollama","model":"none"}' | json "d['id']")"
doc="$(mktemp --suffix=.txt)"
echo "The XR-500 heat pump needs 24 inches of clearance around the outdoor unit." >"$doc"
curl -fsS -b "$JAR" -F "files=@$doc;filename=manual.txt" "$BASE/api/apps/$app_id/sources/upload" >/dev/null || fail "upload"
for _ in $(seq 1 60); do
  state="$(api "$BASE/api/apps/$app_id/sources" | json "d[0]['status']")"
  [ "$state" = ready ] && break
  [ "$state" = failed ] && fail "document processing failed: $(api "$BASE/api/apps/$app_id/sources" | json "d[0]['error']")"
  sleep 5
done
[ "$state" = ready ] || fail "document never finished processing"
echo "Document processed."

if [ "$MODE" != "--seed" ]; then
  api -X DELETE "$BASE/api/apps/$app_id" >/dev/null || fail "delete app"
fi
step "Smoke test passed"
