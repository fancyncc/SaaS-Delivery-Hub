"""Independent login accounts and optional contact details."""
from contextlib import contextmanager
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from alembic import op

revision = "0015_username_profile"
down_revision = "0014_open_registration"
branch_labels = None
depends_on = None


@contextmanager
def preserve_sqlite_expression_indexes(bind):
    if bind.dialect.name != "sqlite":
        yield
        return
    # SQLite reflection cannot represent expression indexes. Preserve their
    # exact DDL, including custom indexes, across the users table rebuild.
    # A SAVEPOINT makes index removal, table replacement and restoration atomic
    # even when sqlite3 has not started a transaction for the preceding DDL.
    quote = bind.dialect.identifier_preparer.quote
    indexes = []
    for name, sql in bind.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='users' AND sql IS NOT NULL"
    ).all():
        if any(row[1] == -2 for row in bind.exec_driver_sql(f"PRAGMA index_xinfo({quote(name)})")):
            indexes.append((name, sql))
    with bind.begin_nested():
        for name, _ in indexes:
            bind.exec_driver_sql(f"DROP INDEX {quote(name)}")
        yield
        for _, sql in indexes:
            bind.exec_driver_sql(sql)


def upgrade():
    bind = op.get_bind()
    columns = {c["name"]: c for c in sa.inspect(bind).get_columns("users")}
    rows = list(bind.execute(sa.text("SELECT id" + (", username" if "username" in columns else "") + " FROM users")).mappings())
    names = {}
    assignments = []
    for row in rows:
        username = (row.get("username") or f"u_{uuid5(NAMESPACE_URL, str(row['id'])).hex}").strip().lower()
        if username in names:
            raise RuntimeError(f"Duplicate normalized usernames: {names[username]}, {row['id']}")
        names[username] = row["id"]
        assignments.append({"id": row["id"], "username": username})
    with op.batch_alter_table("users") as batch:
        if "username" not in columns:
            batch.add_column(sa.Column("username", sa.String(40), nullable=True))
        if "phone" not in columns:
            batch.add_column(sa.Column("phone", sa.String(20), nullable=True))
    for assignment in assignments:
        bind.execute(sa.text("UPDATE users SET username=:username WHERE id=:id"), assignment)
    with preserve_sqlite_expression_indexes(bind):
        unique_columns = {tuple(c["column_names"]) for c in sa.inspect(bind).get_unique_constraints("users")}
        with op.batch_alter_table("users", reflect_kwargs={"resolve_fks": False}) as batch:
            batch.alter_column("username", existing_type=sa.String(40), nullable=False)
            batch.alter_column("email", existing_type=sa.String(160), nullable=True)
            if ("username",) not in unique_columns:
                batch.create_unique_constraint("uq_users_username", ["username"])
            if ("phone",) not in unique_columns:
                batch.create_unique_constraint("uq_users_phone", ["phone"])
    # Legacy databases may not have these indexes yet.
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_normalized ON users (lower(trim(email)))")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_normalized ON users (lower(trim(username)))")
    if "user_id" not in {c["name"] for c in sa.inspect(bind).get_columns("email_verifications")}:
        with op.batch_alter_table("email_verifications", reflect_kwargs={"resolve_fks": False}) as batch:
            batch.add_column(sa.Column("user_id", sa.String(36), nullable=True))
            batch.create_foreign_key("fk_email_verification_user", "users", ["user_id"], ["id"])


def downgrade():
    raise RuntimeError("Username-only accounts may lack email. Restore a backup instead of dropping login identities.")
