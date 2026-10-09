#!/usr/bin/env bash
# Validate the CLOUD-SAFE NEXORA development environment.
#
# Mirrors .github/workflows/validate.yml (python + web jobs) so a fresh
# Claude Code Web / cloud session can prove a clone is healthy without chat
# history or a local D:\NEXORA checkout.
#
# Toolchain targets (same as CI): Python 3.13, uv 0.12.15, Node 24 + npm,
# optional disposable PostgreSQL (CI uses 18).
#
# Safety: this script never starts PROD, never connects to MT5 or a broker,
# never reads production journals/checkpoints and never uses a production
# DSN. It scrubs NEXORA_*, MT5_*, BROKER_* and PG* variables from its own
# environment before running anything. The only PostgreSQL it will touch is
# a throwaway cluster it creates itself (--postgres) or an explicitly opted-in
# loopback test DSN (--postgres-dsn-from-env).
#
# Usage: scripts/validate_cloud.sh [options]
#   --postgres               Run PG integration tests against a disposable
#                            local cluster created and destroyed by this script.
#   --postgres-dsn-from-env  Use an existing NEXORA_TEST_POSTGRES_DSN instead
#                            (must point at localhost/127.0.0.1/::1 or a socket).
#   --no-install             Check tool versions only; do not provision the
#                            pinned uv or Node 24 into the user cache.
#   --skip-python            Skip the python job.
#   --skip-web               Skip the web job.
#   -h, --help               Show this help.

set -euo pipefail

UV_VERSION="0.12.15"
PYTHON_VERSION="3.13"
NODE_MAJOR="24"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/nexora-cloud"

want_pg=0
pg_from_env=0
install=1
run_python=1
run_web=1

usage() { sed -n '2,/^$/{s/^# \{0,1\}//;p}' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --postgres) want_pg=1 ;;
    --postgres-dsn-from-env) want_pg=1; pg_from_env=1 ;;
    --no-install) install=0 ;;
    --skip-python) run_python=0 ;;
    --skip-web) run_web=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

log() { printf '\n==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

results=()
step() {
  local name="$1"; shift
  log "$name"
  if "$@"; then
    results+=("PASS  $name")
  else
    results+=("FAIL  $name")
    summary
    exit 1
  fi
}
summary() {
  log "Summary"
  printf '%s\n' "${results[@]}"
}

# --- environment isolation -------------------------------------------------

test_dsn="${NEXORA_TEST_POSTGRES_DSN:-}"
for var in $(compgen -e); do
  case "$var" in
    NEXORA_*|MT5_*|BROKER_*|PG*) unset "$var" ;;
  esac
done

cd "$ROOT"

# --- toolchain -------------------------------------------------------------

log "Toolchain"

python_bin="$(command -v "python${PYTHON_VERSION}" || true)"
[ -n "$python_bin" ] || fail "python${PYTHON_VERSION} not found"
echo "python: $("$python_bin" --version)"

uv_bin="$(command -v uv || true)"
uv_current=""
[ -n "$uv_bin" ] && uv_current="$("$uv_bin" --version | awk '{print $2}')"
if [ "$uv_current" != "$UV_VERSION" ]; then
  pinned_uv="$CACHE_DIR/uv-$UV_VERSION/bin/uv"
  if [ ! -x "$pinned_uv" ]; then
    [ "$install" = 1 ] || fail "uv $UV_VERSION required (found '${uv_current:-none}'); rerun without --no-install"
    echo "installing uv $UV_VERSION into $CACHE_DIR (found '${uv_current:-none}')"
    "$python_bin" -m venv "$CACHE_DIR/uv-$UV_VERSION"
    "$CACHE_DIR/uv-$UV_VERSION/bin/pip" install -q "uv==$UV_VERSION"
  fi
  uv_bin="$pinned_uv"
fi
echo "uv: $("$uv_bin" --version)"

if [ "$run_web" = 1 ]; then
  node_major="$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || true)"
  if [ "$node_major" != "$NODE_MAJOR" ]; then
    nvm_dir="${NVM_DIR:-}"
    [ -z "$nvm_dir" ] && for d in "$HOME/.nvm" /opt/nvm; do [ -s "$d/nvm.sh" ] && nvm_dir="$d" && break; done
    [ -n "$nvm_dir" ] || fail "Node $NODE_MAJOR required (found '${node_major:-none}') and nvm not available"
    export NVM_DIR="$nvm_dir"
    # shellcheck disable=SC1091
    set +u; . "$nvm_dir/nvm.sh"
    if ! nvm use "$NODE_MAJOR" >/dev/null 2>&1; then
      [ "$install" = 1 ] || { set -u; fail "Node $NODE_MAJOR required (found '${node_major:-none}'); rerun without --no-install"; }
      nvm install "$NODE_MAJOR" >/dev/null
      nvm use "$NODE_MAJOR" >/dev/null
    fi
    set -u
  fi
  echo "node: $(node --version)  npm: $(npm --version)"
fi

# --- disposable PostgreSQL -------------------------------------------------

pg_tmp=""
cleanup() {
  if [ -n "$pg_tmp" ]; then
    as_pg "$pg_bindir/pg_ctl" -D "$pg_tmp/data" -m immediate stop >/dev/null 2>&1 || true
    rm -rf "$pg_tmp"
  fi
}
trap cleanup EXIT

as_pg() {
  if [ "$(id -u)" = 0 ]; then runuser -u postgres -- "$@"; else "$@"; fi
}

if [ "$want_pg" = 1 ] && [ "$run_python" = 1 ]; then
  log "PostgreSQL"
  if [ "$pg_from_env" = 1 ]; then
    [ -n "$test_dsn" ] || fail "--postgres-dsn-from-env given but NEXORA_TEST_POSTGRES_DSN is empty"
    host="$(printf '%s' "$test_dsn" | sed -nE 's/.*host=([^ ]+).*/\1/p; s#^postgres(ql)?://[^@]*@?([^:/?]+).*#\2#p' | head -1)"
    case "$host" in
      localhost|127.0.0.1|::1|/*|"") ;;
      *) fail "refusing non-loopback test DSN host '$host'" ;;
    esac
    export NEXORA_TEST_POSTGRES_DSN="$test_dsn"
    echo "using loopback test DSN from environment (host=${host:-socket})"
  else
    pg_bindir="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)"
    [ -z "$pg_bindir" ] && pg_bindir="$(dirname "$(command -v initdb || echo /nonexistent/initdb)")"
    [ -x "$pg_bindir/initdb" ] || fail "--postgres requested but initdb not found"
    pg_ver="$("$pg_bindir/postgres" --version | awk '{print $3}')"
    [ "${pg_ver%%.*}" = 18 ] || echo "note: CI uses PostgreSQL 18; using local $pg_ver"
    pg_tmp="$(mktemp -d)"
    chmod 755 "$pg_tmp"
    [ "$(id -u)" = 0 ] && chown postgres "$pg_tmp"
    pg_port="$("$python_bin" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
    # Force UTF8 like the CI postgres image: a C-locale initdb defaults to
    # SQL_ASCII, where psycopg returns bytes and the journal tests fail.
    as_pg "$pg_bindir/initdb" -D "$pg_tmp/data" -U postgres --auth=trust -E UTF8 --no-locale >/dev/null
    as_pg "$pg_bindir/pg_ctl" -D "$pg_tmp/data" -l "$pg_tmp/pg.log" -w \
      -o "-c listen_addresses=127.0.0.1 -c port=$pg_port -c unix_socket_directories=$pg_tmp -c fsync=off" start >/dev/null
    as_pg "$pg_bindir/createdb" -h 127.0.0.1 -p "$pg_port" -U postgres nexora_test
    export NEXORA_TEST_POSTGRES_DSN="host=127.0.0.1 port=$pg_port dbname=nexora_test user=postgres"
    echo "disposable PostgreSQL $pg_ver on 127.0.0.1:$pg_port (destroyed on exit)"
  fi
fi

# --- python job (mirrors CI) -----------------------------------------------

if [ "$run_python" = 1 ]; then
  export UV_PYTHON="$python_bin"
  step "uv sync --locked" "$uv_bin" sync --locked --extra api --extra postgres
  step "pytest" "$uv_bin" run --no-sync pytest -q
  step "ruff" "$uv_bin" run --no-sync ruff check .
  step "mypy" "$uv_bin" run --no-sync mypy
  step "recovery drill (synthetic, isolated)" "$uv_bin" run --no-sync python scripts/recovery_drill.py
  step "git diff --check" git diff --check
fi

# --- web job (mirrors CI) --------------------------------------------------

if [ "$run_web" = 1 ]; then
  # Chart regression tests read pre-UI baseline commits; CI uses fetch-depth 0.
  if [ "$(git rev-parse --is-shallow-repository)" = true ]; then
    log "Unshallowing clone for web baseline tests"
    git fetch --quiet --unshallow origin
  fi
  step "npm ci" npm --prefix apps/web ci --no-audit --no-fund
  step "web test" npm --prefix apps/web test
  step "web lint" npm --prefix apps/web run lint
  step "web typecheck" npm --prefix apps/web run typecheck
  step "web build" npm --prefix apps/web run build
fi

summary
[ "$want_pg" = 1 ] || echo "note: PostgreSQL integration tests skipped (rerun with --postgres)"
echo "CLOUD-SAFE validation PASSED"
