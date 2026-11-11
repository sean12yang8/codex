#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
backup_root="${1:-/opt/azurelens/backups}"
mkdir -p "$backup_root"
backup_dir="$backup_root/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$backup_dir"
docker compose exec -T db pg_dump -U acr -d acr -Fc > "$backup_dir/database.dump"
docker compose exec -T app python -c 'import tarfile,sys;from pathlib import Path;root=Path("/data");t=tarfile.open(fileobj=sys.stdout.buffer,mode="w|gz");[(t.add(root/name,arcname=name)) for name in ["credential.key","deletion-ledger.jsonl"] if (root/name).exists()];t.close()' > "$backup_dir/keys-and-ledger.tar.gz"
(cd "$backup_dir" && sha256sum database.dump keys-and-ledger.tar.gz > SHA256SUMS)
test -s "$backup_dir/database.dump"
printf 'Backup saved with mode 0700: %s\n' "$backup_dir"
