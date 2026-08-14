#!/usr/bin/env -S python3 -B
"""Apply or verify the two fixed V0.9.3 database schema migrations."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import os
import sys
from pathlib import Path
from typing import Any, Iterable

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import db_runtime_roles as roles  # noqa: E402
from db_role_policy import DATABASE, OWNER_ROLE, POLICY_SCHEMA_VERSION, SCHEMA  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPINION_MIGRATION = PROJECT_ROOT / "deploy/sql/v0.9.3_web_runtime_schema.sql"
OPINION_SHA256 = "df9c7c1a979f7b637c6c819a9eee3dca3a4881df51c17e16d3973b6b26329c79"
CHECKPOINT_SHA256 = "77e45c5a8652c8513288b1a076fe834d3077d9f6a75c58265d66f46063f65040"
MIGRATION_LOCK = (908731, 2)
CHECKPOINT_COLUMNS = {
    "checkpoint_key",
    "schema_version",
    "job_id",
    "run_id",
    "input_path",
    "input_device",
    "input_inode",
    "input_size",
    "input_offset",
    "input_anchor_sha256",
    "code_version",
    "config_sha256",
    "seen",
    "legacy_seen",
    "inserted",
    "duplicate",
    "invalid",
    "quality_rejected",
    "quality_skip_reasons",
    "completed",
    "sealed_final_bytes",
    "sealed_rows",
    "sealed_sha256",
    "last_progress_at",
    "created_at",
    "updated_at",
}
CHECKPOINT_COLUMN_TYPES = {
    "checkpoint_key": ("text", "NO"),
    "schema_version": ("int2", "NO"),
    "job_id": ("text", "NO"),
    "run_id": ("text", "NO"),
    "input_path": ("text", "NO"),
    "input_device": ("int8", "NO"),
    "input_inode": ("int8", "NO"),
    "input_size": ("int8", "NO"),
    "input_offset": ("int8", "NO"),
    "input_anchor_sha256": ("bpchar", "NO"),
    "code_version": ("text", "NO"),
    "config_sha256": ("bpchar", "NO"),
    "seen": ("int8", "NO"),
    "legacy_seen": ("int8", "YES"),
    "inserted": ("int8", "NO"),
    "duplicate": ("int8", "NO"),
    "invalid": ("int8", "NO"),
    "quality_rejected": ("int8", "NO"),
    "quality_skip_reasons": ("jsonb", "NO"),
    "completed": ("bool", "NO"),
    "sealed_final_bytes": ("int8", "YES"),
    "sealed_rows": ("int8", "YES"),
    "sealed_sha256": ("bpchar", "YES"),
    "last_progress_at": ("timestamptz", "NO"),
    "created_at": ("timestamptz", "NO"),
    "updated_at": ("timestamptz", "NO"),
}
CHECKPOINT_DEFAULT_MARKERS = {
    "seen": "0",
    "inserted": "0",
    "duplicate": "0",
    "invalid": "0",
    "quality_rejected": "0",
    "quality_skip_reasons": "{}",
    "completed": "false",
    "last_progress_at": "clock_timestamp()",
    "created_at": "clock_timestamp()",
    "updated_at": "clock_timestamp()",
}
CHECKPOINT_FIXED_LENGTHS = {
    "input_anchor_sha256": 64,
    "config_sha256": 64,
    "sealed_sha256": 64,
}
OPINION_CRITICAL_COLUMNS = {
    ("china_opinion_article_scores", "news_id"): ("int8", "NO", None),
    ("china_opinion_article_scores", "directness_score"): ("float8", "NO", "0"),
    ("china_opinion_article_scores", "stance_score"): ("float8", "NO", "0"),
    ("china_opinion_article_scores", "confidence"): ("float8", "NO", "0"),
    ("china_opinion_article_scores", "relevance_score"): ("float8", "NO", "0"),
    ("china_opinion_article_scores", "article_weight"): ("float8", "NO", "0"),
    ("china_opinion_article_scores", "method_version"): ("text", "NO", None),
    ("china_opinion_article_scores", "scored_at"): ("timestamptz", "NO", "now()"),
    ("china_opinion_article_scores", "updated_at"): ("timestamptz", "NO", "now()"),
    ("china_opinion_feedback", "id"): ("int8", "NO", "nextval("),
    ("china_opinion_feedback", "news_id"): ("int8", "NO", None),
    ("china_opinion_feedback", "correction"): ("text", "NO", None),
    ("china_opinion_feedback", "created_at"): ("timestamptz", "NO", "now()"),
}
INDEX_CONTRACT = {
    "idx_china_opinion_scores_date": "(published_date)",
    "idx_china_opinion_scores_dims": "(region, language, media_domain, event_family)",
    "idx_china_opinion_scores_direct": "(directness_score, relevance_score)",
    "idx_china_opinion_feedback_news_created": "(news_id, created_at desc, id desc)",
}


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_opinion_migration() -> str:
    payload = OPINION_MIGRATION.read_text(encoding="utf-8")
    if _sha256(payload) != OPINION_SHA256:
        raise roles.PolicyError("fixed opinion migration hash mismatch")
    upper = payload.upper()
    if "BEGIN;" in upper or "COMMIT;" in upper or "ROLLBACK;" in upper:
        raise roles.PolicyError("fixed opinion migration must not control its transaction")
    return payload


def _load_checkpoint_migration() -> str:
    scripts_root = PROJECT_ROOT / "scripts"
    if str(scripts_root) not in sys.path:
        sys.path.insert(0, str(scripts_root))
    migration = importlib.import_module("wave1_loader_migrate")
    payload = str(migration.CHECKPOINT_DDL)
    if _sha256(payload) != CHECKPOINT_SHA256:
        raise roles.PolicyError("fixed checkpoint migration hash mismatch")
    lines = payload.splitlines()
    if not lines or lines[0].strip().upper() != "BEGIN;" or lines[-1].strip().upper() != "COMMIT;":
        raise roles.PolicyError("checkpoint migration transaction wrapper is unexpected")
    body = "\n".join(lines[1:-1]).strip() + "\n"
    upper = body.upper()
    if "BEGIN;" in upper or "COMMIT;" in upper or "ROLLBACK;" in upper:
        raise roles.PolicyError("checkpoint migration contains nested transaction control")
    return body


def dry_run_report() -> dict[str, Any]:
    opinion = _load_opinion_migration()
    checkpoint = _load_checkpoint_migration()
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "mode": "dry-run",
        "status": "planned",
        "target": {"database": DATABASE, "schema": SCHEMA, "owner": OWNER_ROLE},
        "advisory_lock": list(MIGRATION_LOCK),
        "migrations": [
            {
                "id": "v0.9.3-opinion-runtime-schema",
                "sha256": _sha256(opinion),
                "bytes": len(opinion.encode("utf-8")),
            },
            {
                "id": "v0.9.3-wave1-checkpoint-schema",
                "sha256": CHECKPOINT_SHA256,
                "transaction_body_bytes": len(checkpoint.encode("utf-8")),
            },
        ],
    }


def _schema_contract_issues(cursor: Any) -> list[str]:
    issues = roles._required_object_issues(cursor)
    cursor.execute(
        """
        SELECT table_name, column_name, udt_name, is_nullable, column_default,
               character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN (
              'china_opinion_article_scores',
              'china_opinion_feedback',
              'globemind_pipeline_checkpoint'
          )
        """
    )
    columns = {
        (str(table), str(column)): (
            str(column_type),
            str(nullable),
            str(default or ""),
            int(maximum_length) if maximum_length is not None else None,
        )
        for table, column, column_type, nullable, default, maximum_length in cursor.fetchall()
    }
    checkpoint_actual = {
        column for table, column in columns if table == "globemind_pipeline_checkpoint"
    }
    if checkpoint_actual != CHECKPOINT_COLUMNS:
        issues.append("checkpoint column contract differs from V0.9.3")
    for column, (expected_type, expected_nullable) in CHECKPOINT_COLUMN_TYPES.items():
        actual = columns.get(("globemind_pipeline_checkpoint", column))
        if actual is None:
            continue
        if actual[0] != expected_type or actual[1] != expected_nullable:
            issues.append(f"checkpoint column incompatible: {column}")
        default_marker = CHECKPOINT_DEFAULT_MARKERS.get(column)
        if default_marker and default_marker not in actual[2].lower():
            issues.append(f"checkpoint default incompatible: {column}")
        fixed_length = CHECKPOINT_FIXED_LENGTHS.get(column)
        if fixed_length is not None and actual[3] != fixed_length:
            issues.append(f"checkpoint fixed length incompatible: {column}")
    for identity, (expected_type, expected_nullable, default_marker) in OPINION_CRITICAL_COLUMNS.items():
        actual = columns.get(identity)
        if actual is None:
            issues.append(f"opinion column missing: {identity[0]}.{identity[1]}")
            continue
        if actual[0] != expected_type or actual[1] != expected_nullable:
            issues.append(f"opinion column incompatible: {identity[0]}.{identity[1]}")
        if default_marker and default_marker not in actual[2].lower():
            issues.append(f"opinion default incompatible: {identity[0]}.{identity[1]}")

    cursor.execute(
        """
        SELECT c.relname, con.contype,
               pg_catalog.pg_get_constraintdef(con.oid)
        FROM pg_catalog.pg_constraint AS con
        JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname IN (
              'china_opinion_article_scores',
              'china_opinion_feedback',
              'globemind_pipeline_checkpoint'
          )
        """
    )
    constraints = [(str(table), str(kind), str(definition).lower()) for table, kind, definition in cursor.fetchall()]
    if not any(t == "china_opinion_article_scores" and k == "p" and "(news_id)" in d for t, k, d in constraints):
        issues.append("opinion score primary key is incompatible")
    if not any(t == "china_opinion_feedback" and k == "p" and "(id)" in d for t, k, d in constraints):
        issues.append("opinion feedback primary key is incompatible")
    correction_checks = [d for t, k, d in constraints if t == "china_opinion_feedback" and k == "c" and "correction" in d]
    if not correction_checks or not all(
        value in " ".join(correction_checks)
        for value in ("irrelevant", "too_positive", "too_negative", "correct")
    ):
        issues.append("opinion feedback correction CHECK is incompatible")
    checkpoint_named = {
        d
        for t, _k, d in constraints
        if t == "globemind_pipeline_checkpoint"
    }
    if not any(
        t == "globemind_pipeline_checkpoint" and k == "p" and "(checkpoint_key)" in d
        for t, k, d in constraints
    ):
        issues.append("checkpoint primary key is incompatible")
    checkpoint_contract_fragments = (
        "schema_version = 2",
        "input_size >= 0",
        "input_offset >= 0",
        "legacy_seen >= 0",
        "seen >= 0",
        "inserted >= 0",
        "duplicate >= 0",
        "invalid >= 0",
        "quality_rejected >= 0",
    )
    joined_checkpoint_constraints = " ".join(checkpoint_named)
    for fragment in checkpoint_contract_fragments:
        if fragment not in joined_checkpoint_constraints:
            issues.append(f"checkpoint constraint missing: {fragment}")
    if not any("seen =" in definition and "inserted" in definition for definition in checkpoint_named):
        issues.append("checkpoint counter invariant is missing")
    if not any("sealed_final_bytes" in definition and "completed" in definition for definition in checkpoint_named):
        issues.append("checkpoint seal invariant is missing")

    cursor.execute(
        """
        SELECT indexname, indexdef
        FROM pg_catalog.pg_indexes
        WHERE schemaname = 'public' AND indexname = ANY(%s)
        """,
        (list(INDEX_CONTRACT),),
    )
    indexes = {str(name): " ".join(str(definition).lower().split()) for name, definition in cursor.fetchall()}
    for name, fragment in INDEX_CONTRACT.items():
        if fragment not in indexes.get(name, ""):
            issues.append(f"opinion index incompatible: {name}")
    cursor.execute(
        """
        SELECT pg_catalog.pg_get_serial_sequence(
                   'public.china_opinion_feedback', 'id'
               ),
               pg_catalog.pg_get_userbyid(sequence.relowner)
        FROM pg_catalog.pg_class AS sequence
        WHERE sequence.oid = pg_catalog.to_regclass(
            pg_catalog.pg_get_serial_sequence('public.china_opinion_feedback', 'id')
        )
        """
    )
    sequence = cursor.fetchone()
    if not sequence or tuple(sequence) != (
        "public.china_opinion_feedback_id_seq",
        OWNER_ROLE,
    ):
        issues.append("opinion feedback sequence contract is incompatible")
    return sorted(set(issues))


def verify_connection(connection: Any, args: argparse.Namespace) -> dict[str, Any]:
    with connection.cursor() as cursor:
        issues = roles._target_preflight(cursor)
        transport_issues, transport = roles._transport_preflight(cursor, args)
        issues.extend(transport_issues)
        issues.extend(_schema_contract_issues(cursor))
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "mode": "verify",
        "status": "ready" if not issues else "not_ready",
        "target": {"database": DATABASE, "schema": SCHEMA, "owner": OWNER_ROLE},
        "transport": transport,
        "migrations": dry_run_report()["migrations"],
        "issues": sorted(set(issues)),
    }


def _verify(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    password = roles._read_secret_file(args.admin_password_file, require_strong=False)
    connection = roles._connect_admin(args, password)
    try:
        connection.set_session(readonly=True, autocommit=False)
        report = verify_connection(connection, args)
        connection.rollback()
    finally:
        connection.close()
    return report, 0 if report["status"] == "ready" else 2


def _apply(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    opinion = _load_opinion_migration()
    checkpoint = _load_checkpoint_migration()
    password = roles._read_secret_file(args.admin_password_file, require_strong=False)
    connection = roles._connect_admin(args, password)
    try:
        connection.autocommit = False
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s, %s)", MIGRATION_LOCK)
            transport_issues, _transport = roles._transport_preflight(cursor, args)
            preflight_issues = roles._target_preflight(cursor) + transport_issues
            if preflight_issues:
                raise roles.PolicyError("fixed schema target or transport preflight failed")
            cursor.execute(opinion)
            cursor.execute(checkpoint)
        report = verify_connection(connection, args)
        if report["status"] != "ready":
            raise roles.PolicyError("post-migration schema verification failed")
        connection.commit()
        report["mode"] = "apply"
        report["status"] = "applied"
        return report, 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("dry-run")
    for command in ("verify", "apply"):
        command_parser = commands.add_parser(command)
        roles._add_connection_arguments(command_parser)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command in {"verify", "apply"}:
        if args.port < 1 or args.port > 65535:
            parser.error("--port must be between 1 and 65535")
        if args.connect_timeout < 1 or args.connect_timeout > 60:
            parser.error("--connect-timeout must be between 1 and 60")
        try:
            roles._validate_transport_arguments(args)
        except roles.PolicyError as exc:
            parser.error(str(exc))
    return args


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.command == "dry-run":
            report, exit_code = dry_run_report(), 0
        elif args.command == "verify":
            report, exit_code = _verify(args)
        else:
            report, exit_code = _apply(args)
        roles._emit(report)
        return exit_code
    except roles.PolicyError as exc:
        roles._emit(
            {
                "schema_version": POLICY_SCHEMA_VERSION,
                "status": "error",
                "error": str(exc),
            }
        )
        return 2
    except Exception:
        roles._emit(
            {
                "schema_version": POLICY_SCHEMA_VERSION,
                "status": "error",
                "error": "unexpected fixed schema operation failure",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
