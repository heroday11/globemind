#!/usr/bin/env python3
"""Fail closed on unsupported strong claims in bounded public frontend source."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPOSITORY_ROOT / "config" / "public-claim-policy.json"

CONFIG_INVALID = "CLM_CONFIG_INVALID"
CONFIG_MISSING = "CLM_CONFIG_MISSING"
SCOPE_INVALID = "CLM_SCOPE_INVALID"

_ALLOWED_SUFFIXES = frozenset({".vue", ".js", ".ts", ".tsx"})
_ALLOWED_EVIDENCE_KINDS = frozenset({"catalog", "method", "status"})
_ALLOWED_CLASSIFICATIONS = frozenset(
    {"evidence_backed", "historical", "qualified", "ui_state"}
)
_MAX_POLICY_RULES = 32
_MAX_PATTERN_LENGTH = 1_024
_HARD_MAX_FILES = 512
_HARD_MAX_FILE_BYTES = 1_048_576
_HARD_MAX_TOTAL_BYTES = 16_777_216
_HARD_MAX_POLICY_BYTES = 1_048_576
_MASKED_VUE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL)
_MARKUP_TAG_RE = re.compile(r"<[^>\n]*>")
_CLAUSE_BOUNDARIES = frozenset("。；;!?！？，,")


class ClaimPolicyError(RuntimeError):
    """Raised when the checked-in claim policy cannot be trusted."""


def _reject_duplicate_policy_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ClaimPolicyError(f"policy contains duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_non_finite_policy_number(value: str) -> None:
    raise ClaimPolicyError(f"policy contains non-finite JSON number: {value}")


def _path_has_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe = probe / part
        if probe.is_symlink():
            return True
    return False


@dataclass(frozen=True, order=True)
class Finding:
    locator: str
    line: int
    rule_code: str

    def public_payload(self) -> dict[str, object]:
        return {
            "locator": self.locator,
            "line": self.line,
            "rule_code": self.rule_code,
        }


@dataclass(frozen=True)
class CompiledRule:
    code: str
    pattern: re.Pattern[str]
    safe_context_patterns: tuple[re.Pattern[str], ...]
    suffixes: frozenset[str]


@dataclass(frozen=True)
class EvidenceMapping:
    source_locator: str
    rule_code: str
    anchor_pattern: re.Pattern[str]
    classification: str
    evidence_locators: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ClaimPolicy:
    automation_state: str
    include_roots: tuple[str, ...]
    exclusions: tuple[str, ...]
    suffixes: frozenset[str]
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    rules: tuple[CompiledRule, ...]
    evidence_mappings: tuple[EvidenceMapping, ...]


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClaimPolicyError(f"{field} must be a non-empty string")
    return value.strip()


def _bounded_int(value: object, field: str, hard_limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ClaimPolicyError(f"{field} must be a positive integer")
    if value > hard_limit:
        raise ClaimPolicyError(f"{field} exceeds the hard safety limit")
    return value


def _relative_locator(value: object, field: str) -> str:
    locator = _nonempty_string(value, field).replace("\\", "/")
    path = PurePosixPath(locator)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ClaimPolicyError(f"{field} must be a normalized repository-relative locator")
    if any(part in {"current", "previous", "rejected", "releases"} for part in path.parts):
        raise ClaimPolicyError(f"{field} crosses the production release boundary")
    return path.as_posix()


def _compile_pattern(value: object, field: str, *, ignore_case: bool = False) -> re.Pattern[str]:
    source = _nonempty_string(value, field)
    if len(source) > _MAX_PATTERN_LENGTH:
        raise ClaimPolicyError(f"{field} exceeds the maximum pattern length")
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(source, flags)
    except re.error as exc:
        raise ClaimPolicyError(f"{field} is not a valid regular expression") from exc


def _resolve_existing(root: Path, locator: str, field: str) -> Path:
    root_resolved = root.resolve()
    path = root_resolved / locator
    if _path_has_symlink_component(path):
        raise ClaimPolicyError(f"{field} must not contain a symlink")
    resolved = path.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise ClaimPolicyError(f"{field} escapes the repository")
    if not resolved.exists():
        raise ClaimPolicyError(f"{field} does not exist")
    metadata = resolved.stat()
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
        raise ClaimPolicyError(f"{field} must not be hard-linked")
    return resolved


def validate_policy(payload: object, repository_root: Path) -> ClaimPolicy:
    if not isinstance(payload, dict):
        raise ClaimPolicyError("policy root must be an object")
    if payload.get("schema_version") != 1:
        raise ClaimPolicyError("schema_version must be 1")

    automation = payload.get("automation")
    if not isinstance(automation, dict):
        raise ClaimPolicyError("automation must be an object")
    automation_state = automation.get("state")
    if automation_state not in {"configured", "not_configured"}:
        raise ClaimPolicyError("automation.state must be configured or not_configured")
    scheduler_locator = automation.get("scheduler_locator")
    retention_locator = automation.get("artifact_retention_locator")
    if automation_state == "configured":
        scheduler = _relative_locator(
            scheduler_locator,
            "automation.scheduler_locator",
        )
        retention = _relative_locator(
            retention_locator,
            "automation.artifact_retention_locator",
        )
        _resolve_existing(repository_root, scheduler, "automation.scheduler_locator")
        _resolve_existing(repository_root, retention, "automation.artifact_retention_locator")
    elif scheduler_locator is not None or retention_locator is not None:
        raise ClaimPolicyError("not_configured automation must not declare automation locators")
    else:
        _nonempty_string(automation.get("reason_code"), "automation.reason_code")

    scope = payload.get("scope")
    if not isinstance(scope, dict):
        raise ClaimPolicyError("scope must be an object")
    raw_roots = scope.get("include_roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise ClaimPolicyError("scope.include_roots must be a non-empty array")
    include_roots = tuple(
        _relative_locator(value, f"scope.include_roots[{index}]")
        for index, value in enumerate(raw_roots)
    )
    if len(set(include_roots)) != len(include_roots):
        raise ClaimPolicyError("scope.include_roots contains duplicates")
    for index, locator in enumerate(include_roots):
        path = _resolve_existing(repository_root, locator, f"scope.include_roots[{index}]")
        if not path.is_dir():
            raise ClaimPolicyError(f"scope.include_roots[{index}] must be a directory")

    raw_exclusions = scope.get("exclusions", [])
    if not isinstance(raw_exclusions, list):
        raise ClaimPolicyError("scope.exclusions must be an array")
    exclusions: list[str] = []
    for index, item in enumerate(raw_exclusions):
        if not isinstance(item, dict):
            raise ClaimPolicyError(f"scope.exclusions[{index}] must be an object")
        locator = _relative_locator(item.get("locator"), f"scope.exclusions[{index}].locator")
        _nonempty_string(item.get("classification"), f"scope.exclusions[{index}].classification")
        _nonempty_string(item.get("reason_code"), f"scope.exclusions[{index}].reason_code")
        _resolve_existing(repository_root, locator, f"scope.exclusions[{index}].locator")
        exclusions.append(locator)
    if len(set(exclusions)) != len(exclusions):
        raise ClaimPolicyError("scope.exclusions contains duplicate locators")

    raw_suffixes = scope.get("suffixes")
    if not isinstance(raw_suffixes, list) or not raw_suffixes:
        raise ClaimPolicyError("scope.suffixes must be a non-empty array")
    suffixes = frozenset(
        _nonempty_string(value, f"scope.suffixes[{index}]")
        for index, value in enumerate(raw_suffixes)
    )
    if not suffixes <= _ALLOWED_SUFFIXES:
        raise ClaimPolicyError("scope.suffixes contains an unsupported source suffix")

    max_files = _bounded_int(scope.get("max_files"), "scope.max_files", _HARD_MAX_FILES)
    max_file_bytes = _bounded_int(
        scope.get("max_file_bytes"),
        "scope.max_file_bytes",
        _HARD_MAX_FILE_BYTES,
    )
    max_total_bytes = _bounded_int(
        scope.get("max_total_bytes"),
        "scope.max_total_bytes",
        _HARD_MAX_TOTAL_BYTES,
    )

    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ClaimPolicyError("rules must be a non-empty array")
    if len(raw_rules) > _MAX_POLICY_RULES:
        raise ClaimPolicyError("rules exceeds the hard safety limit")
    rules: list[CompiledRule] = []
    codes: list[str] = []
    for index, item in enumerate(raw_rules):
        prefix = f"rules[{index}]"
        if not isinstance(item, dict):
            raise ClaimPolicyError(f"{prefix} must be an object")
        code = _nonempty_string(item.get("code"), f"{prefix}.code")
        if not re.fullmatch(r"CLM_[A-Z0-9_]+", code):
            raise ClaimPolicyError(f"{prefix}.code is invalid")
        codes.append(code)
        ignore_case = item.get("ignore_case", False)
        if not isinstance(ignore_case, bool):
            raise ClaimPolicyError(f"{prefix}.ignore_case must be boolean")
        pattern = _compile_pattern(
            item.get("pattern"),
            f"{prefix}.pattern",
            ignore_case=ignore_case,
        )
        raw_safe = item.get("safe_context_patterns", [])
        if not isinstance(raw_safe, list):
            raise ClaimPolicyError(f"{prefix}.safe_context_patterns must be an array")
        safe_patterns = tuple(
            _compile_pattern(
                value,
                f"{prefix}.safe_context_patterns[{safe_index}]",
                ignore_case=ignore_case,
            )
            for safe_index, value in enumerate(raw_safe)
        )
        raw_rule_suffixes = item.get("suffixes", list(suffixes))
        if not isinstance(raw_rule_suffixes, list) or not raw_rule_suffixes:
            raise ClaimPolicyError(f"{prefix}.suffixes must be a non-empty array")
        rule_suffixes = frozenset(
            _nonempty_string(value, f"{prefix}.suffixes[{suffix_index}]")
            for suffix_index, value in enumerate(raw_rule_suffixes)
        )
        if not rule_suffixes <= suffixes:
            raise ClaimPolicyError(f"{prefix}.suffixes must be inside scope.suffixes")
        rules.append(
            CompiledRule(
                code=code,
                pattern=pattern,
                safe_context_patterns=safe_patterns,
                suffixes=rule_suffixes,
            )
        )
    if len(set(codes)) != len(codes):
        raise ClaimPolicyError("rules contains duplicate codes")

    raw_mappings = payload.get("evidence_mappings", [])
    if not isinstance(raw_mappings, list):
        raise ClaimPolicyError("evidence_mappings must be an array")
    mappings: list[EvidenceMapping] = []
    for index, item in enumerate(raw_mappings):
        prefix = f"evidence_mappings[{index}]"
        if not isinstance(item, dict):
            raise ClaimPolicyError(f"{prefix} must be an object")
        source_locator = _relative_locator(item.get("source_locator"), f"{prefix}.source_locator")
        source_path = _resolve_existing(repository_root, source_locator, f"{prefix}.source_locator")
        if not source_path.is_file() or source_path.suffix not in suffixes:
            raise ClaimPolicyError(f"{prefix}.source_locator must be an in-scope source file")
        if not any(
            source_locator == root_locator or source_locator.startswith(f"{root_locator}/")
            for root_locator in include_roots
        ):
            raise ClaimPolicyError(f"{prefix}.source_locator is outside include_roots")
        if any(_locator_contains(source_locator, excluded) for excluded in exclusions):
            raise ClaimPolicyError(f"{prefix}.source_locator is excluded from scanning")
        rule_code = _nonempty_string(item.get("rule_code"), f"{prefix}.rule_code")
        if rule_code not in codes:
            raise ClaimPolicyError(f"{prefix}.rule_code is unknown")
        classification = _nonempty_string(
            item.get("classification"),
            f"{prefix}.classification",
        )
        if classification not in _ALLOWED_CLASSIFICATIONS:
            raise ClaimPolicyError(f"{prefix}.classification is invalid")
        mapping_ignore_case = item.get("ignore_case", False)
        if not isinstance(mapping_ignore_case, bool):
            raise ClaimPolicyError(f"{prefix}.ignore_case must be boolean")
        anchor_pattern = _compile_pattern(
            item.get("claim_anchor_pattern"),
            f"{prefix}.claim_anchor_pattern",
            ignore_case=mapping_ignore_case,
        )
        raw_evidence = item.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise ClaimPolicyError(f"{prefix}.evidence must be a non-empty array")
        evidence_locators: list[tuple[str, str]] = []
        for evidence_index, evidence in enumerate(raw_evidence):
            evidence_prefix = f"{prefix}.evidence[{evidence_index}]"
            if not isinstance(evidence, dict):
                raise ClaimPolicyError(f"{evidence_prefix} must be an object")
            kind = _nonempty_string(evidence.get("kind"), f"{evidence_prefix}.kind")
            if kind not in _ALLOWED_EVIDENCE_KINDS:
                raise ClaimPolicyError(f"{evidence_prefix}.kind is invalid")
            locator = _relative_locator(evidence.get("locator"), f"{evidence_prefix}.locator")
            evidence_path = _resolve_existing(
                repository_root,
                locator,
                f"{evidence_prefix}.locator",
            )
            if not evidence_path.is_file():
                raise ClaimPolicyError(f"{evidence_prefix}.locator must be a file")
            evidence_locators.append((kind, locator))
        mappings.append(
            EvidenceMapping(
                source_locator=source_locator,
                rule_code=rule_code,
                anchor_pattern=anchor_pattern,
                classification=classification,
                evidence_locators=tuple(evidence_locators),
            )
        )

    return ClaimPolicy(
        automation_state=automation_state,
        include_roots=include_roots,
        exclusions=tuple(exclusions),
        suffixes=suffixes,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        rules=tuple(rules),
        evidence_mappings=tuple(mappings),
    )


def load_policy(path: Path, repository_root: Path = REPOSITORY_ROOT) -> ClaimPolicy:
    if not path.exists():
        raise FileNotFoundError(path)
    root_resolved = repository_root.resolve(strict=True)
    if _path_has_symlink_component(path):
        raise ClaimPolicyError("policy must not contain a symlink")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root_resolved):
        raise ClaimPolicyError("policy must be inside the repository")
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(resolved, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ClaimPolicyError("policy must be a regular file")
        if before.st_nlink != 1:
            raise ClaimPolicyError("policy must not be hard-linked")
        if before.st_size > _HARD_MAX_POLICY_BYTES:
            raise ClaimPolicyError("policy exceeds the hard byte limit")
        encoded = b""
        while len(encoded) <= _HARD_MAX_POLICY_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, _HARD_MAX_POLICY_BYTES + 1 - len(encoded)),
            )
            if not chunk:
                break
            encoded += chunk
        after = os.fstat(descriptor)
        if (
            after.st_nlink != 1
            or after.st_size != len(encoded)
            or len(encoded) > _HARD_MAX_POLICY_BYTES
        ):
            raise ClaimPolicyError("policy integrity changed while being read")
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_policy_keys,
            parse_constant=_reject_non_finite_policy_number,
        )
    except ClaimPolicyError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClaimPolicyError("policy is not readable JSON") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return validate_policy(payload, root_resolved)


def _locator_contains(locator: str, excluded: str) -> bool:
    return locator == excluded or locator.startswith(f"{excluded}/")


def _source_files(repository_root: Path, policy: ClaimPolicy) -> tuple[Path, ...]:
    root_resolved = repository_root.resolve()
    paths: dict[str, Path] = {}
    for root_locator in policy.include_roots:
        include_root = (root_resolved / root_locator).resolve()
        for path in include_root.rglob("*"):
            if _path_has_symlink_component(path):
                raise ClaimPolicyError("source scope contains a symlink")
            if not path.is_file() or path.suffix not in policy.suffixes:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root_resolved):
                raise ClaimPolicyError("source scope escapes the repository")
            metadata = resolved.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ClaimPolicyError("source scope contains a non-regular file")
            if metadata.st_nlink != 1:
                raise ClaimPolicyError("source scope contains a hard-linked file")
            locator = resolved.relative_to(root_resolved).as_posix()
            if any(_locator_contains(locator, excluded) for excluded in policy.exclusions):
                continue
            paths[locator] = resolved
            if len(paths) > policy.max_files:
                raise ClaimPolicyError("source scope exceeds max_files")
    return tuple(paths[key] for key in sorted(paths))


def _read_source(path: Path, *, max_bytes: int) -> tuple[str, int]:
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ClaimPolicyError("source file is not regular")
        if before.st_nlink != 1:
            raise ClaimPolicyError("source file must not be hard-linked")
        if before.st_size > max_bytes:
            raise ClaimPolicyError("source file exceeds max_file_bytes")
        encoded = b""
        while len(encoded) <= max_bytes:
            chunk = os.read(
                descriptor,
                min(64 * 1024, max_bytes + 1 - len(encoded)),
            )
            if not chunk:
                break
            encoded += chunk
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_nlink != 1
            or after.st_size != len(encoded)
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or len(encoded) > max_bytes
        ):
            raise ClaimPolicyError("source file integrity changed while being read")
        try:
            return encoded.decode("utf-8"), len(encoded)
        except UnicodeError as exc:
            raise ClaimPolicyError("source file is not readable UTF-8") from exc
    except ClaimPolicyError:
        raise
    except OSError as exc:
        raise ClaimPolicyError("source file is not readable UTF-8") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _masked_source(path: Path, text: str) -> str:
    if path.suffix != ".vue":
        return text

    def mask(match: re.Match[str]) -> str:
        value = match.group(0)
        return "".join("\n" if character == "\n" else " " for character in value)

    return _MASKED_VUE_BLOCK_RE.sub(mask, text)


def _line_context(text: str, start: int) -> tuple[int, str]:
    line = text.count("\n", 0, start) + 1
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    context_start = line_start
    cursor = start - 1
    while cursor >= line_start:
        if text[cursor] in _CLAUSE_BOUNDARIES:
            context_start = cursor + 1
            break
        cursor -= 1
    context_end = line_end
    cursor = start
    while cursor < line_end:
        if text[cursor] in _CLAUSE_BOUNDARIES:
            context_end = cursor
            break
        cursor += 1
    return line, text[context_start:context_end]


def _mask_markup_for_qualifiers(context: str) -> str:
    return _MARKUP_TAG_RE.sub(
        lambda match: " " * len(match.group(0)),
        context,
    )


def _is_unquoted_markup_syntax(text: str, start: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    open_index = text.rfind("<", line_start, start)
    close_index = text.rfind(">", line_start, start)
    if open_index <= close_index:
        return False
    prefix = text[open_index + 1 : start]
    if re.match(r"/?[A-Za-z][^>]*$", prefix) is None:
        return False
    return prefix.count('"') % 2 == 0 and prefix.count("'") % 2 == 0


def _is_supported(
    *,
    locator: str,
    rule: CompiledRule,
    context: str,
    mappings: Iterable[EvidenceMapping],
) -> bool:
    qualifier_context = _mask_markup_for_qualifiers(context)
    if any(pattern.search(qualifier_context) for pattern in rule.safe_context_patterns):
        return True
    return any(
        mapping.source_locator == locator
        and mapping.rule_code == rule.code
        and mapping.anchor_pattern.search(context)
        for mapping in mappings
    )


def scan_repository(repository_root: Path, policy: ClaimPolicy) -> tuple[Finding, ...]:
    root_resolved = repository_root.resolve()
    total_bytes = 0
    findings: set[Finding] = set()
    for path in _source_files(root_resolved, policy):
        source, size = _read_source(path, max_bytes=policy.max_file_bytes)
        total_bytes += size
        if total_bytes > policy.max_total_bytes:
            raise ClaimPolicyError("source scope exceeds max_total_bytes")
        source = _masked_source(path, source)
        locator = path.relative_to(root_resolved).as_posix()
        for rule in policy.rules:
            if path.suffix not in rule.suffixes:
                continue
            for match in rule.pattern.finditer(source):
                if _is_unquoted_markup_syntax(source, match.start()):
                    continue
                line, context = _line_context(source, match.start())
                if _is_supported(
                    locator=locator,
                    rule=rule,
                    context=context,
                    mappings=policy.evidence_mappings,
                ):
                    continue
                findings.add(Finding(locator=locator, line=line, rule_code=rule.code))
    return tuple(sorted(findings))


def audit_repository(
    repository_root: Path = REPOSITORY_ROOT,
    policy_path: Path = DEFAULT_POLICY,
) -> tuple[ClaimPolicy, tuple[Finding, ...]]:
    policy = load_policy(policy_path, repository_root)
    return policy, scan_repository(repository_root, policy)


def _policy_output_locator(policy_path: Path, repository_root: Path) -> str:
    try:
        resolved = policy_path.resolve()
        root = repository_root.resolve()
        return resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return "claim-policy"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    locator = _policy_output_locator(args.policy, REPOSITORY_ROOT)
    try:
        policy, findings = audit_repository(REPOSITORY_ROOT, args.policy)
    except FileNotFoundError:
        findings = (Finding(locator=locator, line=0, rule_code=CONFIG_MISSING),)
        automation_state = "unknown"
        exit_code = 2
    except ClaimPolicyError:
        findings = (Finding(locator=locator, line=0, rule_code=CONFIG_INVALID),)
        automation_state = "unknown"
        exit_code = 2
    else:
        automation_state = policy.automation_state
        exit_code = 1 if findings else 0

    if args.format == "json":
        print(
            json.dumps(
                {
                    "automation_state": automation_state,
                    "findings": [finding.public_payload() for finding in findings],
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        for finding in findings:
            print(f"{finding.locator}:{finding.line}:{finding.rule_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
