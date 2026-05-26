#!/bin/bash
set -euo pipefail

# Daily PostgreSQL backup
BACKUP_DIR=/opt/betty/backups
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)

echo "Starting backup: $DATE"

docker compose --env-file /opt/betty/.env -f /opt/betty/backend/docker-compose.yml exec -T postgres \
    pg_dump -U betty betty | gzip > "$BACKUP_DIR/betty_$DATE.sql.gz"

docker compose --env-file /opt/betty/.env -f /opt/betty/backend/docker-compose.yml exec -T postgres \
    pg_dump -U betty market | gzip > "$BACKUP_DIR/market_$DATE.sql.gz"

# Keep last 7 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete

echo "Backup complete: $DATE (betty: $(du -h "$BACKUP_DIR/betty_$DATE.sql.gz" | cut -f1), market: $(du -h "$BACKUP_DIR/market_$DATE.sql.gz" | cut -f1))"
