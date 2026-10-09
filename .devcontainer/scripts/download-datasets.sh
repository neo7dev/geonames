#!/usr/bin/env bash
# Download GeoNames dumps from geonames.org into gostatic/ so slow-download-server
# (and gostatic) can serve them in development.
#
# Optionally fetches the current daily modifications/deletes pair. GeoNames only publishes
# one pair at a time (the latest day), so its date is read from the directory listing.
#
# Interactive by default. Non-interactive when DATASETS is set:
#   DATASETS=cities15000,cities5000 DAILY=yes download-datasets.sh
#   DATASETS           comma-separated dataset keys, or "all"
#   DAILY              yes/no: also fetch the daily modifications/deletes pair (default no)
#   GEONAMES_DUMP_URL  source directory (default https://download.geonames.org/export/dump/)
set -euo pipefail

BASE_URL="${GEONAMES_DUMP_URL:-https://download.geonames.org/export/dump/}"
BASE_URL="${BASE_URL%/}/"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${ROOT}/gostatic"

KEYS=(cities15000 cities5000 cities1000 cities500 allcountries)
declare -A FILES=(
    [cities15000]=cities15000.zip
    [cities5000]=cities5000.zip
    [cities1000]=cities1000.zip
    [cities500]=cities500.zip
    [allcountries]=allCountries.zip
)

# ENABLED_DATASETS from .env (or .env.dev), lowercased, used as the default selection.
enabled_datasets() {
    local env_file
    for env_file in "${ROOT}/.env" "${ROOT}/.env.dev"; do
        if [ -f "$env_file" ]; then
            grep -iE '^[[:space:]]*ENABLED_DATASETS[[:space:]]*=' "$env_file" \
                | head -1 | sed -E 's/^[^=]*=//' | tr -d '"[:space:]' | tr '[:upper:]' '[:lower:]'
            return
        fi
    done
}

remote_size() {
    curl -sfI "${BASE_URL}$1" | tr -d '\r' | awk -F': ' 'tolower($1) == "content-length" {print $2}'
}

# Daily files listed in the source directory (GeoNames lists only the latest pair).
daily_files() {
    curl -sf "$BASE_URL" | grep -oE '(modifications|deletes)-[0-9]{4}-[0-9]{2}-[0-9]{2}\.txt' | sort -u || true
}

human() {
    numfmt --to=iec --suffix=B "${1:-0}" 2>/dev/null || echo "${1:-?} bytes"
}

# Download $1 into gostatic/. Keeps the server's Last-Modified time (-R) so nginx reports
# the same date as geonames.org, and skips files that are not newer than the local copy.
fetch() {
    local file="$1" target="${DEST}/$1" part="${DEST}/$1.part"
    local args=(-L -R --retry 3 -o "$part" -w '%{http_code}')
    [ -f "$target" ] && args+=(-z "$target")
    if [ -t 1 ]; then args+=(--progress-bar); else args+=(-sS); fi

    local code
    code=$(curl "${args[@]}" "${BASE_URL}${file}") || code="000"
    case "$code" in
        200)
            mv "$part" "$target"
            echo "  ${file}: downloaded ($(human "$(stat -c %s "$target")"))"
            ;;
        304)
            rm -f "$part"
            echo "  ${file}: already up to date"
            ;;
        404)
            rm -f "$part"
            echo "  ${file}: not available"
            return 1
            ;;
        *)
            rm -f "$part"
            echo "  ${file}: download failed (HTTP ${code})" >&2
            return 1
            ;;
    esac
}

mkdir -p "$DEST"
default_selection="$(enabled_datasets)"
selection="${DATASETS:-}"
daily_choice="${DAILY:-}"
listed_daily="$(daily_files)"

if [ -z "$selection" ]; then
    echo "Datasets on ${BASE_URL}"
    for i in "${!KEYS[@]}"; do
        key="${KEYS[$i]}"
        file="${FILES[$key]}"
        status=""
        [ -f "${DEST}/${file}" ] && status="  (in gostatic/)"
        printf "  %d) %-13s %-17s %8s%s\n" "$((i + 1))" "$key" "$file" "$(human "$(remote_size "$file")")" "$status"
    done
    echo
    echo "Enter numbers or names separated by commas, or 'all'."
    read -rp "Datasets to download [${default_selection:-cities15000}]: " selection
    selection="${selection:-${default_selection:-cities15000}}"
    if [ -n "$listed_daily" ]; then
        read -rp "Download the daily modifications/deletes files ($(echo $listed_daily | sed 's/ /, /g'))? [y/N]: " daily_choice
    fi
fi
daily_choice="$(echo "${daily_choice:-no}" | tr '[:upper:]' '[:lower:]')"

case "$daily_choice" in
    y|yes) fetch_daily=1 ;;
    n|no) fetch_daily=0 ;;
    *)
        echo "Invalid answer '${daily_choice}'. Use yes or no." >&2
        exit 1
        ;;
esac

# Resolve the selection to dataset keys.
selected=()
if [ "$(echo "$selection" | tr '[:upper:]' '[:lower:]')" = "all" ]; then
    selected=("${KEYS[@]}")
else
    IFS=',' read -ra items <<< "$selection"
    for item in "${items[@]}"; do
        item="$(echo "$item" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
        [ -z "$item" ] && continue
        if [[ "$item" =~ ^[0-9]+$ ]] && [ "$item" -ge 1 ] && [ "$item" -le "${#KEYS[@]}" ]; then
            item="${KEYS[$((item - 1))]}"
        fi
        if [ -z "${FILES[$item]:-}" ]; then
            echo "Unknown dataset '${item}'. Valid: ${KEYS[*]}" >&2
            exit 1
        fi
        selected+=("$item")
    done
fi

echo "Downloading into ${DEST}"
for key in "${selected[@]}"; do
    fetch "${FILES[$key]}" || exit 1
done

if [ "$fetch_daily" = 1 ]; then
    if [ -z "$listed_daily" ]; then
        echo "  No daily modifications/deletes files listed at ${BASE_URL}"
    fi
    for file in $listed_daily; do
        fetch "$file" || true
    done
fi

echo "Done. slow-download-server serves these files at GEONAMES_URL=http://slow-download-server:80/"
