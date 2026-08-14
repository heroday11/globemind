from __future__ import annotations

import contextlib
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping

import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

import promote_web_release as promotion_cli  # noqa: E402
from promote_web_release import build_parser  # noqa: E402
from web_promotion import (  # noqa: E402
    AtomicLinkManager,
    AuditTrail,
    ControllerError,
    HealthGate,
    PreflightBuilder,
    ProcessInspector,
    PromotionApplyError,
    PromotionConfig,
    PromotionError,
    PromotionJournal,
    PromotionTransaction,
    ReleaseIdentity,
    ReleaseVerifier,
    SubprocessController,
    controller_environment,
    create_credential,
    database_password_file_record,
    load_credential,
    sha256_file,
    write_credential,
)


def _write(path: Path, value: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)
    return path


def _config(tmp_path: Path, **overrides: Any) -> PromotionConfig:
    project = tmp_path / "project"
    releases = tmp_path / "releases"
    runtimes = tmp_path / "runtimes"
    project.mkdir(exist_ok=True)
    releases.mkdir(exist_ok=True)
    runtimes.mkdir(exist_ok=True)
    target = releases / "1.0.0-build"
    current = releases / "0.11.0-build"
    previous = releases / "0.10.0-build"
    for path in (target, current, previous):
        path.mkdir(exist_ok=True)
    current_link = releases / "current"
    previous_link = releases / "previous"
    if not current_link.exists() and not current_link.is_symlink():
        current_link.symlink_to(current)
    if not previous_link.exists() and not previous_link.is_symlink():
        previous_link.symlink_to(previous)
    controller = _write(
        project / "deploy/start_web_prod.sh",
        "#!/bin/sh\nexit 0\n",
        0o700,
    )
    verifier = _write(
        project / "deploy/verify_release.py",
        "raise SystemExit(0)\n",
        0o600,
    )
    _write(project / "deploy/release_lib.py", "# fixture\n", 0o600)
    verify_python = Path("/usr/bin/python3").resolve()
    env_file = _write(project / "api.env", "APP_ENV=production\n", 0o600)
    database_password_file = _write(
        tmp_path / "secrets/web_runtime.password",
        "fixture-database-password\n",
        0o600,
    )
    values: dict[str, Any] = {
        "request_id": "release-1.0.0-test",
        "project_dir": project.resolve(),
        "release_root": releases.resolve(),
        "runtime_root": runtimes.resolve(),
        "target_release": target.resolve(),
        "current_link": current_link.absolute(),
        "previous_link": previous_link.absolute(),
        "controller": controller.resolve(),
        "verifier": verifier.resolve(),
        "verify_python": verify_python,
        "pid_file": (tmp_path / "web/pids/prod.pid").absolute(),
        "environment_files": (env_file.resolve(),),
        "database_password_file": database_password_file.absolute(),
        "generated_asset_root": (tmp_path / "generated").absolute(),
        "audit_root": (tmp_path / "audit").absolute(),
        "port": 18089,
        "credential_ttl_seconds": 60,
    }
    values.update(overrides)
    return PromotionConfig(**values)


def _release(path: Path, version: str, build_id: str) -> dict[str, str]:
    return {
        "path": str(path),
        "version": version,
        "build_id": build_id,
        "git_sha": "a" * 40,
        "runtime_version": version,
        "manifest_sha256": "b" * 64,
    }


def _facts(config: PromotionConfig) -> dict[str, Any]:
    target = _release(config.target_release, "1.0.0", "1.0.0-build")
    rollback_path = config.release_root / "0.11.0-build"
    previous_path = config.release_root / "0.10.0-build"
    rollback = _release(rollback_path, "0.11.0", "0.11.0-build")
    previous = _release(previous_path, "0.10.0", "0.10.0-build")
    target_runtime = config.runtime_root / "1.0.0"
    rollback_runtime = config.runtime_root / "0.11.0"
    return {
        "links": {
            "current_target": str(rollback_path),
            "previous_target": str(previous_path),
        },
        "target": target,
        "rollback": rollback,
        "prior_previous": previous,
        "releases": {
            str(config.target_release): {
                **target,
                "runtime": str(target_runtime),
                "runtime_manifest": str(target_runtime / "inventory/runtime.json"),
            },
            str(rollback_path): {
                **rollback,
                "runtime": str(rollback_runtime),
                "runtime_manifest": str(rollback_runtime / "inventory/runtime.json"),
            },
            str(previous_path): previous,
        },
        "environment_files": [],
        "tools": {},
        "current_process": {
            "pid": 100,
            "start_ticks": 900,
            "workers": [{"pid": 101, "start_ticks": 901}],
        },
        "current_health": {"ready": True},
        "target_environment": {
            "PATH": "/usr/bin:/bin",
            "BUILD_ID": "1.0.0-build",
            "APP_VERSION": "1.0.0",
        },
        "rollback_environment": {
            "PATH": "/usr/bin:/bin",
            "BUILD_ID": "0.11.0-build",
            "APP_VERSION": "0.11.0",
        },
    }


class FakePreflight:
    def __init__(
        self,
        facts: dict[str, Any],
        *,
        recovery_links: FakeLinks | None = None,
    ) -> None:
        self.facts = facts
        self.calls = 0
        self.recovery_calls = 0
        self.recovery_links = recovery_links

    def capture(self, _config: PromotionConfig) -> dict[str, Any]:
        self.calls += 1
        return self.facts

    def validate_recovery(
        self,
        _config: PromotionConfig,
        _facts: Mapping[str, Any],
        *,
        allow_tool_drift: bool = False,
    ) -> dict[str, str]:
        self.recovery_calls += 1
        _ = allow_tool_drift
        if self.recovery_links is not None:
            return self.recovery_links.inspect()
        return dict(self.facts["links"])

    def assert_bound_inputs(
        self,
        _config: PromotionConfig,
        _facts: Mapping[str, Any],
    ) -> None:
        return None


class FakeController:
    def __init__(
        self,
        *,
        fail_target_start: bool = False,
        fail_rollback_start: bool = False,
        fail_rollback_stop: bool = False,
        fail_target_stop: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_target_start = fail_target_start
        self.fail_rollback_start = fail_rollback_start
        self.fail_rollback_stop = fail_rollback_stop
        self.fail_target_stop = fail_target_stop

    def run(self, action: str, environment: Mapping[str, str]) -> dict[str, Any]:
        build_id = environment["BUILD_ID"]
        self.calls.append((action, build_id))
        if self.fail_target_start and (action, build_id) == ("start", "1.0.0-build"):
            raise ControllerError(
                "fixture target start failed",
                {"action": action, "returncode": 1},
            )
        if self.fail_rollback_start and (action, build_id) == ("start", "0.11.0-build"):
            raise ControllerError(
                "fixture rollback start failed",
                {"action": action, "returncode": 1},
            )
        if self.fail_rollback_stop and (action, build_id) == ("stop", "0.11.0-build"):
            raise ControllerError(
                "fixture rollback stop failed",
                {"action": action, "returncode": 1},
            )
        if self.fail_target_stop and (action, build_id) == ("stop", "1.0.0-build"):
            raise ControllerError(
                "fixture target stop failed",
                {"action": action, "returncode": 1},
            )
        return {"action": action, "returncode": 0, "build_id": build_id}


class FakeInspector:
    def __init__(
        self,
        *,
        fail_target_dead: bool = False,
        fail_target_port: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.fail_target_dead = fail_target_dead
        self.fail_target_port = fail_target_port
        self.port_calls = 0

    def wait_dead(self, identity: Mapping[str, Any], _timeout: float) -> None:
        self.calls.append("dead")
        if self.fail_target_dead and identity.get("pid") == 200:
            raise PromotionError("fixture target remained alive")

    def wait_port_free(self, _timeout: float) -> None:
        self.calls.append("port-free")
        self.port_calls += 1
        if self.fail_target_port and self.port_calls >= 2:
            raise PromotionError("fixture target port remained open")

    def wait_running(self, release: Path, _runtime: Path, _timeout: float) -> dict[str, Any]:
        self.calls.append(f"running:{release.name}")
        return {
            "pid": 200,
            "start_ticks": 1000,
            "workers": [{"pid": 201, "start_ticks": 1001}],
        }


class FakeHealth:
    def __init__(self, *, fail_target: bool = False) -> None:
        self.calls: list[str] = []
        self.fail_target = fail_target

    def wait(
        self,
        identity: ReleaseIdentity,
        _process: Mapping[str, Any],
        _timeout: float,
    ) -> dict[str, Any]:
        self.calls.append(identity.build_id)
        if self.fail_target and identity.build_id == "1.0.0-build":
            raise PromotionError("fixture scheduler gate failed")
        return {
            "ready": True,
            "release": {"build_id": identity.build_id},
            "scheduler": {"state": "running"},
        }


class FakeLinks:
    def __init__(
        self,
        current: Path | None = None,
        previous: Path | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.current = current
        self.previous = previous

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.calls.append("lock")
        yield

    def inspect(self) -> dict[str, str]:
        if self.current is None or self.previous is None:
            raise AssertionError("fake link state was not initialized")
        return {
            "current_target": str(self.current),
            "previous_target": str(self.previous),
        }

    def promote(
        self,
        target: Path,
        expected_current: Path,
        _expected_previous: Path,
    ) -> None:
        self.calls.append(f"promote:{target.name}:{expected_current.name}")
        self.current = target
        self.previous = expected_current

    def restore(self, old_current: Path, old_previous: Path) -> None:
        self.calls.append(f"restore:{old_current.name}:{old_previous.name}")
        self.current = old_current
        self.previous = old_previous


def _credential(
    tmp_path: Path,
    config: PromotionConfig,
    facts: dict[str, Any],
    *,
    now: int = 1_000,
) -> tuple[Path, str]:
    value = create_credential(config, facts, now=now, nonce="c" * 32)
    path = tmp_path / "credential.json"
    return path, write_credential(path, value)


def _transaction(
    config: PromotionConfig,
    facts: dict[str, Any],
    *,
    controller: FakeController | None = None,
    health: FakeHealth | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[PromotionTransaction, FakeController, FakeInspector, FakeHealth, FakeLinks]:
    actual_controller = controller or FakeController()
    inspector = FakeInspector()
    actual_health = health or FakeHealth()
    links = FakeLinks(
        Path(facts["links"]["current_target"]),
        Path(facts["links"]["previous_target"]),
    )
    transaction = PromotionTransaction(
        config,
        preflight=FakePreflight(facts, recovery_links=links),
        controller=actual_controller,
        inspector=inspector,
        health=actual_health,
        links=links,
        clock=clock or (lambda: 1_001),
    )
    return transaction, actual_controller, inspector, actual_health, links


def _begin_interrupted_audit(
    transaction: PromotionTransaction,
    config: PromotionConfig,
    credential_path: Path,
    credential_sha256: str,
    *,
    phase: str = "prepared",
) -> AuditTrail:
    credential = load_credential(
        credential_path,
        credential_sha256,
        config,
        now=1_001,
    )
    audit = AuditTrail(config.audit_root, config.request_id, credential["nonce"])
    audit.record(
        "credential",
        "accepted",
        {
            "credential_path": str(credential_path),
            "credential_sha256": credential_sha256,
            "preflight_credential": credential,
        },
    )
    transaction.journal.begin(credential, credential_sha256, audit.directory)
    if phase != "prepared":
        transaction.journal.update(credential, credential_sha256, phase)
    return audit


def test_cli_defaults_to_dry_run() -> None:
    args = build_parser().parse_args(
        [
            "--request-id",
            "release-1.0.0-test",
            "--target-release",
            "/tmp/target",
            "--env-file",
            "/tmp/api.env",
        ]
    )

    assert args.apply is False
    assert args.recover is False
    assert args.credential is None
    assert args.credential_sha256 is None
    assert args.database_password_file == promotion_cli.PRODUCTION_DATABASE_PASSWORD_FILE


def test_production_cli_database_password_path_is_anchored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(promotion_cli, "PRODUCTION_PROJECT_DIR", config.project_dir)
    monkeypatch.setattr(promotion_cli, "PRODUCTION_RELEASE_ROOT", config.release_root)
    monkeypatch.setattr(promotion_cli, "PRODUCTION_RUNTIME_ROOT", config.runtime_root)
    monkeypatch.setattr(promotion_cli, "PRODUCTION_CURRENT_LINK", config.current_link)
    monkeypatch.setattr(promotion_cli, "PRODUCTION_PREVIOUS_LINK", config.previous_link)
    monkeypatch.setattr(promotion_cli, "PRODUCTION_PID_FILE", config.pid_file)
    monkeypatch.setattr(
        promotion_cli,
        "PRODUCTION_GENERATED_ASSET_ROOT",
        config.generated_asset_root,
    )
    monkeypatch.setattr(promotion_cli, "PRODUCTION_AUDIT_ROOT", config.audit_root)
    monkeypatch.setattr(
        promotion_cli,
        "PRODUCTION_ENVIRONMENT_FILES",
        config.environment_files,
    )
    monkeypatch.setattr(
        promotion_cli,
        "PRODUCTION_DATABASE_PASSWORD_FILE",
        config.database_password_file,
    )
    values = {
        **config.__dict__,
        "env_file": list(config.environment_files),
        "database_password_file": config.database_password_file,
        "stop_timeout": config.stop_timeout_seconds,
        "controller_timeout": config.controller_timeout_seconds,
        "health_timeout": config.health_timeout_seconds,
        "scheduler_max_heartbeat_age": config.scheduler_max_heartbeat_age_seconds,
        "credential_ttl": config.credential_ttl_seconds,
    }
    for replaced in (
        "environment_files",
        "stop_timeout_seconds",
        "controller_timeout_seconds",
        "health_timeout_seconds",
        "scheduler_max_heartbeat_age_seconds",
        "credential_ttl_seconds",
    ):
        values.pop(replaced)

    accepted = promotion_cli._config(SimpleNamespace(**values))
    assert accepted.database_password_file == config.database_password_file

    alternate = _write(tmp_path / "secrets/alternate.password", "alternate\n", 0o600)
    values["database_password_file"] = alternate
    with pytest.raises(PromotionError, match="path anchors cannot be overridden"):
        promotion_cli._config(SimpleNamespace(**values))


@pytest.mark.parametrize("host", ["localhost", "192.0.2.10"])
def test_config_rejects_nonliteral_or_nonloopback_health_hosts(
    tmp_path: Path,
    host: str,
) -> None:
    config = _config(tmp_path, host=host)

    with pytest.raises(PromotionError, match="literal loopback"):
        config.validate()


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o700])
def test_config_requires_database_password_file_mode_0600(
    tmp_path: Path,
    mode: int,
) -> None:
    config = _config(tmp_path)
    config.database_password_file.chmod(mode)

    with pytest.raises(PromotionError, match="mode must be exactly 0600"):
        config.validate()


def test_config_rejects_symlinked_or_foreign_database_password_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    link = tmp_path / "secrets/password-link"
    link.symlink_to(config.database_password_file)
    linked = PromotionConfig(
        **{**config.__dict__, "database_password_file": link.absolute()}
    )

    with pytest.raises(PromotionError, match="non-symlink regular file"):
        linked.validate()

    monkeypatch.setattr(
        "web_promotion.os.geteuid",
        lambda: config.database_password_file.stat().st_uid + 1,
    )
    with pytest.raises(PromotionError, match="not owned by the effective user"):
        config.validate()


def test_request_binds_database_password_metadata_without_exposing_secret(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    payload = config.request_payload()
    record = payload["database_password_file"]

    assert record == database_password_file_record(config.database_password_file)
    assert record["path"] == str(config.database_password_file)
    assert record["sha256"] == sha256_file(config.database_password_file)
    assert record["mode"] == "0600"
    assert "fixture-database-password" not in json.dumps(payload)


def test_controller_environment_forces_web_runtime_database_identity(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    identity = ReleaseIdentity(
        path=config.target_release,
        version="1.0.0",
        build_id="1.0.0-build",
        git_sha="a" * 40,
        runtime_version="1.0.0",
        manifest_sha256="b" * 64,
    )
    runtime = config.runtime_root / identity.runtime_version
    manifest = runtime / "inventory/runtime.json"

    environment = controller_environment(config, identity, runtime, manifest)
    database_environment = {
        key: environment[key]
        for key in (
            "DB_USER",
            "GLOBEMIND_DB_PASSWORD_FILE",
            "DB_SSLMODE",
            "GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT",
            "PGOPTIONS",
            "L1_DB_HOST",
            "L1_DB_PORT",
            "L1_DB_USER",
            "L1_DB_NAME",
            "OPINION_DB_HOST",
            "OPINION_DB_PORT",
            "OPINION_DB_USER",
            "OPINION_DB_NAME",
        )
    }

    assert database_environment == {
        "DB_USER": "web_runtime",
        "GLOBEMIND_DB_PASSWORD_FILE": str(config.database_password_file),
        "DB_SSLMODE": "disable",
        "GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT": "1",
        "PGOPTIONS": "-c max_parallel_workers_per_gather=0",
        "L1_DB_HOST": "",
        "L1_DB_PORT": "",
        "L1_DB_USER": "",
        "L1_DB_NAME": "",
        "OPINION_DB_HOST": "",
        "OPINION_DB_PORT": "",
        "OPINION_DB_USER": "",
        "OPINION_DB_NAME": "",
    }
    assert environment["DB_POOL_TIMEOUT"] == "30"
    assert "DB_PASSWORD" not in environment
    assert "DATABASE_URL" not in environment
    assert "fixture-database-password" not in json.dumps(environment)


def test_credential_rejects_database_password_content_change(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path, digest = _credential(tmp_path, config, _facts(config))
    config.database_password_file.write_text("rotated-password\n", encoding="utf-8")
    config.database_password_file.chmod(0o600)

    with pytest.raises(PromotionError, match="different promotion inputs"):
        load_credential(path, digest, config, now=1_010)


def test_credential_is_content_bound_private_and_short_lived(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)

    loaded = load_credential(path, digest, config, now=1_010)

    assert loaded["facts"] == facts
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(PromotionError, match="not currently valid"):
        load_credential(path, digest, config, now=1_060)

    path.chmod(0o600)
    path.write_bytes(path.read_bytes().replace(b'"dry-run"', b'"apply!!"'))
    with pytest.raises(PromotionError, match="content digest"):
        load_credential(path, digest, config, now=1_010)


def test_credential_writer_refuses_to_replace_existing_evidence(tmp_path: Path) -> None:
    config = _config(tmp_path)
    value = create_credential(config, _facts(config), now=1_000, nonce="d" * 32)
    path = tmp_path / "credential.json"
    write_credential(path, value)

    with pytest.raises(PromotionError, match="already exists"):
        write_credential(path, value)


def test_apply_runs_exact_stop_link_start_and_health_order(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, controller, inspector, health, links = _transaction(config, facts)

    result = transaction.apply(path, digest)

    assert result["status"] == "promoted"
    assert controller.calls == [
        ("stop", "0.11.0-build"),
        ("start", "1.0.0-build"),
    ]
    assert inspector.calls == [
        "dead",
        "port-free",
        "running:1.0.0-build",
        "running:1.0.0-build",
    ]
    assert health.calls == ["1.0.0-build"]
    assert links.calls == ["lock", "promote:1.0.0-build:0.11.0-build"]
    audit = Path(result["audit_directory"])
    assert stat.S_IMODE(audit.stat().st_mode) == 0o550
    assert (audit / "SHA256SUMS").is_file()
    for line in (audit / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        digest_value, name = line.split("  ", 1)
        assert sha256_file(audit / name) == digest_value
    with pytest.raises(PromotionError, match="already consumed"):
        transaction.apply(path, digest)
    assert controller.calls == [
        ("stop", "0.11.0-build"),
        ("start", "1.0.0-build"),
    ]


def test_apply_allows_revalidation_to_finish_after_credential_expiry(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    clock_values = iter([1_001, 1_001, 1_061])
    transaction, controller, _inspector, _health, _links = _transaction(
        config,
        facts,
        clock=lambda: next(clock_values),
    )

    result = transaction.apply(path, digest)

    assert result["status"] == "promoted"
    assert controller.calls == [
        ("stop", "0.11.0-build"),
        ("start", "1.0.0-build"),
    ]
    audit = Path(result["audit_directory"])
    revalidation = json.loads((audit / "002-revalidation.json").read_text(encoding="utf-8"))
    assert revalidation["status"] == "passed"
    assert revalidation["details"] == {
        "completed_after_credential_expiry": True,
        "credential_valid_at_apply_start": True,
        "facts_match": True,
    }


def test_promotion_journal_creation_uses_single_link_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    credential = load_credential(path, digest, config, now=1_001)
    audit_dir = config.audit_root / "release-journal-test"
    audit_dir.mkdir(parents=True)

    journal = PromotionJournal(config)
    journal.begin(credential, digest, audit_dir)

    assert config.release_root.joinpath(".promotion-active.json").stat().st_nlink == 1


def test_audit_record_creation_uses_single_link_files(tmp_path: Path) -> None:
    config = _config(tmp_path)
    audit = AuditTrail(config.audit_root, config.request_id, "a" * 32)

    audit.record("credential", "accepted", {"ok": True})

    record = next(audit.directory.glob("001-credential.json"))
    assert record.stat().st_nlink == 1


@pytest.mark.parametrize("failure", ["start", "health"])
def test_failure_restores_links_and_old_release(
    tmp_path: Path,
    failure: str,
) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    controller = FakeController(fail_target_start=failure == "start")
    health = FakeHealth(fail_target=failure == "health")
    transaction, controller, inspector, health, links = _transaction(
        config,
        facts,
        controller=controller,
        health=health,
    )

    with pytest.raises(PromotionApplyError) as captured:
        transaction.apply(path, digest)

    assert captured.value.rollback_succeeded is True
    expected_controller = [
        ("stop", "0.11.0-build"),
        ("start", "1.0.0-build"),
    ]
    expected_controller.append(("stop", "1.0.0-build"))
    expected_controller.append(("start", "0.11.0-build"))
    assert controller.calls == expected_controller
    assert links.calls[-1] == "restore:0.11.0-build:0.10.0-build"
    assert inspector.calls[-2:] == [
        "running:0.11.0-build",
        "running:0.11.0-build",
    ]
    assert health.calls[-1] == "0.11.0-build"


def test_failed_old_stop_accepts_existing_healthy_rollback(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    controller = FakeController(fail_rollback_stop=True)
    transaction, controller, inspector, health, links = _transaction(
        config,
        facts,
        controller=controller,
    )

    with pytest.raises(PromotionApplyError) as captured:
        transaction.apply(path, digest)

    assert captured.value.rollback_succeeded is True
    assert controller.calls == [("stop", "0.11.0-build")]
    assert inspector.calls == [
        "running:0.11.0-build",
        "running:0.11.0-build",
    ]
    assert health.calls == ["0.11.0-build"]
    assert links.calls == ["lock", "restore:0.11.0-build:0.10.0-build"]
    assert not transaction.journal.path.exists()
    result_path = next(config.audit_root.glob("*/result.json"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "rolled_back"
    assert result["rollback_succeeded"] is True
    rollback_gate = next(config.audit_root.glob("*/005-rollback-gate.json"))
    gate = json.loads(rollback_gate.read_text(encoding="utf-8"))
    assert gate["details"]["already_running"] is True


@pytest.mark.parametrize("failure", ["stop", "death", "port"])
def test_unproven_target_cleanup_blocks_link_restore_and_old_start(
    tmp_path: Path,
    failure: str,
) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    controller = FakeController(fail_target_stop=failure == "stop")
    inspector = FakeInspector(
        fail_target_dead=failure == "death",
        fail_target_port=failure == "port",
    )
    health = FakeHealth(fail_target=True)
    links = FakeLinks()
    transaction = PromotionTransaction(
        config,
        preflight=FakePreflight(facts),
        controller=controller,
        inspector=inspector,
        health=health,
        links=links,
        clock=lambda: 1_001,
    )

    with pytest.raises(PromotionApplyError) as captured:
        transaction.apply(path, digest)

    assert captured.value.rollback_succeeded is False
    assert controller.calls == [
        ("stop", "0.11.0-build"),
        ("start", "1.0.0-build"),
        ("stop", "1.0.0-build"),
    ]
    assert not any(call.startswith("restore:") for call in links.calls)
    result_path = next(config.audit_root.glob("*/result.json"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "rollback_failed"
    assert "target cleanup was not proven" in result["rollback_error"]


def test_changed_facts_are_rejected_before_controller_call(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    changed = json.loads(json.dumps(facts))
    changed["current_process"]["start_ticks"] += 1
    controller = FakeController()
    links = FakeLinks()
    transaction = PromotionTransaction(
        config,
        preflight=FakePreflight(changed),
        controller=controller,
        inspector=FakeInspector(),
        health=FakeHealth(),
        links=links,
        clock=lambda: 1_001,
    )

    with pytest.raises(PromotionError, match="facts changed"):
        transaction.apply(path, digest)

    assert controller.calls == []
    assert links.calls == ["lock"]


def test_failed_rollback_is_a_distinct_incident_result(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    controller = FakeController(fail_target_start=True, fail_rollback_start=True)
    transaction, _, _, _, _ = _transaction(
        config,
        facts,
        controller=controller,
    )

    with pytest.raises(PromotionApplyError) as captured:
        transaction.apply(path, digest)

    assert captured.value.rollback_succeeded is False
    audit_directories = list(config.audit_root.iterdir())
    assert len(audit_directories) == 1
    result = json.loads((audit_directories[0] / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "rollback_failed"
    assert result["rollback_succeeded"] is False
    assert stat.S_IMODE(audit_directories[0].stat().st_mode) == 0o700
    assert not (audit_directories[0] / "SHA256SUMS").exists()


def test_recover_prepared_transaction_keeps_verified_old_release(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, controller, inspector, health, links = _transaction(config, facts)
    _begin_interrupted_audit(transaction, config, path, digest)

    result = transaction.recover(path, digest)

    assert result["status"] == "recovered"
    assert controller.calls == []
    assert inspector.calls == [
        "running:0.11.0-build",
        "running:0.11.0-build",
    ]
    assert health.calls == ["0.11.0-build"]
    assert links.calls == [
        "lock",
        "restore:0.11.0-build:0.10.0-build",
    ]
    assert not transaction.journal.path.exists()
    audit = Path(result["audit_directory"])
    assert stat.S_IMODE(audit.stat().st_mode) == 0o550
    assert (audit / "SHA256SUMS").is_file()


def test_recover_promoted_links_stops_target_before_restoring_and_starting_old(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, controller, inspector, health, links = _transaction(config, facts)
    links.current = config.target_release
    links.previous = Path(facts["links"]["current_target"])
    _begin_interrupted_audit(
        transaction,
        config,
        path,
        digest,
        phase="starting-target",
    )

    result = transaction.recover(path, digest)

    assert result["status"] == "recovered"
    assert controller.calls == [
        ("stop", "1.0.0-build"),
        ("start", "0.11.0-build"),
    ]
    assert inspector.calls == [
        "running:1.0.0-build",
        "dead",
        "port-free",
        "port-free",
        "running:0.11.0-build",
        "running:0.11.0-build",
    ]
    assert health.calls == ["0.11.0-build"]
    assert links.inspect() == facts["links"]


def test_recover_previous_first_intermediate_restores_both_links_and_old_service(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, controller, inspector, health, links = _transaction(config, facts)
    old_current = Path(facts["links"]["current_target"])
    links.current = old_current
    links.previous = old_current
    _begin_interrupted_audit(
        transaction,
        config,
        path,
        digest,
        phase="promoting-links",
    )

    result = transaction.recover(path, digest)

    assert result["status"] == "recovered"
    assert controller.calls == [("start", "0.11.0-build")]
    assert inspector.calls == [
        "port-free",
        "running:0.11.0-build",
        "running:0.11.0-build",
    ]
    assert health.calls == ["0.11.0-build"]
    assert links.inspect() == facts["links"]
    assert not transaction.journal.path.exists()


def test_recover_refuses_link_restore_when_target_stop_is_unproven(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    controller = FakeController(fail_target_stop=True)
    transaction, controller, _inspector, _health, links = _transaction(
        config,
        facts,
        controller=controller,
    )
    links.current = config.target_release
    links.previous = Path(facts["links"]["current_target"])
    audit = _begin_interrupted_audit(
        transaction,
        config,
        path,
        digest,
        phase="target-started",
    )

    with pytest.raises(PromotionApplyError) as captured:
        transaction.recover(path, digest)

    assert captured.value.rollback_succeeded is False
    assert controller.calls == [("stop", "1.0.0-build")]
    assert not any(call.startswith("restore:") for call in links.calls)
    assert transaction.journal.path.exists()
    checkpoint = json.loads((audit.directory / "result.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "recovery_failed"


def test_recovery_allows_expired_bound_credential_only_with_active_journal(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, controller, _inspector, _health, _links = _transaction(
        config,
        facts,
        clock=lambda: 2_000,
    )
    _begin_interrupted_audit(transaction, config, path, digest)

    result = transaction.recover(path, digest)

    assert result["status"] == "recovered"
    assert controller.calls == []
    with pytest.raises(PromotionError, match="not currently valid"):
        transaction.apply(path, digest)


def test_recovery_rejects_journal_content_mismatch_before_controller(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, controller, _inspector, _health, _links = _transaction(config, facts)
    _begin_interrupted_audit(transaction, config, path, digest)
    journal = json.loads(transaction.journal.path.read_text(encoding="utf-8"))
    journal["links"]["target"] = str(config.release_root / "not-the-target")
    transaction.journal.path.write_text(json.dumps(journal), encoding="utf-8")
    transaction.journal.path.chmod(0o600)

    with pytest.raises(PromotionError, match="does not match"):
        transaction.recover(path, digest)

    assert controller.calls == []


def test_recovery_cleans_journal_publish_hardlink_left_by_interruption(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, _controller, _inspector, _health, _links = _transaction(config, facts)
    _begin_interrupted_audit(transaction, config, path, digest)
    temporary = transaction.journal.path.with_name(
        f".{transaction.journal.path.name}.123.aaaaaaaaaaaa.tmp"
    )
    temporary.hardlink_to(transaction.journal.path)
    assert transaction.journal.path.stat().st_nlink == 2

    result = transaction.recover(path, digest)

    assert result["status"] == "recovered"
    assert not temporary.exists()


def test_recovery_clears_journal_after_revalidating_sealed_success(tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, controller, inspector, health, links = _transaction(config, facts)
    links.current = config.target_release
    links.previous = Path(facts["links"]["current_target"])
    audit = _begin_interrupted_audit(
        transaction,
        config,
        path,
        digest,
        phase="target-healthy",
    )
    audit.seal(
        {
            "schema_version": 1,
            "status": "promoted",
            "request_id": config.request_id,
            "target": facts["target"],
            "rollback_release": facts["rollback"],
            "audit_directory": str(audit.directory),
        }
    )

    result = transaction.recover(path, digest)

    assert result["status"] == "promoted"
    assert result["journal_recovery"] == "cleared-after-sealed-outcome-revalidation"
    assert controller.calls == []
    assert inspector.calls == [
        "running:1.0.0-build",
        "running:1.0.0-build",
    ]
    assert health.calls == ["1.0.0-build"]
    assert not transaction.journal.path.exists()


@pytest.mark.parametrize("partial_state", ["result-only", "checksums-unsealed"])
def test_recovery_finishes_partial_success_audit_seal(
    tmp_path: Path,
    partial_state: str,
) -> None:
    config = _config(tmp_path)
    facts = _facts(config)
    path, digest = _credential(tmp_path, config, facts)
    transaction, controller, _inspector, _health, links = _transaction(config, facts)
    links.current = config.target_release
    links.previous = Path(facts["links"]["current_target"])
    audit = _begin_interrupted_audit(
        transaction,
        config,
        path,
        digest,
        phase="target-healthy",
    )
    completed = {
        "schema_version": 1,
        "status": "promoted",
        "request_id": config.request_id,
        "target": facts["target"],
        "rollback_release": facts["rollback"],
        "audit_directory": str(audit.directory),
    }
    if partial_state == "result-only":
        _write(audit.directory / "result.json", json.dumps(completed), 0o600)
    else:
        audit.seal(completed)
        audit.directory.chmod(0o700)
        for evidence in audit.directory.iterdir():
            evidence.chmod(0o600)

    result = transaction.recover(path, digest)

    assert result["status"] == "promoted"
    assert controller.calls == []
    assert stat.S_IMODE(audit.directory.stat().st_mode) == 0o550
    assert all(
        stat.S_IMODE(evidence.stat().st_mode) == 0o440 for evidence in audit.directory.iterdir()
    )
    assert not transaction.journal.path.exists()


def test_audit_record_write_failure_never_exposes_partial_final_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    audit = AuditTrail(config.audit_root, config.request_id, "e" * 32)
    audit.record("first", "passed", {"complete": True})

    def fail_replace(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("fixture replace failure")

    with monkeypatch.context() as patch:
        patch.setattr("web_promotion.os.replace", fail_replace)
        with pytest.raises(OSError, match="fixture replace failure"):
            audit.record("second", "passed", {"complete": False})

    assert not (audit.directory / "002-second.json").exists()
    assert not list(audit.directory.glob(".*.tmp"))
    resumed = AuditTrail.resume(
        config.audit_root,
        config.request_id,
        "e" * 32,
        audit.directory,
    )
    record = resumed.record("second", "passed", {"complete": True})
    assert record.name == "002-second.json"


def test_audit_resume_cleans_hardlink_temporary_left_after_atomic_publish(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    audit = AuditTrail(config.audit_root, config.request_id, "f" * 32)
    audit.record("first", "passed", {"complete": True})
    final = audit.directory / "002-second.json"
    temporary = audit.directory / ".002-second.json.123.aaaaaaaaaaaa.tmp"
    value = {
        "schema_version": 1,
        "sequence": 2,
        "recorded_at": "2026-07-11T00:00:00Z",
        "phase": "second",
        "status": "passed",
        "details": {"complete": True},
    }
    _write(temporary, json.dumps(value), 0o600)
    final.hardlink_to(temporary)

    resumed = AuditTrail.resume(
        config.audit_root,
        config.request_id,
        "f" * 32,
        audit.directory,
    )

    assert resumed.sequence == 2
    assert final.stat().st_nlink == 1
    assert not temporary.exists()


def test_atomic_links_use_previous_then_current_and_restore(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = AtomicLinkManager(config)
    original = manager.inspect()

    manager.promote(
        config.target_release,
        Path(original["current_target"]),
        Path(original["previous_target"]),
    )

    assert manager.inspect() == {
        "current_target": str(config.target_release),
        "previous_target": original["current_target"],
    }
    manager.restore(Path(original["current_target"]), Path(original["previous_target"]))
    assert manager.inspect() == original
    assert list(config.release_root.glob(".*.tmp")) == []


def test_subprocess_controller_passes_only_exact_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    capture = tmp_path / "capture"
    controller = _write(
        config.controller,
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$CAPTURE.args"\nenv | LC_ALL=C sort > "$CAPTURE.env"\n',
        0o700,
    )
    config = PromotionConfig(**{**config.__dict__, "controller": controller.resolve()})
    monkeypatch.setenv("MUST_NOT_LEAK", "sensitive")

    result = SubprocessController(config).run(
        "start",
        {"PATH": "/usr/bin:/bin", "CAPTURE": str(capture), "BUILD_ID": "fixture"},
    )

    assert result["returncode"] == 0
    assert (tmp_path / "capture.args").read_text(encoding="utf-8").strip() == "start"
    environment = (tmp_path / "capture.env").read_text(encoding="utf-8")
    assert "BUILD_ID=fixture" in environment
    assert "MUST_NOT_LEAK" not in environment
    assert "stdout" not in result and "stderr" not in result
    with pytest.raises(PromotionError, match="exactly start or stop"):
        SubprocessController(config).run("restart", {})


def test_subprocess_controller_inherits_the_transaction_lock_fd(tmp_path: Path) -> None:
    config = _config(tmp_path)
    capture = tmp_path / "lock-capture"
    controller = _write(
        config.controller,
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'fd="$GLOBEMIND_PROMOTION_LOCK_FD"\n'
        'readlink -f "/proc/$$/fd/$fd" > "$CAPTURE.path"\n'
        'flock -n "$fd"\n',
        0o700,
    )
    config = PromotionConfig(**{**config.__dict__, "controller": controller.resolve()})

    with AtomicLinkManager(config).lock():
        result = SubprocessController(config).run(
            "stop",
            {"PATH": "/usr/bin:/bin", "CAPTURE": str(capture)},
        )

    assert result["returncode"] == 0
    assert (tmp_path / "lock-capture.path").read_text(encoding="utf-8").strip() == str(
        config.release_root / ".promotion.lock"
    )


def test_subprocess_controller_rejects_forged_lock_environment(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with pytest.raises(PromotionError, match="cannot supply the promotion lock lease"):
        SubprocessController(config).run(
            "stop",
            {
                "PATH": "/usr/bin:/bin",
                "GLOBEMIND_PROMOTION_LOCK_FD": "9",
            },
        )


def test_controller_failure_evidence_never_contains_raw_output(tmp_path: Path) -> None:
    config = _config(tmp_path)
    controller = _write(
        config.controller,
        "#!/bin/sh\n"
        "echo must-not-enter-audit\n"
        "echo secret-error-must-not-enter-audit >&2\n"
        "exit 9\n",
        0o700,
    )
    config = PromotionConfig(**{**config.__dict__, "controller": controller.resolve()})

    with pytest.raises(ControllerError) as captured:
        SubprocessController(config).run("stop", {"PATH": "/usr/bin:/bin"})

    serialized = json.dumps(captured.value.result)
    assert "must-not-enter-audit" not in serialized
    assert captured.value.result["returncode"] == 9
    assert captured.value.result["stdout_bytes"] > 0


def test_release_verifier_cannot_drop_production_or_runtime_binding(tmp_path: Path) -> None:
    config = _config(tmp_path)
    release = config.target_release
    runtime = config.runtime_root / "1.0.0"
    manifest = _write(runtime / "inventory/runtime.json", "{}\n", 0o600)
    capture = tmp_path / "verifier-args.json"
    verifier = _write(
        config.verifier,
        "import json,sys\n"
        f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'status':'verified','version':'1.0.0',"
        "'build_id':'1.0.0-build','git_sha':'" + "a" * 40 + "',"
        "'artifact_manifest_sha256':'" + "d" * 64 + "'}))\n",
        0o600,
    )
    config = PromotionConfig(**{**config.__dict__, "verifier": verifier.resolve()})
    identity = ReleaseIdentity(
        path=release,
        version="1.0.0",
        build_id="1.0.0-build",
        git_sha="a" * 40,
        runtime_version="1.0.0",
        manifest_sha256="b" * 64,
    )

    result = ReleaseVerifier(config).verify(identity, runtime, manifest)

    assert result["status"] == "verified"
    arguments = json.loads(capture.read_text(encoding="utf-8"))
    assert "--production" in arguments
    assert arguments[arguments.index("--python-runtime-dir") + 1] == str(runtime)
    assert arguments[arguments.index("--python-runtime-manifest") + 1] == str(manifest)
    assert "--allow-unverified" not in arguments
    assert "--allow-legacy" not in arguments


def _proc_stat(pid: int, ppid: int, pgid: int, sid: int, ticks: int) -> str:
    tail = ["S", str(ppid), str(pgid), str(sid), *("0" for _ in range(15)), str(ticks), "0"]
    return f"{pid} (python worker) {' '.join(tail)}\n"


def _make_proc(
    proc_root: Path,
    pid: int,
    *,
    ppid: int,
    pgid: int,
    sid: int,
    ticks: int,
    argv: list[str],
    runtime: Path,
    release: Path,
) -> None:
    root = proc_root / str(pid)
    (root / "fd").mkdir(parents=True)
    _write(root / "stat", _proc_stat(pid, ppid, pgid, sid, ticks), 0o600)
    (root / "cmdline").write_bytes(b"\0".join(value.encode() for value in argv) + b"\0")
    (root / "exe").symlink_to(runtime / "bin/python")
    (root / "cwd").symlink_to(release, target_is_directory=True)


def test_process_inspector_requires_strong_master_workers_and_listener(tmp_path: Path) -> None:
    config = _config(tmp_path)
    release = config.release_root / "0.11.0-build"
    runtime = config.runtime_root / "0.11.0"
    python = _write(runtime / "bin/python", "fixture\n", 0o700)
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    _write(
        proc / "sys/kernel/random/boot_id",
        "11111111-2222-3333-4444-555555555555\n",
        0o600,
    )
    pid = 100
    _make_proc(
        proc,
        pid,
        ppid=1,
        pgid=pid,
        sid=pid,
        ticks=900,
        argv=[str(python), "backend/serve_prod.py"],
        runtime=runtime,
        release=release,
    )
    (proc / str(pid) / "fd/3").symlink_to("socket:[777]")
    for index in range(4):
        worker = 101 + index
        _make_proc(
            proc,
            worker,
            ppid=pid,
            pgid=pid,
            sid=pid,
            ticks=901 + index,
            argv=[
                str(python),
                "-B",
                "-c",
                "from multiprocessing.spawn import spawn_main; spawn_main(pipe_handle=1)",
                "--multiprocessing-fork",
            ],
            runtime=runtime,
            release=release,
        )
    port_hex = f"{config.port:04X}"
    _write(
        proc / "net/tcp",
        "  sl  local_address rem_address st tx_queue tr tm->when retrnsmt uid timeout inode\n"
        f"0: 0100007F:{port_hex} 00000000:0000 0A 0:0 00:0 0 0 0 777\n",
        0o600,
    )
    _write(proc / "net/tcp6", "  sl local_address rem_address st\n", 0o600)
    _write(config.pid_file, f"{pid}\n", 0o640)
    _write(
        config.pid_file.with_name(f"{config.pid_file.name}.meta"),
        f"{pid} 900 18089 production\n",
        0o640,
    )

    observed = ProcessInspector(config, proc_root=proc).inspect(release, runtime)

    assert observed["pid"] == pid
    assert observed["boot_id"] == "11111111-2222-3333-4444-555555555555"
    assert observed["start_ticks"] == 900
    assert [worker["pid"] for worker in observed["workers"]] == [101, 102, 103, 104]
    _write(
        proc / "net/tcp",
        "  sl  local_address rem_address st tx_queue tr tm->when retrnsmt uid timeout inode\n"
        f"0: 00000000:{port_hex} 00000000:0000 0A 0:0 00:0 0 0 0 777\n",
        0o600,
    )
    with pytest.raises(PromotionError, match="declared TCP listener"):
        ProcessInspector(config, proc_root=proc).inspect(release, runtime)
    _write(
        proc / "net/tcp",
        "  sl  local_address rem_address st tx_queue tr tm->when retrnsmt uid timeout inode\n"
        f"0: 0100007F:{port_hex} 00000000:0000 0A 0:0 00:0 0 0 0 777\n",
        0o600,
    )
    _write(
        config.pid_file.with_name(f"{config.pid_file.name}.meta"),
        f"{pid} 899 18089 production\n",
        0o640,
    )
    with pytest.raises(PromotionError, match="start ticks changed"):
        ProcessInspector(config, proc_root=proc).inspect(release, runtime)


def test_health_gate_binds_scheduler_leader_to_verified_worker(tmp_path: Path) -> None:
    config = _config(tmp_path)
    identity = ReleaseIdentity(
        path=config.target_release,
        version="1.0.0",
        build_id="1.0.0-build",
        git_sha="a" * 40,
        runtime_version="1.0.0",
        manifest_sha256="b" * 64,
    )
    process = {"workers": [{"pid": 201, "start_ticks": 1001}]}
    payload = {
        "status": "healthy",
        "ready": True,
        "service": "globemind-api",
        "release": {
            "version": "1.0.0",
            "build_id": "1.0.0-build",
            "git_sha": "a" * 40,
        },
        "checks": {
            "database": {"status": "up"},
            "assistant_scheduler": {
                "enabled": True,
                "healthy": True,
                "state": "running",
                "leader_pid": 201,
                "leader_instance_id": "201-fixture",
                "heartbeat_age_seconds": 1.5,
            },
        },
    }

    accepted = HealthGate(config).validate(payload, identity, process)

    assert accepted["scheduler"]["leader_pid"] == 201
    payload["checks"]["assistant_scheduler"]["leader_pid"] = 999
    with pytest.raises(PromotionError, match="verified Web worker"):
        HealthGate(config).validate(payload, identity, process)


class _FixtureVerifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def verify(self, identity: ReleaseIdentity, _runtime: Path, _manifest: Path) -> dict[str, str]:
        self.calls.append(identity.build_id)
        return {
            "status": "verified",
            "version": identity.version,
            "build_id": identity.build_id,
            "git_sha": identity.git_sha,
            "artifact_manifest_sha256": "d" * 64,
        }


class _FixtureProcessInspector:
    def __init__(self) -> None:
        self.process = {
            "pid": 100,
            "start_ticks": 900,
            "workers": [{"pid": 101, "start_ticks": 901}],
        }
        self.calls = 0

    def inspect(self, _release: Path, _runtime: Path) -> dict[str, Any]:
        self.calls += 1
        return self.process


class _FixtureHealthGate:
    def wait(
        self,
        identity: ReleaseIdentity,
        _process: Mapping[str, Any],
        _timeout: float,
    ) -> dict[str, Any]:
        return {
            "ready": True,
            "release": {"build_id": identity.build_id},
            "scheduler": {"leader_pid": 101},
        }


def test_preflight_binds_three_verified_releases_runtime_tools_and_exact_env(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _write(config.verifier.parent / "release_lib.py", "# fixture\n", 0o600)
    releases = (
        (config.target_release, "1.0.0", "1.0.0-build"),
        (config.release_root / "0.11.0-build", "0.11.0", "0.11.0-build"),
        (config.release_root / "0.10.0-build", "0.10.0", "0.10.0-build"),
    )
    for release, version, build_id in releases:
        _write(
            release / "release.json",
            json.dumps(
                {
                    "schema_version": 3,
                    "version": version,
                    "build_id": build_id,
                    "git_sha": "a" * 40,
                    "python_runtime": {"version": version, "role": "web"},
                }
            ),
            0o444,
        )
        runtime = config.runtime_root / version
        _write(runtime / "bin/python", "fixture\n", 0o700)
        _write(runtime / "inventory/runtime.json", "{}\n", 0o600)
    verifier = _FixtureVerifier()
    inspector = _FixtureProcessInspector()
    builder = PreflightBuilder(
        config,
        verifier=verifier,
        inspector=inspector,  # type: ignore[arg-type]
        health=_FixtureHealthGate(),  # type: ignore[arg-type]
    )

    facts = builder.capture(config)

    assert verifier.calls == ["1.0.0-build", "0.11.0-build", "0.10.0-build"]
    assert inspector.calls == 2
    assert facts["target_environment"]["BUILD_ID"] == "1.0.0-build"
    assert facts["target_environment"]["ASSISTANT_SCHEDULE_DISABLE"] == "0"
    assert facts["target_environment"]["ALLOW_RUNTIME_SCHEMA_MUTATIONS"] == "0"
    assert "verifier_library" in facts["tools"]
    assert facts["database_password_file"] == database_password_file_record(
        config.database_password_file
    )
    assert facts["target_environment"]["DB_USER"] == "web_runtime"
    assert facts["target_environment"]["GLOBEMIND_DB_PASSWORD_FILE"] == str(
        config.database_password_file
    )
    builder.assert_bound_inputs(config, facts)

    config.database_password_file.write_text("rotated-password\n", encoding="utf-8")
    config.database_password_file.chmod(0o600)
    with pytest.raises(PromotionError, match="changed before a controller action"):
        builder.assert_bound_inputs(config, facts)
    with pytest.raises(PromotionError, match="differs from the interrupted transaction"):
        builder.validate_recovery(config, facts)


def test_promotion_source_contains_no_direct_or_broad_signal_api() -> None:
    source = (DEPLOY_DIR / "web_promotion.py").read_text(encoding="utf-8")

    assert "os.kill" not in source
    assert "pkill" not in source
    assert "killall" not in source
