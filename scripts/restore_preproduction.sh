#!/bin/sh
set -eu
umask 077
: "${BACKUP_FILE:?Pass an absolute /backups/saas_preproduction_*.dump path}"
case "$BACKUP_FILE" in
  /backups/saas_preproduction_*.dump) ;;
  *) echo 'Only isolated preproduction dumps are accepted' >&2; exit 2 ;;
esac
case "$BACKUP_FILE" in *..*) exit 2 ;; esac
export PGPASSWORD="$POSTGRES_PASSWORD"
target="saas_restore_$(date -u +%Y%m%d%H%M%S)"
createdb -h postgres -U saas "$target"
pg_restore -h postgres -U saas -d "$target" --no-owner --exit-on-error "$BACKUP_FILE"
psql -h postgres -U saas -d "$target" -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version; SELECT count(*) AS projects FROM implementation_projects; SELECT count(*) AS actual_members FROM saas_members; SELECT count(*) AS artifacts FROM project_artifacts;'
printf 'Restored to %s. Database retained for verification. No existing database overwritten.\n' "$target"
