#!/usr/bin/env bash
# Chat Builder installer.
#
#   curl -fsSL https://raw.githubusercontent.com/jeremiahcarreon/chat-builder/main/install.sh | bash
#
# Options (environment variables):
#   CB_HOME=/path        Install location (default: ~/chat-builder)
#   CB_VERSION=x.y.z     Install a specific release (default: latest)
#   CB_PORT=8080         Port for the web dashboard
#   CB_PUBLIC_URL=...    Public address behind a reverse proxy, e.g. https://chat.example.com
#   CB_ADMIN_EMAIL=...   Admin login email (prompted if not set)

set -euo pipefail

REPO="${CB_REPO:-jeremiahcarreon/chat-builder}"
CB_HOME="${CB_HOME:-$HOME/chat-builder}"
PORT="${CB_PORT:-8080}"

say() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# When piped into bash, stdin is this script, so prompts read from the terminal.
ask() {
  local prompt="$1" default="${2:-}" reply
  if [ -r /dev/tty ]; then
    read -r -p "$prompt" reply </dev/tty || true
  fi
  echo "${reply:-$default}"
}

ask_secret() {
  local prompt="$1" reply=""
  if [ -r /dev/tty ]; then
    read -r -s -p "$prompt" reply </dev/tty || true
    echo >/dev/tty
  fi
  echo "$reply"
}

random_hex() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
random_b64() { head -c "$1" /dev/urandom | base64 | tr -d '\n'; }

say "Chat Builder installer"
say ""

# -------------------------------------------------------------- Docker

if ! command -v docker >/dev/null 2>&1; then
  case "$(uname -s)" in
    Linux)
      answer="$(ask "Docker is required but not installed. Install it now with Docker's official script (uses sudo)? [y/N] " n)"
      case "$answer" in
        y|Y|yes) curl -fsSL https://get.docker.com | sh
                 say "Docker installed. You may need to log out and back in to use it without sudo." ;;
        *) die "Install Docker (https://docs.docker.com/engine/install/), then run this again." ;;
      esac
      ;;
    Darwin) die "Install Docker Desktop (https://www.docker.com/products/docker-desktop/), start it, then run this again." ;;
    *) die "Unsupported system. Install Docker, then run this again." ;;
  esac
fi
docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is required (https://docs.docker.com/compose/install/)."
docker info >/dev/null 2>&1 || die "Can't reach Docker. Start it, and make sure this user can run 'docker ps'."

# -------------------------------------------------------------- Version

if [ -z "${CB_VERSION:-}" ]; then
  CB_VERSION="$(curl -fsSL --max-time 20 "https://api.github.com/repos/$REPO/releases/latest" \
    | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name": *"v?([^"]+)".*/\1/')" \
    || die "Couldn't find the latest release on GitHub."
fi
[[ "$CB_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Unexpected version '$CB_VERSION'."
say "Installing version $CB_VERSION into $CB_HOME"

mkdir -p "$CB_HOME"
CB_HOME="$(cd "$CB_HOME" && pwd)"
raw="https://raw.githubusercontent.com/$REPO/v$CB_VERSION"
# An existing compose file holds the owner's customizations; never replace it.
if [ ! -f "$CB_HOME/docker-compose.yml" ]; then
  curl -fsSL "$raw/deploy/docker-compose.yml" -o "$CB_HOME/docker-compose.yml" || die "Download failed."
fi
curl -fsSL "$raw/deploy/chat-builder" -o "$CB_HOME/chat-builder" || die "Download failed."
chmod 755 "$CB_HOME/chat-builder"

# -------------------------------------------------------------- Settings

ENV_FILE="$CB_HOME/.env"
if [ -f "$ENV_FILE" ]; then
  say "Keeping existing settings in $ENV_FILE."
  sed -i.bak -E "s|^CB_VERSION=.*|CB_VERSION=$CB_VERSION|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
else
  email="${CB_ADMIN_EMAIL:-}"
  while [[ ! "$email" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; do
    email="$(ask "Admin email: ")"
    [ -r /dev/tty ] || die "Set CB_ADMIN_EMAIL when installing without a terminal."
  done
  password="$(ask_secret "Admin password (at least 12 characters, blank to generate one): ")"
  generated=""
  if [ -z "$password" ]; then
    password="$(random_hex 9)"
    generated=1
  fi
  [ "${#password}" -ge 12 ] || die "The password must be at least 12 characters."

  public_url="${CB_PUBLIC_URL:-}"
  if [ -z "$public_url" ]; then
    public_url="$(ask "Public address if it'll sit behind a reverse proxy, like https://chat.example.com (Enter to skip): ")"
  fi
  public_url="${public_url%/}"
  # Behind a proxy: keep the port private to this machine, trust the proxy's
  # client-address headers, and use secure cookies over https.
  if [ -n "$public_url" ]; then bind=127.0.0.1; forwarded="*"; else bind=0.0.0.0; forwarded=127.0.0.1; fi
  case "$public_url" in https://*) secure=true ;; *) secure=false ;; esac

  umask 077
  cat >"$ENV_FILE" <<EOF
# Chat Builder settings. Back up this file: MASTER_KEY decrypts stored API keys
# and database passwords, and can't be recovered if lost.
CB_VERSION=$CB_VERSION
CB_REPO=$REPO
CB_HOME=$CB_HOME
APP_PORT=$PORT
APP_BIND=$bind
PUBLIC_URL=$public_url
FORWARDED_ALLOW_IPS=$forwarded
ADMIN_EMAIL=$email
ADMIN_PASSWORD=$password
POSTGRES_PASSWORD=$(random_hex 24)
MASTER_KEY=$(random_b64 32)
COOKIE_SECURE=$secure
EOF
  umask 022
fi

# -------------------------------------------------------------- CLI

link=""
for dir in /usr/local/bin "$HOME/.local/bin"; do
  if [ -d "$dir" ] && [ -w "$dir" ]; then link="$dir/chat-builder"; break; fi
done
if [ -z "$link" ]; then
  mkdir -p "$HOME/.local/bin"
  link="$HOME/.local/bin/chat-builder"
fi
# A small wrapper so the command works from anywhere and finds this install.
cat >"$link" <<EOF
#!/usr/bin/env bash
export CB_HOME="$CB_HOME"
exec "$CB_HOME/chat-builder" "\$@"
EOF
chmod 755 "$link"
case ":$PATH:" in
  *":$(dirname "$link"):"*) ;;
  *) say "Note: add $(dirname "$link") to your PATH to use the 'chat-builder' command." ;;
esac

# -------------------------------------------------------------- Start

say "Downloading and starting Chat Builder (the first start takes a few minutes)..."
export CB_HOME
"$CB_HOME/chat-builder" start >/dev/null

for _ in $(seq 1 60); do
  if curl -fs "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
    say ""
    say "Chat Builder is running: http://localhost:$PORT"
    public="$(grep -E '^PUBLIC_URL=' "$ENV_FILE" | cut -d= -f2-)"
    [ -n "$public" ] && say "Once your reverse proxy points $public at localhost:$PORT, use that address."
    say "Log in with: $(grep -E '^ADMIN_EMAIL=' "$ENV_FILE" | cut -d= -f2-)"
    [ -n "${generated:-}" ] && say "Generated password: $password  (keep it somewhere safe)"
    say ""
    say "Manage it with: chat-builder status | logs | stop | start | update"
    say "Updates are installed automatically within a day of each release."
    exit 0
  fi
  sleep 5
done
die "Chat Builder didn't respond on port $PORT. Check: chat-builder logs api"
