#!/bin/sh
set -eu
umask 077
# Fixed isolated database and output directory; never uses a production URL.
export PGPASSWORD="$POSTGRES_PASSWORD"
mkdir -p /backups
while true; do
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  pg_dump -h postgres -U saas -d saas_preproduction --format=custom --file="/backups/saas_preproduction_${stamp}.dump"
  pg_restore --list "/backups/saas_preproduction_${stamp}.dump" >/dev/null
  printf '%s\n' "$stamp" > /backups/last_success.txt
  sleep 86400
done
