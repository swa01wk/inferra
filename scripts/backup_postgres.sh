#!/usr/bin/env bash
# scripts/backup_postgres.sh
#
# Daily PostgreSQL backup for Inferra V1.
# Dumps the inferra database, gzips it, uploads to S3, and prunes old local copies.
#
# Usage (manually or via cron):
#   POSTGRES_HOST=localhost POSTGRES_USER=inferra S3_BACKUP_BUCKET=inferra-backups \
#     bash scripts/backup_postgres.sh
#
# Cron example (2 AM daily):
#   0 2 * * * POSTGRES_HOST=localhost POSTGRES_USER=inferra S3_BACKUP_BUCKET=inferra-backups \
#     /workspace/inference-platform/scripts/backup_postgres.sh >> /workspace/logs/backup.log 2>&1

set -euo pipefail

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-inferra}"
POSTGRES_DB="${POSTGRES_DB:-inferra}"
PGPASSWORD="${PGPASSWORD:-}"          # set via env or Docker secret
S3_BACKUP_BUCKET="${S3_BACKUP_BUCKET:-}"
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-/workspace/backups/postgres}"
RETAIN_LOCAL_DAYS="${RETAIN_LOCAL_DAYS:-7}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
FILENAME="inferra_${TIMESTAMP}.sql.gz"
DEST="${LOCAL_BACKUP_DIR}/${FILENAME}"

echo "[$(date -u +%FT%TZ)] Starting backup → ${DEST}"

mkdir -p "${LOCAL_BACKUP_DIR}"

# ── Dump ──────────────────────────────────────────────────────────────────────
PGPASSWORD="${PGPASSWORD}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    "${POSTGRES_DB}" \
  | gzip -9 > "${DEST}"

SIZE="$(du -sh "${DEST}" | cut -f1)"
echo "[$(date -u +%FT%TZ)] Dump complete — ${SIZE} → ${DEST}"

# ── Upload to S3 (optional) ───────────────────────────────────────────────────
if [[ -n "${S3_BACKUP_BUCKET}" ]]; then
    S3_KEY="postgres/${FILENAME}"
    echo "[$(date -u +%FT%TZ)] Uploading to s3://${S3_BACKUP_BUCKET}/${S3_KEY} …"
    aws s3 cp "${DEST}" "s3://${S3_BACKUP_BUCKET}/${S3_KEY}" \
        --storage-class STANDARD_IA
    echo "[$(date -u +%FT%TZ)] Upload complete."
else
    echo "[$(date -u +%FT%TZ)] S3_BACKUP_BUCKET not set — skipping S3 upload."
fi

# ── Prune old local copies ────────────────────────────────────────────────────
echo "[$(date -u +%FT%TZ)] Pruning local backups older than ${RETAIN_LOCAL_DAYS} days…"
find "${LOCAL_BACKUP_DIR}" -name "inferra_*.sql.gz" -mtime "+${RETAIN_LOCAL_DAYS}" -delete

echo "[$(date -u +%FT%TZ)] Backup finished successfully."
