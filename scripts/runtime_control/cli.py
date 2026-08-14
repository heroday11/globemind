"""Command-line interface for runtime observation and adopted lifecycle control."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from .catalog import catalog_payload
from .constants import DESTRUCTIVE_COMMANDS, LIFECYCLE_COMMANDS, SAFE_COMMANDS, SCHEMA_VERSION
from .inspection import RuntimeInspector, list_payload
from .lifecycle import LifecycleDispatcher, LifecycleError
from .manifest import InventoryError, load_inventory
from .redaction import redact_text, sanitize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GlobeMind runtime inventory, diagnostics, and per-service lifecycle control."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in sorted(SAFE_COMMANDS | DESTRUCTIVE_COMMANDS):
        subparser = subparsers.add_parser(operation)
        subparser.add_argument("services", nargs="*", help="optional service IDs")
        subparser.add_argument(
            "--json", action="store_true", dest="as_json", help="emit strict JSON"
        )
        if operation in LIFECYCLE_COMMANDS:
            mode = subparser.add_mutually_exclusive_group()
            mode.add_argument(
                "--dry-run",
                "--plan",
                action="store_true",
                dest="dry_run",
                help="validate and audit the fixed action without dispatching it",
            )
            mode.add_argument(
                "--apply",
                action="store_true",
                help="dispatch one adopted service action after all gates pass",
            )
            subparser.add_argument(
                "--request-id",
                help="reviewed change/request identifier; required with --apply",
            )
        if operation == "status":
            subparser.add_argument(
                "--controller",
                action="store_true",
                help="dispatch the adopted controller status operation for one service",
            )
    return parser


def print_text(payload: Mapping[str, Any]) -> None:
    if payload.get("mode") == "lifecycle":
        print(
            f"service={payload.get('service_id')} operation={payload.get('operation')} "
            f"outcome={payload.get('outcome')} dry_run={payload.get('dry_run')}"
        )
        return
    if payload.get("operation") == "list":
        print("ID                 KIND          CRITICALITY OWNER              CONTROLLER")
        for service in payload.get("services") or []:
            print(
                f"{service['id']:<18} {str(service.get('kind', '')):<13} "
                f"{service['criticality']:<11} {service['owner']:<18} {service['controller'].get('path', '')}"
            )
        return
    if payload.get("operation") == "catalog":
        summary = payload.get("summary") or {}
        print(
            f"catalog_current={summary.get('catalog_current', 0)} "
            f"catalog_drifted={summary.get('catalog_drifted', 0)} "
            f"takeover_ready={summary.get('takeover_ready', 0)} "
            f"takeover_blocked={summary.get('takeover_blocked', 0)}"
        )
        for service in payload.get("services") or []:
            blockers = ",".join(service.get("management_blockers") or []) or "none"
            print(
                f"{service['id']:<18} {service['catalog_status']:<8} "
                f"authorization={service['lifecycle_authorization']['state']} "
                f"blockers={blockers}"
            )
        return
    summary = payload.get("summary") or {}
    print(
        f"overall={payload.get('overall_status')} services={summary.get('service_count', 0)} "
        f"healthy={summary.get('healthy', 0)} degraded={summary.get('degraded', 0)} "
        f"unhealthy={summary.get('unhealthy', 0)}"
    )
    for service in payload.get("services") or []:
        pid = service.get("pid") or {}
        identity = pid.get("pid") or f"{pid.get('running_members', 0)}/{pid.get('member_count', 0)}"
        print(f"{service['id']:<18} {service['status']:<9} pid={identity}")
        if payload.get("operation") == "doctor":
            for issue in service.get("issues") or []:
                print(f"  {issue['severity']}: {issue['code']}: {issue['message']}")


def configuration_override_requested(argv: Sequence[str]) -> bool:
    return any(
        token in {"--manifest", "--root"}
        or token.startswith("--manifest=")
        or token.startswith("--root=")
        for token in argv
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if configuration_override_requested(arguments):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "read_only": True,
            "error": "configuration-override-disabled",
            "operation": "configuration",
            "message": "Production runtime inventory and trust roots cannot be overridden by CLI.",
        }
        if "--json" in arguments:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(payload["message"], file=sys.stderr)
        return 64

    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.operation in {"adopt", "kill"} or (
        args.operation in DESTRUCTIVE_COMMANDS and not args.services
    ):
        payload = sanitize(
            {
                "schema_version": SCHEMA_VERSION,
                "read_only": True,
                "error": "destructive-command-disabled",
                "operation": args.operation,
                "message": "Runtime control is observe-only; lifecycle operations are disabled.",
            }
        )
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(payload["message"], file=sys.stderr)
        return 64

    lifecycle_requested = args.operation in {"restart", "start", "stop"} or (
        args.operation == "status" and args.controller
    )
    if (
        args.operation == "status"
        and (args.dry_run or args.apply or args.request_id)
        and not args.controller
    ):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "read_only": True,
            "error": "lifecycle-flag-requires-controller",
            "operation": args.operation,
            "message": "--dry-run/--plan on status requires --controller.",
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(payload["message"], file=sys.stderr)
        return 64
    if lifecycle_requested and len(args.services) != 1:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "read_only": True,
            "error": "service-selection-invalid",
            "operation": args.operation,
            "message": "Lifecycle operations require exactly one explicit service ID.",
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(payload["message"], file=sys.stderr)
        return 64

    try:
        inventory = load_inventory()
        if lifecycle_requested:
            payload = LifecycleDispatcher(inventory).execute(
                args.services[0],
                args.operation,
                dry_run=not args.apply,
                request_id=args.request_id,
            )
        elif args.operation == "list":
            payload = list_payload(inventory, args.services)
        elif args.operation == "catalog":
            payload = catalog_payload(inventory, args.services)
        else:
            inspector = RuntimeInspector(inventory)
            payload = inspector.inspect(args.services, doctor=args.operation == "doctor")
    except LifecycleError as exc:
        payload = sanitize(
            {
                "schema_version": SCHEMA_VERSION,
                "read_only": True,
                "error": exc.code,
                "operation": args.operation,
                "service_id": args.services[0] if len(args.services) == 1 else None,
                "message": redact_text(str(exc)),
                "details": [redact_text(item) for item in exc.details],
            }
        )
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(payload["message"], file=sys.stderr)
        return 64
    except InventoryError as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "read_only": True,
            "error": "inventory-error",
            "message": redact_text(str(exc)),
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(payload["message"], file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print_text(payload)
    if lifecycle_requested and payload.get("outcome") != "planned":
        return 0 if payload.get("outcome") == "succeeded" else 1
    if args.operation in {"status", "doctor"} and payload.get("overall_status") == "unhealthy":
        return 1
    return 0
