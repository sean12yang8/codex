#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
[ "${CONFIRM_RESTORE:-}" = restore ] || { echo 'Set CONFIRM_RESTORE=restore only for the approved destination database.' >&2; exit 1; }
backup_dir="${1:?backup directory required}"
ledger_file="${2:?latest deletion ledger from independent retained storage required}"
[ -f "$ledger_file" ] || { echo 'Restore blocked: current deletion ledger unavailable.' >&2;exit 1; }
(cd "$backup_dir" && sha256sum -c SHA256SUMS)
# A current ledger is mandatory, even when empty; never restore an old ledger over it.
docker compose exec -T app python -c 'import sys,json;[json.loads(line) for line in sys.stdin if line.strip()];print("Ledger format valid")' < "$ledger_file"
docker compose stop worker app
docker compose exec -T db pg_restore -U acr -d acr --clean --if-exists --exit-on-error < "$backup_dir/database.dump"
# The application-data volume (encryption key) must be retained. New-host key
# recovery is an operator step: never overwrite a different live encryption key.
docker compose run --rm --no-deps -T app python -c 'import sys,os;from pathlib import Path;p=Path("/data/deletion-ledger.jsonl");p.write_bytes(sys.stdin.buffer.read());os.chmod(p,0o600)' < "$ledger_file"
# Startup replays deletion tombstones BEFORE serving any restored data.
docker compose up -d
