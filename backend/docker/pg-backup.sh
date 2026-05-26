#!/bin/bash
set -euo pipefail

# Daily PostgreSQL backup
BACKUP_DIR=/opt/arnold/backups
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)

echo "Starting backup: $DATE"

docker compose -f /opt/arnold/docker-compose.yml exec -T postgres \
    pg_dump -U arnold arnold | gzip > "$BACKUP_DIR/arnold_$DATE.sql.gz"

docker compose -f /opt/arnold/docker-compose.yml exec -T postgres \
    pg_dump -U arnold market | gzip > "$BACKUP_DIR/market_$DATE.sql.gz"

# Keep last 7 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete

echo "Backup complete: $DATE (arnold: $(du -h "$BACKUP_DIR/arnold_$DATE.sql.gz" | cut -f1), market: $(du -h "$BACKUP_DIR/market_$DATE.sql.gz" | cut -f1))"
