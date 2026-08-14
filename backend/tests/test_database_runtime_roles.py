from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.routes import opinion_v2

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_ROOT = PROJECT_ROOT / "deploy"
if str(DEPLOY_ROOT) not in sys.path:
    sys.path.insert(0, str(DEPLOY_ROOT))

db_role_policy = importlib.import_module("db_role_policy")
db_runtime_roles = importlib.import_module("db_runtime_roles")
v093_database_schema = importlib.import_module("v093_database_schema")


class CapturingCursor:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement: str, parameters: object = None):
        self.calls.append((statement, parameters))


class MissingRoleCursor(CapturingCursor):
    def fetchone(self):
        return None


class ZeroLargeObjectCursor(CapturingCursor):
    def fetchone(self):
        return (0,)


def test_fixed_role_policy_is_minimal_and_complete():
    policies = {policy.name: policy for policy in db_role_policy.ROLE_POLICIES}
    assert set(policies) == {"web_runtime", "wave1_loader"}
    assert len(policies["web_runtime"].table_privileges) == 31
    assert set(db_role_policy.CURRENT_STORY_GRAPH_TABLES) <= set(
        policies["web_runtime"].table_privileges
    )
    assert {"story_edges", "story_trees", "story_relations"}.isdisjoint(
        policies["web_runtime"].table_privileges
    )
    assert policies["wave1_loader"].table_privileges == {
        "news": ("SELECT", "INSERT"),
        "media_source": ("SELECT", "INSERT", "UPDATE"),
        "globemind_pipeline_checkpoint": ("SELECT", "INSERT", "UPDATE"),
    }
    assert policies["wave1_loader"].sequences == ("news_id_seq", "media_source_id_seq")
    assert all(
        "CREATE" not in privileges
        for policy in policies.values()
        for privileges in policy.table_privileges.values()
    )
    legacy_relations = {
        relation
        for gap in db_role_policy.LEGACY_RELATION_GAPS.values()
        for relation in gap["relations"]
    }
    assert legacy_relations.isdisjoint(policies["web_runtime"].table_privileges)


def test_current_story_graph_relations_are_exactly_read_only():
    cursor = CapturingCursor()
    policy = next(policy for policy in db_role_policy.ROLE_POLICIES if policy.name == "web_runtime")

    db_runtime_roles._reset_and_grant(cursor, policy)

    statements = [statement for statement, _parameters in cursor.calls]
    expected = {
        f"GRANT SELECT ON TABLE public.{table} TO web_runtime"
        for table in db_role_policy.CURRENT_STORY_GRAPH_TABLES
    }
    assert expected <= set(statements)
    assert not any(
        statement.startswith(("GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"))
        and any(f"public.{table}" in statement for table in db_role_policy.CURRENT_STORY_GRAPH_TABLES)
        for statement in statements
    )


def test_story_graph_health_relations_match_the_fixed_role_policy():
    from api.features.story_graph import STORY_GRAPH_HEALTH_RELATIONS

    health_tables = {relation.removeprefix("public.") for relation in STORY_GRAPH_HEALTH_RELATIONS}
    assert health_tables == set(db_role_policy.CURRENT_STORY_GRAPH_TABLES)


def test_all_legacy_relation_gaps_are_closed():
    assert db_role_policy.LEGACY_RELATION_GAPS == {}


def test_resolved_legacy_surfaces_have_explicit_outcomes():
    resolved = db_role_policy.RESOLVED_LEGACY_SURFACES
    assert resolved["api_graph"]["current_behavior"] == "migrated_current_l3_l2_l1"
    assert set(resolved["api_graph"]["current_relations"]) <= set(db_role_policy.WEB_TABLES)
    assert resolved["legacy_opinion"]["current_behavior"] == "retired_410"
    assert resolved["legacy_search"]["current_behavior"] == "migrated_current_l3_l2_l1"
    assert set(resolved["legacy_search"]["current_relations"]) <= set(db_role_policy.WEB_TABLES)
    report = db_runtime_roles.dry_run_report()
    assert report["legacy_relation_gaps"] == {}
    assert (
        report["resolved_legacy_surfaces"]["api_graph"]["current_behavior"]
        == "migrated_current_l3_l2_l1"
    )
    assert report["resolved_legacy_surfaces"]["legacy_opinion"]["current_behavior"] == "retired_410"
    assert (
        report["resolved_legacy_surfaces"]["legacy_search"]["current_behavior"]
        == "migrated_current_l3_l2_l1"
    )


def test_role_password_is_always_a_database_parameter():
    cursor = CapturingCursor()
    secret = "parameter-only-secret-value"
    verifier = db_runtime_roles._scram_sha_256_verifier(secret)

    db_runtime_roles._set_role_password(cursor, "web_runtime", verifier)

    assert cursor.calls == [("ALTER ROLE web_runtime PASSWORD %s", (verifier,))]
    assert secret not in cursor.calls[0][0]
    assert secret not in cursor.calls[0][1]
    assert verifier.startswith("SCRAM-SHA-256$4096:")


def test_role_password_target_cannot_be_supplied_dynamically():
    verifier = db_runtime_roles._scram_sha_256_verifier("not-used")
    with pytest.raises(db_runtime_roles.PolicyError, match="fixed runtime role"):
        db_runtime_roles._set_role_password(CapturingCursor(), "postgres", verifier)


def test_missing_role_probe_has_only_its_psycopg_placeholder():
    cursor = MissingRoleCursor()
    policy = db_role_policy.ROLE_POLICIES[0]

    assert db_runtime_roles._role_attribute_issues(cursor, policy) == ["role is missing"]
    statement, parameters = cursor.calls[0]
    assert parameters == (policy.name,)
    assert statement.count("%s") == 1
    assert "%" not in statement.replace("%s", "")
    assert "rolconfig" not in statement
    assert "pg_db_role_setting" in statement


def test_large_object_probe_uses_catalog_acls_supported_by_postgresql_17():
    cursor = ZeroLargeObjectCursor()

    assert db_runtime_roles._large_object_privilege_count(cursor, "web_runtime") == 0
    statement, parameters = cursor.calls[0]
    assert parameters == ("web_runtime",)
    assert "has_largeobject_privilege" not in statement
    assert "pg_largeobject_metadata" in statement
    assert "aclexplode" in statement
    assert "acldefault" in statement


def test_scram_verifier_uses_random_salt_and_strong_components():
    first = db_runtime_roles._scram_sha_256_verifier("A-secure-test-password-1234567890/+")
    second = db_runtime_roles._scram_sha_256_verifier("A-secure-test-password-1234567890/+")

    assert first != second
    match = db_runtime_roles.SCRAM_VERIFIER_RE.fullmatch(first)
    assert match is not None
    assert int(match.group(1)) >= 4096
    assert len(base64.b64decode(match.group(2))) >= 16
    assert len(base64.b64decode(match.group(3))) == 32
    assert len(base64.b64decode(match.group(4))) == 32


@pytest.mark.parametrize(
    "argv",
    [
        [
            "verify",
            "--host",
            "192.168.1.10",
            "--sslmode",
            "disable",
            "--admin-password-file",
            "/tmp/admin",
        ],
        [
            "verify",
            "--host",
            "8.8.8.8",
            "--sslmode",
            "disable",
            "--allow-private-scram-transport",
            "--admin-password-file",
            "/tmp/admin",
        ],
    ],
)
def test_unencrypted_transport_rejects_missing_switch_or_public_host(argv: list[str]):
    with pytest.raises(SystemExit):
        db_runtime_roles.parse_args(argv)


class TransportCursor:
    def __init__(self, *, password_encryption: str = "scram-sha-256", hba_rows=None):
        self.password_encryption = password_encryption
        self.hba_rows = hba_rows or []
        self.last_statement = ""
        self.statements: list[str] = []

    def execute(self, statement: str, _parameters=None):
        self.last_statement = statement
        self.statements.append(statement)

    def fetchone(self):
        if "current_setting('ssl')" in self.last_statement:
            return ("off", self.password_encryption, False, True, "192.168.1.20")
        raise AssertionError(self.last_statement)

    def fetchall(self):
        if "pg_hba_file_rules" in self.last_statement:
            return self.hba_rows
        raise AssertionError(self.last_statement)


def _private_transport_args() -> argparse.Namespace:
    return argparse.Namespace(
        host="192.168.1.10",
        sslmode="disable",
        allow_private_scram_transport=True,
    )


def test_private_transport_requires_scram_server_and_runtime_role_hba_matches():
    good_hba = [
        (1, 5, "host", ["all"], ["all"], "127.0.0.1", "255.255.255.255", "trust", None),
        (
            2,
            6,
            "host",
            ["all"],
            ["all"],
            "::1",
            "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
            "trust",
            None,
        ),
        (3, 10, "host", ["all"], ["all"], "0.0.0.0", "0.0.0.0", "scram-sha-256", None),
    ]
    cursor = TransportCursor(hba_rows=good_hba)
    issues, report = db_runtime_roles._transport_preflight(cursor, _private_transport_args())

    assert issues == []
    assert "host(inet_client_addr())" in cursor.statements[0]
    assert report["transport_encrypted"] is False
    assert report["auth"] == "scram-sha-256"
    assert report["private_transition"] is True


def test_private_transport_accepts_precise_role_and_network_rules():
    precise_hba = [
        (
            1,
            10,
            "hostnossl",
            ["news"],
            ["web_runtime"],
            "192.168.1.0",
            "255.255.255.0",
            "scram-sha-256",
            None,
        ),
        (
            2,
            11,
            "hostnossl",
            ["news"],
            ["wave1_loader"],
            "192.168.1.0",
            "255.255.255.0",
            "scram-sha-256",
            None,
        ),
        (3, 12, "host", ["all"], ["all"], "0.0.0.0", "0.0.0.0", "md5", None),
    ]

    issues, _report = db_runtime_roles._transport_preflight(
        TransportCursor(hba_rows=precise_hba), _private_transport_args()
    )

    assert issues == []


@pytest.mark.parametrize(
    "password_encryption,hba_rows,expected",
    [
        (
            "md5",
            [(1, 10, "host", ["all"], ["all"], "0.0.0.0", "0.0.0.0", "scram-sha-256", None)],
            "password_encryption",
        ),
        (
            "scram-sha-256",
            [(1, 10, "host", ["all"], ["all"], "0.0.0.0", "0.0.0.0", "md5", None)],
            "first match is not scram-sha-256",
        ),
        ("scram-sha-256", [], "no matching rule"),
    ],
)
def test_private_transport_fails_closed_on_weak_or_missing_scram_policy(
    password_encryption: str, hba_rows, expected: str
):
    issues, _report = db_runtime_roles._transport_preflight(
        TransportCursor(password_encryption=password_encryption, hba_rows=hba_rows),
        _private_transport_args(),
    )

    assert any(expected in issue for issue in issues)


def test_private_transport_fails_closed_on_indeterminate_hba_selector():
    hba_rows = [
        (1, 10, "host", ["news"], ["+runtime"], "all", None, "scram-sha-256", None),
        (2, 11, "host", ["all"], ["all"], "all", None, "scram-sha-256", None),
    ]

    issues, _report = db_runtime_roles._transport_preflight(
        TransportCursor(hba_rows=hba_rows), _private_transport_args()
    )

    assert any("indeterminate for runtime role" in issue for issue in issues)


class TargetCursor:
    def __init__(self, schema_owner: str, database_owner: str):
        self.schema_owner = schema_owner
        self.database_owner = database_owner
        self.last_statement = ""

    def execute(self, statement: str, _parameters=None):
        self.last_statement = statement

    def fetchone(self):
        if "FROM pg_catalog.pg_roles" in self.last_statement:
            return ("news", "postgres", "postgres", True)
        if "CROSS JOIN pg_catalog.pg_database" in self.last_statement:
            return (self.schema_owner, self.database_owner)
        raise AssertionError(self.last_statement)


@pytest.mark.parametrize("schema_owner", ["postgres", "pg_database_owner"])
def test_target_preflight_accepts_only_postgres_equivalent_public_ownership(
    schema_owner: str,
):
    assert db_runtime_roles._target_preflight(TargetCursor(schema_owner, "postgres")) == []


def test_target_preflight_rejects_pg_database_owner_for_another_database_owner():
    issues = db_runtime_roles._target_preflight(
        TargetCursor("pg_database_owner", "application_owner")
    )

    assert any("fixed postgres owner contract" in issue for issue in issues)


def test_role_cli_dry_run_is_structured_and_does_not_read_secrets(tmp_path: Path):
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["DB_PASSWORD"] = "sentinel-that-must-not-appear"
    result = subprocess.run(
        [sys.executable, "-B", str(DEPLOY_ROOT / "db_runtime_roles.py"), "dry-run"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "planned"
    assert report["target"] == {"database": "news", "owner": "postgres", "schema": "public"}
    assert "sentinel-that-must-not-appear" not in result.stdout


def test_fixed_schema_cli_dry_run_has_only_two_hash_pinned_migrations():
    result = subprocess.run(
        [sys.executable, "-B", str(DEPLOY_ROOT / "v093_database_schema.py"), "dry-run"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "planned"
    assert [migration["id"] for migration in report["migrations"]] == [
        "v0.9.3-opinion-runtime-schema",
        "v0.9.3-wave1-checkpoint-schema",
    ]
    assert all(len(migration["sha256"]) == 64 for migration in report["migrations"])


def test_fixed_schema_sql_matches_hash_and_avoids_reserved_constraint_alias():
    payload = v093_database_schema.OPINION_MIGRATION.read_text(encoding="utf-8")

    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == (
        v093_database_schema.OPINION_SHA256
    )
    assert "AS constraint" not in payload
    assert "ADD CONSTRAINT china_opinion_feedback_correction_check" in payload
    assert "AS constraint" not in Path(v093_database_schema.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("argument", ["--path", "--sql", "--migration", "--database"])
def test_fixed_schema_cli_rejects_migration_overrides(argument: str):
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(DEPLOY_ROOT / "v093_database_schema.py"),
            "dry-run",
            argument,
            "x",
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0


def test_fixed_schema_loader_rejects_hash_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    changed = tmp_path / "changed.sql"
    changed.write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(v093_database_schema, "OPINION_MIGRATION", changed)

    with pytest.raises(db_runtime_roles.PolicyError, match="hash mismatch"):
        v093_database_schema._load_opinion_migration()


@pytest.mark.parametrize("argument", ["--role", "--table", "--database", "--sql", "--manifest"])
def test_role_cli_rejects_policy_override_arguments(argument: str):
    result = subprocess.run(
        [sys.executable, "-B", str(DEPLOY_ROOT / "db_runtime_roles.py"), "dry-run", argument, "x"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0


def test_role_secret_file_rejects_weak_mode_symlink_and_weak_value(tmp_path: Path):
    secret = tmp_path / "runtime.password"
    secret.write_text("too-short\n", encoding="utf-8")
    secret.chmod(0o600)
    with pytest.raises(db_runtime_roles.PolicyError, match="independently random"):
        db_runtime_roles._read_secret_file(secret, require_strong=True)

    secret.write_text("aB3/" * 16 + "\n", encoding="utf-8")
    secret.chmod(0o640)
    with pytest.raises(db_runtime_roles.PolicyError, match="mode 0600"):
        db_runtime_roles._read_secret_file(secret, require_strong=True)

    secret.chmod(0o600)
    link = tmp_path / "link.password"
    link.symlink_to(secret)
    with pytest.raises(db_runtime_roles.PolicyError, match="must not be symlinks"):
        db_runtime_roles._read_secret_file(link, require_strong=True)


def test_no_request_runtime_module_contains_schema_ddl():
    ddl_pattern = __import__("re").compile(
        r"\b(?:CREATE|ALTER|DROP|TRUNCATE)\s+(?:TABLE|INDEX|SCHEMA|SEQUENCE)\b",
        __import__("re").IGNORECASE,
    )
    for directory in (PROJECT_ROOT / "backend/api/routes", PROJECT_ROOT / "backend/api/services"):
        for source_path in directory.glob("*.py"):
            assert ddl_pattern.search(source_path.read_text(encoding="utf-8")) is None, source_path


class MappingResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class SchemaSession:
    def __init__(self, *, missing_index: bool = False):
        self.missing_index = missing_index
        self.statements: list[str] = []

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "information_schema.columns" in sql:
            return MappingResult(
                [
                    {
                        "table_name": table,
                        "column_name": column,
                        "udt_name": column_type,
                        "is_nullable": (
                            "NO" if column in opinion_v2._OPINION_NOT_NULL_COLUMNS[table] else "YES"
                        ),
                        "column_default": opinion_v2._OPINION_DEFAULT_MARKERS.get((table, column)),
                    }
                    for table, columns in opinion_v2._OPINION_COLUMN_TYPES.items()
                    for column, column_type in columns.items()
                ]
            )
        if "pg_constraint" in sql:
            return MappingResult(
                [
                    {
                        "table_name": "china_opinion_article_scores",
                        "contype": "p",
                        "definition": "PRIMARY KEY (news_id)",
                    },
                    {
                        "table_name": "china_opinion_feedback",
                        "contype": "p",
                        "definition": "PRIMARY KEY (id)",
                    },
                    {
                        "table_name": "china_opinion_feedback",
                        "contype": "c",
                        "definition": (
                            "CHECK (correction IN ('irrelevant', 'too_positive', "
                            "'too_negative', 'correct'))"
                        ),
                    },
                ]
            )
        if "pg_indexes" in sql:
            indexes = sorted(opinion_v2._OPINION_WRITE_INDEXES.items())
            if self.missing_index:
                indexes = indexes[1:]
            return MappingResult(
                [
                    {
                        "indexname": name,
                        "indexdef": f"CREATE INDEX {name} ON table {columns}",
                    }
                    for name, columns in indexes
                ]
            )
        if "pg_get_serial_sequence" in sql:
            return MappingResult(
                [
                    {
                        "sequence_name": "public.china_opinion_feedback_id_seq",
                        "sequence_owner": "postgres",
                    }
                ]
            )
        raise AssertionError(sql)


def test_opinion_schema_readiness_is_read_only(monkeypatch: pytest.MonkeyPatch):
    session = SchemaSession()
    monkeypatch.setattr(opinion_v2, "_SCHEMA_READY", False)

    opinion_v2._require_opinion_write_schema(session)  # type: ignore[arg-type]

    assert opinion_v2._SCHEMA_READY is True
    assert session.statements
    assert all(
        token not in statement.upper()
        for statement in session.statements
        for token in ("CREATE TABLE", "CREATE INDEX", "ALTER TABLE", "DROP TABLE")
    )


def test_opinion_schema_readiness_fails_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(opinion_v2, "_SCHEMA_READY", False)

    with pytest.raises(HTTPException) as exc_info:
        opinion_v2._require_opinion_write_schema(SchemaSession(missing_index=True))  # type: ignore[arg-type]

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["missing_indexes"]
