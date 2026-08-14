#!/usr/bin/env -S python3 -B
"""Provision and verify GlobeMind's fixed least-privilege PostgreSQL roles."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, Iterable

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from db_role_policy import (  # noqa: E402
    ALL_REQUIRED_SEQUENCES,
    ALL_REQUIRED_TABLES,
    ALLOWED_SEQUENCE_PRIVILEGES,
    ALLOWED_TABLE_PRIVILEGES,
    DATABASE,
    EXPECTED_ROLE_SETTINGS,
    LEGACY_RELATION_GAPS,
    OWNER_ROLE,
    POLICY_SCHEMA_VERSION,
    RESOLVED_LEGACY_SURFACES,
    ROLE_POLICIES,
    SCHEMA,
    RolePolicy,
)

IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MAX_SECRET_BYTES = 4096
SCRAM_ITERATIONS = 4096
SCRAM_SALT_BYTES = 16
SCRAM_VERIFIER_RE = re.compile(
    r"^SCRAM-SHA-256\$([0-9]+):([A-Za-z0-9+/]+={0,2})\$"
    r"([A-Za-z0-9+/]+={0,2}):([A-Za-z0-9+/]+={0,2})$"
)
ROLE_BY_NAME = {role.name: role for role in ROLE_POLICIES}
ROLE_NAMES = tuple(ROLE_BY_NAME)


class PolicyError(RuntimeError):
    """A deterministic safety precondition was not met."""


def _validate_policy() -> None:
    fixed_identifiers = [DATABASE, SCHEMA, OWNER_ROLE, *ROLE_NAMES]
    fixed_identifiers.extend(ALL_REQUIRED_TABLES)
    fixed_identifiers.extend(ALL_REQUIRED_SEQUENCES)
    if any(not IDENTIFIER_RE.fullmatch(value) for value in fixed_identifiers):
        raise PolicyError("fixed database policy contains an invalid identifier")
    if set(ROLE_NAMES) != {"web_runtime", "wave1_loader"}:
        raise PolicyError("fixed database role set changed unexpectedly")
    for role in ROLE_POLICIES:
        if role.connection_limit < 1:
            raise PolicyError("fixed role connection limit is invalid")
        for privileges in role.table_privileges.values():
            if not privileges or not set(privileges) <= ALLOWED_TABLE_PRIVILEGES:
                raise PolicyError("fixed table privilege set is invalid")


def _read_secret_file(path: Path, *, require_strong: bool) -> str:
    path = path.expanduser()
    if not path.is_absolute():
        raise PolicyError("secret file paths must be absolute")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise PolicyError("a required secret file is unavailable") from exc
    if stat.S_ISLNK(before.st_mode):
        raise PolicyError("secret files must not be symlinks")
    if not stat.S_ISREG(before.st_mode):
        raise PolicyError("secret files must be regular files")
    if stat.S_IMODE(before.st_mode) != 0o600:
        raise PolicyError("secret files must have mode 0600")
    if before.st_uid != os.geteuid():
        raise PolicyError("secret files must be owned by the invoking user")
    if before.st_size <= 0 or before.st_size > MAX_SECRET_BYTES:
        raise PolicyError("a secret file has an invalid size")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PolicyError("a secret file could not be opened safely") from exc
    try:
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or not stat.S_ISREG(after.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_uid != os.geteuid()
            or after.st_size <= 0
            or after.st_size > MAX_SECRET_BYTES
        ):
            raise PolicyError("a secret file changed while opening")
        chunks: list[bytes] = []
        remaining = MAX_SECRET_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(payload) != after.st_size
        ):
            raise PolicyError("a secret file changed while reading")
    finally:
        os.close(descriptor)
    if len(payload) > MAX_SECRET_BYTES:
        raise PolicyError("a secret file is too large")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError("secret files must contain UTF-8 text") from exc
    if text.endswith("\r\n"):
        value = text[:-2]
    elif text.endswith("\n"):
        value = text[:-1]
    else:
        value = text
    if not value or value != value.strip() or any(char in value for char in "\r\n\x00"):
        raise PolicyError("a secret file contains an invalid value")
    if len(value) > 256:
        raise PolicyError("runtime role passwords must be at most 256 characters")
    if require_strong:
        if (
            len(value) < 32
            or len(set(value)) < 12
            or not value.isascii()
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
        ):
            raise PolicyError("runtime role passwords must be independently random ASCII values")
    return value


def _scram_sha_256_verifier(password: str) -> str:
    """Derive a PostgreSQL SCRAM verifier locally; plaintext never enters SQL."""
    salt = secrets.token_bytes(SCRAM_SALT_BYTES)
    salted_password = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("ascii"),
        salt,
        SCRAM_ITERATIONS,
    )
    client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()
    encoded_salt = base64.b64encode(salt).decode("ascii")
    encoded_stored_key = base64.b64encode(stored_key).decode("ascii")
    encoded_server_key = base64.b64encode(server_key).decode("ascii")
    return (
        f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{encoded_salt}$"
        f"{encoded_stored_key}:{encoded_server_key}"
    )


def _validate_scram_verifier(verifier: str) -> None:
    match = SCRAM_VERIFIER_RE.fullmatch(verifier)
    if not match or int(match.group(1)) < SCRAM_ITERATIONS:
        raise PolicyError("role password must be supplied as a strong SCRAM-SHA-256 verifier")


def _connection_kwargs(args: argparse.Namespace, password: str) -> dict[str, Any]:
    return {
        "host": args.host,
        "port": args.port,
        "dbname": DATABASE,
        "user": OWNER_ROLE,
        "password": password,
        "sslmode": args.sslmode,
        "connect_timeout": args.connect_timeout,
        "application_name": "globemind-db-role-provisioner-v093",
        "options": "-c statement_timeout=60000 -c lock_timeout=5000",
    }


def _validate_transport_arguments(args: argparse.Namespace) -> None:
    if args.sslmode == "disable":
        if not args.allow_private_scram_transport:
            raise PolicyError(
                "sslmode=disable requires --allow-private-scram-transport"
            )
        try:
            address = ipaddress.ip_address(args.host)
        except ValueError as exc:
            raise PolicyError(
                "unencrypted SCRAM transition requires a literal private IP"
            ) from exc
        if not address.is_private:
            raise PolicyError(
                "unencrypted SCRAM transition is forbidden for non-private addresses"
            )
    elif args.allow_private_scram_transport:
        raise PolicyError(
            "--allow-private-scram-transport is valid only with sslmode=disable"
        )


def _connect_admin(args: argparse.Namespace, password: str):
    try:
        import psycopg2
    except ImportError as exc:
        raise PolicyError("psycopg2 is required for verify/apply") from exc
    try:
        return psycopg2.connect(**_connection_kwargs(args, password))
    except Exception as exc:
        raise PolicyError("database connection failed") from exc


def _hba_selectors(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    return (text,) if text else ()


def _hba_selector_match(value: Any, target: str, *, database: bool) -> bool | None:
    selectors = _hba_selectors(value)
    if not selectors:
        return None
    uncertain = False
    complex_database = {"sameuser", "samerole", "samegroup", "replication"}
    for selector in selectors:
        normalized = selector.lower()
        if normalized == "all" or selector == target:
            return True
        if (
            normalized.startswith(("+", "@", "/"))
            or (database and normalized in complex_database)
        ):
            uncertain = True
    return None if uncertain else False


def _hba_address_match(address: Any, netmask: Any, client_address: str) -> bool | None:
    address_text = str(address or "").strip().lower()
    netmask_text = str(netmask or "").strip().lower()
    if address_text == "all":
        return True
    if address_text in {"samehost", "samenet"} or not address_text:
        return None
    try:
        client = ipaddress.ip_address(client_address)
        if "/" in address_text:
            network = ipaddress.ip_network(address_text, strict=False)
        else:
            network_address = ipaddress.ip_address(address_text)
            if not netmask_text:
                return client == network_address
            mask = ipaddress.ip_address(netmask_text)
            if network_address.version != mask.version:
                return None
            mask_bits = f"{int(mask):0{mask.max_prefixlen}b}"
            if "01" in mask_bits:
                return None
            prefix_length = mask_bits.count("1")
            network = ipaddress.ip_network(
                f"{network_address}/{prefix_length}",
                strict=False,
            )
    except ValueError:
        return None
    return client.version == network.version and client in network


def _hba_connection_match(connection_type: Any) -> bool | None:
    normalized = str(connection_type or "").strip().lower()
    if normalized in {"host", "hostnossl"}:
        return True
    if normalized in {"local", "hostssl"}:
        return False
    return None


def _runtime_hba_issues(hba_rows: Iterable[Any], client_address: str) -> list[str]:
    issues: list[str] = []
    parsed_rows: list[tuple[Any, ...]] = []
    for row in hba_rows:
        parsed = tuple(row)
        if len(parsed) != 9:
            issues.append("pg_hba_file_rules returned an unexpected row shape")
            continue
        parsed_rows.append(parsed)
        if parsed[8]:
            issues.append("pg_hba_file_rules contains an invalid rule")

    for role_name in ROLE_NAMES:
        matched_auth: str | None = None
        indeterminate = False
        for row in parsed_rows:
            (
                _rule_number,
                _line_number,
                connection_type,
                databases,
                users,
                address,
                netmask,
                auth_method,
                error,
            ) = row
            if error:
                continue
            dimensions = (
                _hba_connection_match(connection_type),
                _hba_selector_match(databases, DATABASE, database=True),
                _hba_selector_match(users, role_name, database=False),
                _hba_address_match(address, netmask, client_address),
            )
            if False in dimensions:
                continue
            if None in dimensions:
                indeterminate = True
                break
            matched_auth = str(auth_method or "").strip().lower()
            break
        if indeterminate:
            issues.append(f"HBA first match is indeterminate for runtime role: {role_name}")
        elif matched_auth is None:
            issues.append(f"HBA has no matching rule for runtime role: {role_name}")
        elif matched_auth != "scram-sha-256":
            issues.append(f"HBA first match is not scram-sha-256 for runtime role: {role_name}")
    return issues


def _transport_preflight(
    cursor: Any, args: argparse.Namespace
) -> tuple[list[str], dict[str, Any]]:
    issues: list[str] = []
    cursor.execute(
        """
        SELECT current_setting('ssl'), current_setting('password_encryption'),
               coalesce(
                   (SELECT ssl FROM pg_catalog.pg_stat_ssl
                    WHERE pid = pg_catalog.pg_backend_pid()),
                   false
               ),
               coalesce(
                   (SELECT rolpassword LIKE 'SCRAM-SHA-256$%'
                    FROM pg_catalog.pg_authid
                    WHERE rolname = current_user),
                   false
               ),
               pg_catalog.host(inet_client_addr())
        """
    )
    row = cursor.fetchone()
    server_ssl = str(row[0]).lower() if row else "unknown"
    password_encryption = str(row[1]).lower() if row else "unknown"
    transport_encrypted = bool(row and row[2])
    admin_scram_verifier = bool(row and row[3])
    client_address = str(row[4]) if row and row[4] is not None else ""
    if password_encryption != "scram-sha-256":
        issues.append("server password_encryption is not scram-sha-256")
    if not admin_scram_verifier:
        issues.append("administrative role does not store a SCRAM-SHA-256 verifier")

    private_transition = args.sslmode == "disable"
    if private_transition:
        if server_ssl != "off":
            issues.append("private SCRAM transition is allowed only while server ssl=off")
        if transport_encrypted:
            issues.append("sslmode=disable unexpectedly negotiated encrypted transport")
        try:
            connected_address = ipaddress.ip_address(client_address)
        except ValueError:
            issues.append("private SCRAM transition requires a remote client address")
        else:
            if connected_address.is_loopback or not connected_address.is_private:
                issues.append("private SCRAM transition client address is not private remote")
        cursor.execute(
            """
            SELECT rule_number, line_number, type, database, user_name, address, netmask,
                   auth_method, error
            FROM pg_catalog.pg_hba_file_rules
            ORDER BY rule_number
            """
        )
        issues.extend(_runtime_hba_issues(cursor.fetchall(), client_address))
    else:
        if server_ssl != "on":
            issues.append("TLS mode requires server ssl=on")
        if not transport_encrypted:
            issues.append("TLS mode did not negotiate encrypted transport")

    return issues, {
        "sslmode": args.sslmode,
        "transport_encrypted": transport_encrypted,
        "auth": (
            "scram-sha-256"
            if password_encryption == "scram-sha-256" and admin_scram_verifier
            else "invalid"
        ),
        "private_transition": private_transition,
    }


def _target_preflight(cursor: Any) -> list[str]:
    issues: list[str] = []
    cursor.execute(
        """
        SELECT current_database(), current_user, session_user, r.rolsuper
        FROM pg_catalog.pg_roles AS r
        WHERE r.rolname = current_user
        """
    )
    row = cursor.fetchone()
    if not row or tuple(row[:3]) != (DATABASE, OWNER_ROLE, OWNER_ROLE) or row[3] is not True:
        issues.append("connection is not the fixed postgres owner session on news")
    cursor.execute(
        """
        SELECT pg_catalog.pg_get_userbyid(n.nspowner),
               pg_catalog.pg_get_userbyid(d.datdba)
        FROM pg_catalog.pg_namespace AS n
        CROSS JOIN pg_catalog.pg_database AS d
        WHERE n.nspname = %s AND d.datname = current_database()
        """,
        (SCHEMA,),
    )
    ownership = cursor.fetchone()
    if not ownership or ownership[1] != OWNER_ROLE or ownership[0] not in {
        OWNER_ROLE,
        "pg_database_owner",
    }:
        issues.append("public schema ownership is outside the fixed postgres owner contract")
    return issues


def _object_inventory(cursor: Any) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    cursor.execute(
        """
        SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner), c.relkind
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'f', 'v', 'm')
        """,
        (SCHEMA,),
    )
    tables = {
        str(name): (str(owner), str(relation_kind))
        for name, owner, relation_kind in cursor.fetchall()
    }
    cursor.execute(
        """
        SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner)
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind = 'S'
        """,
        (SCHEMA,),
    )
    sequences = {str(name): str(owner) for name, owner in cursor.fetchall()}
    return tables, sequences


def _required_object_issues(cursor: Any) -> list[str]:
    tables, sequences = _object_inventory(cursor)
    issues = [
        f"required table missing: {SCHEMA}.{name}"
        for name in ALL_REQUIRED_TABLES
        if name not in tables
    ]
    issues.extend(
        f"required sequence missing: {SCHEMA}.{name}"
        for name in ALL_REQUIRED_SEQUENCES
        if name not in sequences
    )
    issues.extend(
        f"unexpected table owner: {SCHEMA}.{name}"
        for name in ALL_REQUIRED_TABLES
        if name in tables and tables[name][0] != OWNER_ROLE
    )
    issues.extend(
        f"unexpected relation kind: {SCHEMA}.{name}"
        for name in ALL_REQUIRED_TABLES
        if name in tables and tables[name][1] not in {"r", "p"}
    )
    issues.extend(
        f"unexpected sequence owner: {SCHEMA}.{name}"
        for name in ALL_REQUIRED_SEQUENCES
        if name in sequences and sequences[name] != OWNER_ROLE
    )
    return issues


def _role_memberships(cursor: Any, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT parent.rolname
        FROM pg_catalog.pg_auth_members AS membership
        JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
        JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
        WHERE member.rolname = %s
        ORDER BY parent.rolname
        """,
        (role_name,),
    )
    return [str(row[0]) for row in cursor.fetchall()]


def _role_grantees(cursor: Any, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT member.rolname
        FROM pg_catalog.pg_auth_members AS membership
        JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
        JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
        WHERE parent.rolname = %s
        ORDER BY member.rolname
        """,
        (role_name,),
    )
    return [str(row[0]) for row in cursor.fetchall()]


def _role_owned_objects(cursor: Any, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT 'relation:' || n.nspname || '.' || c.relname
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_roles AS r ON r.oid = c.relowner
        WHERE r.rolname = %s
        UNION ALL
        SELECT 'routine:' || n.nspname || '.' || p.proname || '/' || p.oid::text
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        JOIN pg_catalog.pg_roles AS r ON r.oid = p.proowner
        WHERE r.rolname = %s
        UNION ALL
        SELECT 'schema:' || n.nspname
        FROM pg_catalog.pg_namespace AS n
        JOIN pg_catalog.pg_roles AS r ON r.oid = n.nspowner
        WHERE r.rolname = %s
        UNION ALL
        SELECT 'database:' || d.datname
        FROM pg_catalog.pg_database AS d
        JOIN pg_catalog.pg_roles AS r ON r.oid = d.datdba
        WHERE r.rolname = %s
        ORDER BY 1
        """,
        (role_name, role_name, role_name, role_name),
    )
    return [str(row[0]) for row in cursor.fetchall()]


def _column_acl_grants(cursor: Any, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT n.nspname, c.relname, a.attname, expanded.privilege_type,
               coalesce(grantee.rolname, 'PUBLIC'), expanded.is_grantable
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) AS expanded
        LEFT JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = expanded.grantee
        WHERE n.nspname = %s
          AND (expanded.grantee = 0 OR grantee.rolname = %s)
        ORDER BY 1, 2, 3, 4, 5
        """,
        (SCHEMA, role_name),
    )
    return ["/".join(str(value) for value in row) for row in cursor.fetchall()]


def _grant_options(cursor: Any, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT object_kind, object_name, privilege_type
        FROM (
            SELECT 'relation' AS object_kind,
                   n.nspname || '.' || c.relname AS object_name,
                   expanded.privilege_type,
                   expanded.is_grantable,
                   expanded.grantee
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) AS expanded
            WHERE n.nspname = %s
            UNION ALL
            SELECT 'schema', n.nspname, expanded.privilege_type,
                   expanded.is_grantable, expanded.grantee
            FROM pg_catalog.pg_namespace AS n
            CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) AS expanded
            WHERE n.nspname = %s
            UNION ALL
            SELECT 'database', d.datname, expanded.privilege_type,
                   expanded.is_grantable, expanded.grantee
            FROM pg_catalog.pg_database AS d
            CROSS JOIN LATERAL pg_catalog.aclexplode(d.datacl) AS expanded
            WHERE d.datname = %s
        ) AS grants
        JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = grants.grantee
        WHERE grantee.rolname = %s AND grants.is_grantable
        ORDER BY 1, 2, 3
        """,
        (SCHEMA, SCHEMA, DATABASE, role_name),
    )
    return ["/".join(str(value) for value in row) for row in cursor.fetchall()]


def _executable_security_definers(cursor: Any, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT p.oid::regprocedure::text
        FROM pg_catalog.pg_proc AS p
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = %s
          AND p.prosecdef
          AND pg_catalog.has_function_privilege(%s, p.oid, 'EXECUTE')
        ORDER BY 1
        """,
        (SCHEMA, role_name),
    )
    return [str(row[0]) for row in cursor.fetchall()]


def _large_object_privilege_count(cursor: Any, role_name: str) -> int:
    cursor.execute(
        """
        SELECT count(DISTINCT object.oid)
        FROM pg_catalog.pg_largeobject_metadata AS object
        JOIN pg_catalog.pg_roles AS role ON role.rolname = %s
        LEFT JOIN LATERAL pg_catalog.aclexplode(
            coalesce(
                object.lomacl,
                pg_catalog.acldefault('L'::"char", object.lomowner)
            )
        ) AS expanded ON true
        WHERE object.lomowner = role.oid
           OR expanded.grantee IN (0, role.oid)
        """,
        (role_name,),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _other_schema_privilege_issues(cursor: Any, role_name: str) -> list[str]:
    issues: list[str] = []
    cursor.execute(
        """
        SELECT nspname
        FROM pg_catalog.pg_namespace
        WHERE nspname <> %s
          AND nspname <> 'information_schema'
          AND nspname !~ '^pg_'
        ORDER BY nspname
        """,
        (SCHEMA,),
    )
    other_schemas = [str(row[0]) for row in cursor.fetchall()]
    for schema_name in other_schemas:
        for privilege in ("USAGE", "CREATE"):
            if _has_privilege(
                cursor,
                "has_schema_privilege",
                role_name,
                schema_name,
                privilege,
            ):
                issues.append(f"unexpected schema privilege: {schema_name}/{privilege}")

    cursor.execute(
        """
        SELECT n.nspname, c.relname, c.relkind
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname <> %s
          AND n.nspname <> 'information_schema'
          AND n.nspname !~ '^pg_'
          AND c.relkind IN ('r', 'p', 'f', 'v', 'm', 'S')
        ORDER BY 1, 2
        """,
        (SCHEMA,),
    )
    for schema_name, object_name, relation_kind in cursor.fetchall():
        qualified = f"{schema_name}.{object_name}"
        if relation_kind == "S":
            probes = ALLOWED_SEQUENCE_PRIVILEGES
            function_name = "has_sequence_privilege"
        else:
            probes = _table_privilege_probes(cursor)
            function_name = "has_table_privilege"
        if any(
            _has_privilege(cursor, function_name, role_name, qualified, privilege)
            for privilege in sorted(probes)
        ):
            issues.append(f"unexpected object privilege: {qualified}")

    if _large_object_privilege_count(cursor, role_name) > 0:
        issues.append("unexpected large-object privileges")
    return issues


def _default_acl_grants(cursor: Any, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT owner.rolname, coalesce(n.nspname, '*'), defaults.defaclobjtype,
               expanded.privilege_type
        FROM pg_catalog.pg_default_acl AS defaults
        JOIN pg_catalog.pg_roles AS owner ON owner.oid = defaults.defaclrole
        LEFT JOIN pg_catalog.pg_namespace AS n ON n.oid = defaults.defaclnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS expanded
        JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = expanded.grantee
        WHERE grantee.rolname = %s
        ORDER BY 1, 2, 3, 4
        """,
        (role_name,),
    )
    return ["/".join(str(value) for value in row) for row in cursor.fetchall()]


def _public_default_acl_grants(cursor: Any) -> list[str]:
    cursor.execute(
        """
        SELECT owner.rolname, coalesce(n.nspname, '*'), defaults.defaclobjtype,
               expanded.privilege_type
        FROM pg_catalog.pg_default_acl AS defaults
        JOIN pg_catalog.pg_roles AS owner ON owner.oid = defaults.defaclrole
        LEFT JOIN pg_catalog.pg_namespace AS n ON n.oid = defaults.defaclnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS expanded
        WHERE expanded.grantee = 0
          AND defaults.defaclobjtype IN ('r', 'S')
        ORDER BY 1, 2, 3, 4
        """
    )
    return ["/".join(str(value) for value in row) for row in cursor.fetchall()]


def _database_role_settings(cursor: Any, role_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT setting
        FROM pg_catalog.pg_db_role_setting AS configured
        JOIN pg_catalog.pg_roles AS role ON role.oid = configured.setrole
        JOIN pg_catalog.pg_database AS database ON database.oid = configured.setdatabase
        CROSS JOIN LATERAL unnest(configured.setconfig) AS setting
        WHERE role.rolname = %s AND database.datname = %s
        ORDER BY setting
        """,
        (role_name, DATABASE),
    )
    return [str(row[0]) for row in cursor.fetchall()]


def _set_role_password(cursor: Any, role_name: str, verifier: str) -> None:
    if role_name not in ROLE_BY_NAME:
        raise PolicyError("password target is not a fixed runtime role")
    _validate_scram_verifier(verifier)
    cursor.execute(f"ALTER ROLE {role_name} PASSWORD %s", (verifier,))


def _ensure_role(cursor: Any, policy: RolePolicy, verifier: str) -> None:
    cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (policy.name,))
    if cursor.fetchone() is None:
        cursor.execute(f"CREATE ROLE {policy.name}")
    memberships = _role_memberships(cursor, policy.name)
    if memberships:
        raise PolicyError("runtime roles must not be members of other roles")
    if _role_grantees(cursor, policy.name):
        raise PolicyError("runtime roles must not be granted to other roles")
    owned_objects = _role_owned_objects(cursor, policy.name)
    if owned_objects:
        raise PolicyError("runtime roles must not own database objects")
    cursor.execute(
        f"""
        ALTER ROLE {policy.name}
        WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
             NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {policy.connection_limit}
             VALID UNTIL 'infinity'
        """
    )
    _set_role_password(cursor, policy.name, verifier)
    cursor.execute(f"ALTER ROLE {policy.name} RESET ALL")
    cursor.execute(f"ALTER ROLE {policy.name} IN DATABASE {DATABASE} RESET ALL")
    cursor.execute(f"ALTER ROLE {policy.name} SET search_path TO public, pg_catalog")
    cursor.execute(f"ALTER ROLE {policy.name} SET statement_timeout TO '60s'")
    cursor.execute(f"ALTER ROLE {policy.name} SET lock_timeout TO '5s'")
    cursor.execute(f"ALTER ROLE {policy.name} SET idle_in_transaction_session_timeout TO '60s'")


def _reset_and_grant(cursor: Any, policy: RolePolicy) -> None:
    role = policy.name
    cursor.execute(f"REVOKE ALL PRIVILEGES ON DATABASE {DATABASE} FROM {role}")
    cursor.execute(f"GRANT CONNECT ON DATABASE {DATABASE} TO {role}")
    cursor.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA {SCHEMA} FROM {role}")
    cursor.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}")
    cursor.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {SCHEMA} FROM {role}")
    cursor.execute(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {SCHEMA} FROM {role}")
    cursor.execute(f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA {SCHEMA} FROM {role}")
    for table, privileges in policy.table_privileges.items():
        cursor.execute(f"GRANT {', '.join(privileges)} ON TABLE {SCHEMA}.{table} TO {role}")
    for sequence in policy.sequences:
        cursor.execute(f"GRANT USAGE, SELECT ON SEQUENCE {SCHEMA}.{sequence} TO {role}")
    for object_type in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        cursor.execute(
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA {SCHEMA}
            REVOKE ALL PRIVILEGES ON {object_type} FROM {role}
            """
        )


def _has_privilege(cursor: Any, function_name: str, role: str, obj: str, privilege: str) -> bool:
    if function_name not in {
        "has_database_privilege",
        "has_schema_privilege",
        "has_table_privilege",
        "has_sequence_privilege",
    }:
        raise PolicyError("unsupported privilege probe")
    cursor.execute(f"SELECT pg_catalog.{function_name}(%s, %s, %s)", (role, obj, privilege))
    row = cursor.fetchone()
    return bool(row and row[0])


def _table_privilege_probes(cursor: Any) -> frozenset[str]:
    cursor.execute("SELECT current_setting('server_version_num')::integer")
    row = cursor.fetchone()
    privileges = set(ALLOWED_TABLE_PRIVILEGES)
    if row and int(row[0]) >= 170000:
        privileges.add("MAINTAIN")
    return frozenset(privileges)


def _role_attribute_issues(cursor: Any, policy: RolePolicy) -> list[str]:
    cursor.execute(
        """
        SELECT role.rolcanlogin, role.rolsuper, role.rolcreatedb,
               role.rolcreaterole, role.rolinherit, role.rolreplication,
               role.rolbypassrls, role.rolconnlimit,
               (role.rolvaliduntil IS NULL
                OR role.rolvaliduntil > now() + interval '365 days'),
               coalesce(
                   (
                       SELECT configured.setconfig
                       FROM pg_catalog.pg_db_role_setting AS configured
                       WHERE configured.setrole = role.oid
                         AND configured.setdatabase = 0
                   ),
                   ARRAY[]::text[]
               ),
               coalesce(left(role.rolpassword, 14) = 'SCRAM-SHA-256$', false)
        FROM pg_catalog.pg_authid AS role
        WHERE role.rolname = %s
        """,
        (policy.name,),
    )
    row = cursor.fetchone()
    if row is None:
        return ["role is missing"]
    issues: list[str] = []
    expected_flags = (True, False, False, False, False, False, False)
    if tuple(row[:7]) != expected_flags:
        issues.append("role attributes violate the runtime policy")
    if int(row[7]) != policy.connection_limit:
        issues.append("role connection limit differs from policy")
    if row[8] is not True:
        issues.append("role password validity is too short or expired")
    actual_settings = {}
    for raw in row[9] or ():
        name, separator, value = str(raw).partition("=")
        if separator:
            actual_settings[name] = value
    if actual_settings != EXPECTED_ROLE_SETTINGS:
        issues.append("role settings differ from policy")
    if row[10] is not True:
        issues.append("role password is not a SCRAM-SHA-256 verifier")
    if _role_memberships(cursor, policy.name):
        issues.append("role has inherited or set-role memberships")
    if _role_grantees(cursor, policy.name):
        issues.append("role is granted to another role")
    if _role_owned_objects(cursor, policy.name):
        issues.append("role owns database objects")
    if _default_acl_grants(cursor, policy.name):
        issues.append("role receives default privileges")
    if _database_role_settings(cursor, policy.name):
        issues.append("role has database-specific setting overrides")
    if _column_acl_grants(cursor, policy.name):
        issues.append("role receives unmanaged column privileges")
    if _grant_options(cursor, policy.name):
        issues.append("role has privilege grant options")
    if _executable_security_definers(cursor, policy.name):
        issues.append("role can execute SECURITY DEFINER routines in public")
    issues.extend(_other_schema_privilege_issues(cursor, policy.name))
    return issues


def _role_privilege_issues(cursor: Any, policy: RolePolicy) -> list[str]:
    role = policy.name
    issues: list[str] = []
    if not _has_privilege(cursor, "has_database_privilege", role, DATABASE, "CONNECT"):
        issues.append("database CONNECT is missing")
    if _has_privilege(cursor, "has_database_privilege", role, DATABASE, "CREATE"):
        issues.append("database CREATE is forbidden")
    if not _has_privilege(cursor, "has_schema_privilege", role, SCHEMA, "USAGE"):
        issues.append("schema USAGE is missing")
    if _has_privilege(cursor, "has_schema_privilege", role, SCHEMA, "CREATE"):
        issues.append("schema CREATE is forbidden")

    tables, sequences = _object_inventory(cursor)
    for table in tables:
        expected = set(policy.table_privileges.get(table, ()))
        for privilege in sorted(_table_privilege_probes(cursor)):
            actual = _has_privilege(
                cursor,
                "has_table_privilege",
                role,
                f"{SCHEMA}.{table}",
                privilege,
            )
            if actual != (privilege in expected):
                issues.append(f"table privilege mismatch: {SCHEMA}.{table}/{privilege}")
    for sequence in sequences:
        expected = {"USAGE", "SELECT"} if sequence in policy.sequences else set()
        for privilege in sorted(ALLOWED_SEQUENCE_PRIVILEGES):
            actual = _has_privilege(
                cursor,
                "has_sequence_privilege",
                role,
                f"{SCHEMA}.{sequence}",
                privilege,
            )
            if actual != (privilege in expected):
                issues.append(f"sequence privilege mismatch: {SCHEMA}.{sequence}/{privilege}")
    return issues


def verify_connection(connection: Any, args: argparse.Namespace) -> dict[str, Any]:
    role_reports: dict[str, Any] = {}
    global_issues: list[str] = []
    with connection.cursor() as cursor:
        global_issues.extend(_target_preflight(cursor))
        transport_issues, transport_report = _transport_preflight(cursor, args)
        global_issues.extend(transport_issues)
        global_issues.extend(_required_object_issues(cursor))
        if _public_default_acl_grants(cursor):
            global_issues.append("PUBLIC receives table or sequence default privileges")
        for policy in ROLE_POLICIES:
            issues = _role_attribute_issues(cursor, policy)
            if not any(issue == "role is missing" for issue in issues):
                issues.extend(_role_privilege_issues(cursor, policy))
            role_reports[policy.name] = {
                "ready": not issues,
                "issue_count": len(issues),
                "issues": sorted(set(issues)),
            }
    ready = not global_issues and all(report["ready"] for report in role_reports.values())
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "mode": "verify",
        "status": "ready" if ready else "not_ready",
        "target": {"database": DATABASE, "schema": SCHEMA, "owner": OWNER_ROLE},
        "transport": transport_report,
        "global_issues": sorted(set(global_issues)),
        "roles": role_reports,
    }


def dry_run_report() -> dict[str, Any]:
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "mode": "dry-run",
        "status": "planned",
        "target": {"database": DATABASE, "schema": SCHEMA, "owner": OWNER_ROLE},
        "fixed_global_changes": [
            "revoke CREATE on schema public from PUBLIC",
            "remove direct and postgres-owned default privileges for runtime roles",
            "grant only fixed existing-object allowlists",
            "derive role SCRAM-SHA-256 verifiers locally before ALTER ROLE",
        ],
        "legacy_relation_gaps": {
            name: {
                "mount": gap["mount"],
                "current_behavior": gap["current_behavior"],
                "relations": list(gap["relations"]),
                "grant_policy": "not_granted_missing_object",
            }
            for name, gap in LEGACY_RELATION_GAPS.items()
        },
        "resolved_legacy_surfaces": {
            name: {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in surface.items()
            }
            for name, surface in RESOLVED_LEGACY_SURFACES.items()
        },
        "roles": {
            policy.name: {
                "connection_limit": policy.connection_limit,
                "attributes": {
                    "login": True,
                    "superuser": False,
                    "createdb": False,
                    "createrole": False,
                    "inherit": False,
                    "replication": False,
                    "bypassrls": False,
                },
                "tables": {
                    table: list(privileges) for table, privileges in policy.table_privileges.items()
                },
                "sequences": list(policy.sequences),
                "default_privileges": "none",
            }
            for policy in ROLE_POLICIES
        },
    }


def _verify(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    password = _read_secret_file(args.admin_password_file, require_strong=False)
    connection = _connect_admin(args, password)
    try:
        connection.set_session(readonly=True, autocommit=False)
        report = verify_connection(connection, args)
        connection.rollback()
    finally:
        connection.close()
    return report, 0 if report["status"] == "ready" else 2


def _apply(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    admin_password = _read_secret_file(args.admin_password_file, require_strong=False)
    web_password = _read_secret_file(args.web_password_file, require_strong=True)
    loader_password = _read_secret_file(args.loader_password_file, require_strong=True)
    if len({admin_password, web_password, loader_password}) != 3:
        raise PolicyError("admin and runtime roles must use distinct passwords")
    role_verifiers = {
        "web_runtime": _scram_sha_256_verifier(web_password),
        "wave1_loader": _scram_sha_256_verifier(loader_password),
    }
    del web_password, loader_password
    connection = _connect_admin(args, admin_password)
    try:
        connection.autocommit = False
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(908731, 2)")
            transport_issues, _transport_report = _transport_preflight(cursor, args)
            preflight_issues = (
                _target_preflight(cursor)
                + transport_issues
                + _required_object_issues(cursor)
            )
            if preflight_issues:
                raise PolicyError("database ownership or required-object preflight failed")
            cursor.execute(f"REVOKE CREATE ON SCHEMA {SCHEMA} FROM PUBLIC")
            for object_type in ("TABLES", "SEQUENCES"):
                cursor.execute(
                    f"""
                    ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA {SCHEMA}
                    REVOKE ALL PRIVILEGES ON {object_type} FROM PUBLIC
                    """
                )
            for policy in ROLE_POLICIES:
                _ensure_role(cursor, policy, role_verifiers[policy.name])
                _reset_and_grant(cursor, policy)
        report = verify_connection(connection, args)
        if report["status"] != "ready":
            raise PolicyError("post-provision verification failed")
        connection.commit()
        report["mode"] = "apply"
        report["status"] = "applied"
        return report, 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument(
        "--sslmode",
        choices=("verify-full", "require", "disable"),
        default="verify-full",
    )
    parser.add_argument(
        "--allow-private-scram-transport",
        action="store_true",
        help="Allow the audited ssl=off private-network transition mode.",
    )
    parser.add_argument("--connect-timeout", type=int, default=10)
    parser.add_argument("--admin-password-file", type=Path, required=True)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage the fixed GlobeMind PostgreSQL runtime-role policy."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "dry-run", help="Print the fixed policy without connecting or reading secrets."
    )
    verify_parser = commands.add_parser(
        "verify", help="Read-only verification of the fixed policy."
    )
    _add_connection_arguments(verify_parser)
    apply_parser = commands.add_parser("apply", help="Apply the fixed policy transactionally.")
    _add_connection_arguments(apply_parser)
    apply_parser.add_argument("--web-password-file", type=Path, required=True)
    apply_parser.add_argument("--loader-password-file", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if getattr(args, "port", 1) < 1 or getattr(args, "port", 1) > 65535:
        parser.error("--port must be between 1 and 65535")
    if getattr(args, "connect_timeout", 1) < 1 or getattr(args, "connect_timeout", 1) > 60:
        parser.error("--connect-timeout must be between 1 and 60")
    if args.command in {"verify", "apply"}:
        try:
            _validate_transport_arguments(args)
        except PolicyError as exc:
            parser.error(str(exc))
    return args


def _emit(report: dict[str, Any]) -> None:
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def main(argv: Iterable[str] | None = None) -> int:
    try:
        _validate_policy()
        args = parse_args(argv)
        if args.command == "dry-run":
            _emit(dry_run_report())
            return 0
        if args.command == "verify":
            report, exit_code = _verify(args)
        else:
            report, exit_code = _apply(args)
        _emit(report)
        return exit_code
    except PolicyError as exc:
        _emit(
            {
                "schema_version": POLICY_SCHEMA_VERSION,
                "status": "error",
                "error": str(exc),
            }
        )
        return 2
    except Exception:
        _emit(
            {
                "schema_version": POLICY_SCHEMA_VERSION,
                "status": "error",
                "error": "unexpected database role operation failure",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
