#!/bin/sh
set -eu
umask 077
: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_DIR:?BACKUP_DIR is required}"
mkdir -p "$BACKUP_DIR"
pg_dump --format=custom --dbname="$DATABASE_URL" --file="$BACKUP_DIR/saas_agent_$(date +%Y%m%d_%H%M%S).dump"
find "$BACKUP_DIR" -type f -name 'saas_agent_*.dump' -mtime +30 -delete
