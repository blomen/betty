#!/bin/bash
# Daily PostgreSQL backup
BACKUP_DIR=/opt/firev/backups
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d_%H%M%S)

docker compose -f /opt/firev/docker-compose.yml exec -T postgres \
    pg_dump -U firev firev | gzip > $BACKUP_DIR/firev_$DATE.sql.gz

docker compose -f /opt/firev/docker-compose.yml exec -T postgres \
    pg_dump -U firev market | gzip > $BACKUP_DIR/market_$DATE.sql.gz

# Keep last 7 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

echo "Backup complete: $DATE"
