"""Database-level invariants for populated SQLite migration roundtrips."""
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from backend.models import KnowledgeDocument, Project, Tenant, User


def seed_migration_records(connection):
    connection.execute(User.__table__.insert().values(
        id="actor", username="actor", email="actor@example.test",
        display_name="Migration actor", password_hash="test-only",
    ))
    # Multiple NULL contacts must remain valid; these also provide UPDATE targets.
    for number in (1, 2):
        connection.execute(User.__table__.insert().values(
            id=f"optional-{number}", username=f"optional-{number}", email=None,
            display_name="Optional contact", password_hash="test-only",
        ))
    connection.execute(Tenant.__table__.insert().values(id="tenant", name="Migration", slug="migration"))
    connection.execute(Project.__table__.insert().values(
        id="project", tenant_id="tenant", name="Migration", customer_name="Customer", created_by="actor",
    ))
    connection.execute(KnowledgeDocument.__table__.insert().values(
        id="document", tenant_id="tenant", project_id="project", title="原始资料", version=1,
        module="migration", source="source.md", license="internal", body="迁移前的原始内容",
    ))


def user_index_ddl(connection):
    return dict(connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='users' AND sql IS NOT NULL"
    ).all())


def assert_identity_integrity(connection, expected_indexes):
    assert {'uq_users_email_normalized', 'uq_users_username_normalized'} <= expected_indexes.keys()
    assert user_index_ddl(connection) == expected_indexes
    for column, duplicate, index in (
        ("email", "  ACTOR@EXAMPLE.TEST  ", "uq_users_email_normalized"),
        ("username", "  ACTOR  ", "uq_users_username_normalized"),
    ):
        for operation in ("insert", "update"):
            with pytest.raises(IntegrityError, match=index), connection.begin_nested():
                if operation == "insert":
                    values = dict(username="new-identity", email="new@example.test",
                                  display_name="Duplicate", password_hash="test-only")
                    values[column] = duplicate
                    connection.execute(User.__table__.insert().values(**values))
                else:
                    connection.execute(User.__table__.update().where(User.id == "optional-1").values(**{column: duplicate}))
    assert connection.execute(select(User.username, User.email).where(User.id == "actor")).one() == (
        "actor", "actor@example.test",
    )
    assert connection.scalar(select(KnowledgeDocument.body).where(KnowledgeDocument.id == "document")) == "迁移前的原始内容"
    assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()


def foreign_keys(connection, table):
    return {
        (tuple(key["constrained_columns"]), key["referred_table"], tuple(key["referred_columns"]),
         tuple(sorted(key["options"].items())))
        for key in inspect(connection).get_foreign_keys(table)
    }


def assert_foreign_key_enforcement(engine, probes):
    # Enable enforcement outside the transaction; changing this pragma inside
    # an active SQLite transaction would silently do nothing.
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        for table, column, row_id in probes:
            with pytest.raises(IntegrityError, match="FOREIGN KEY"), connection.begin_nested():
                connection.exec_driver_sql(
                    f"UPDATE {table} SET {column}=? WHERE id=?", ("missing-reference", row_id),
                )
