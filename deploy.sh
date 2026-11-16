#!/usr/bin/env bash
set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
FUNCTION_NAME="realm-status"

RUNTIME="python314"
MEMORY="128m"

FUNCTION_TIMEOUT="30s"

FOLDER_ID="b1gs8d5lqs5n7dhd3ntf"  # default
SERVICE_ACCOUNT_ID="ajee4eliqejv4c2fem3c"  # gistrec

# ─── Environment variables ────────────────────────────────────────────────────
ENV_ARGS=(
    --environment MYSQL_HOST=ru.mysql.gistrec.cloud
    --environment MYSQL_PORT=3306
    --environment MYSQL_USER=realmctl
    --environment MYSQL_DB=realmctl
    --environment TELEGRAM_CHAT_ID=-1003410760885
    --environment STARDEW_API_BASE_URL=http://sv.makstashkevich.com:8080
    --secret environment-variable=TELEGRAM_BOT_TOKEN,id=e6q8o67amdd5s6uuq8jt,key=telegram-token
    --secret environment-variable=MYSQL_PASSWORD,id=e6q23o2nvde6ho5h5lcs,key=mysql-password
    --secret environment-variable=STARDEW_API_TOKEN,id=e6q4s66e35eqhkhoi3pr,key=stardew-api-token
)
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$SCRIPT_DIR/.dist"
ZIP_PATH="$DIST_DIR/function.zip"

# ─── Helpers ──────────────────────────────────────────────────────────────────
log()  { echo "[deploy] $*"; }
err()  { echo "[deploy] ERROR: $*" >&2; exit 1; }

check_deps() {
    command -v yc  &>/dev/null || err "'yc' CLI not found. Install it from https://cloud.yandex.com/docs/cli/"
    command -v zip &>/dev/null || err "'zip' not found. Install it via 'brew install zip' or your package manager."
}

resolve_function_id() {
    local name="$1"
    yc serverless function get --name "$name" --folder-id "$FOLDER_ID" --format json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || true
}

create_function_if_missing() {
    local name="$1"
    local id
    id=$(resolve_function_id "$name")
    if [[ -z "$id" ]]; then
        log "Creating function '$name'..."
        yc serverless function create --name "$name" --folder-id "$FOLDER_ID" --format json \
            | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"
    else
        log "Function '$name' already exists (id=$id)"
        echo "$id"
    fi
}

deploy_version() {
    local name="$1"
    local entrypoint="$2"
    local timeout="$3"

    log "Deploying version for '$name' (entrypoint: $entrypoint)..."

    local args=(
        --function-name      "$name"
        --folder-id          "$FOLDER_ID"
        --service-account-id "$SERVICE_ACCOUNT_ID"
        --runtime            "$RUNTIME"
        --entrypoint         "$entrypoint"
        --memory             "$MEMORY"
        --execution-timeout  "$timeout"
        --source-path        "$ZIP_PATH"
        "${ENV_ARGS[@]}"
    )

    yc serverless function version create "${args[@]}"
    log "Done: '$name'"
}

# ─── Main ─────────────────────────────────────────────────────────────────────
main() {
    check_deps

    log "Packaging source..."
    rm -rf "$DIST_DIR"
    mkdir -p "$DIST_DIR"

    # Include only runtime-relevant files; exclude venvs, caches, etc.
    (
        cd "$SCRIPT_DIR"
        zip -r "$ZIP_PATH" . \
            --exclude "*.git*" \
            --exclude ".dist/*" \
            --exclude ".venv/*" \
            --exclude "venv/*" \
            --exclude ".ruff_cache/*" \
            --exclude "__pycache__/*" \
            --exclude "*.pyc" \
            --exclude "*.pyo" \
            --exclude "*.crt" \
            --exclude "deploy.sh" \
            --exclude "README.md" \
            --exclude "pyvenv.cfg"
    )
    log "Archive: $ZIP_PATH ($(du -sh "$ZIP_PATH" | cut -f1))"

    # Function deploy is owned by Terraform (infra: terraform/yandex/functions.tf).
    log "Packaged only — deploy via terraform apply (infra repo)."
}

main "$@"
