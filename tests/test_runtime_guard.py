import pytest

from backend.runtime_guard import PROTECTED_TABLES, UnsafeDatabaseRole, validate_role


def safe():
    roles = [{"current_user": "saas_app", "rolsuper": False, "rolbypassrls": False,
              "rolcreaterole": False, "rolcreatedb": False}]
    tables = [{"name": name, "rls": True, "force_rls": True, "can_own": False, "can_truncate": False}
              for name in PROTECTED_TABLES]
    return roles, tables


def test_safe_role_and_inherited_privilege_rejection():
    roles, tables = safe()
    validate_role(roles, tables, expected_user="saas_app")
    roles.append({**roles[0], "rolbypassrls": True})
    with pytest.raises(UnsafeDatabaseRole, match="PRIVILEGED"):
        validate_role(roles, tables, expected_user="saas_app")


@pytest.mark.parametrize("field", ["can_own", "can_truncate", "rls", "force_rls"])
def test_table_policy_bypass_rejected(field):
    roles, tables = safe()
    tables[0][field] = not tables[0][field]
    with pytest.raises(UnsafeDatabaseRole):
        validate_role(roles, tables, expected_user="saas_app")


def test_wrong_identity_and_missing_table_rejected():
    roles, tables = safe()
    with pytest.raises(UnsafeDatabaseRole, match="MISMATCH"):
        validate_role(roles, tables, expected_user="owner")
    with pytest.raises(UnsafeDatabaseRole, match="MISSING"):
        validate_role(roles, tables[:-1], expected_user="saas_app")
