from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from api.core.environment import (  # noqa: E402
    app_version,
    bool_setting,
    default_env_paths,
    float_setting,
    int_setting,
    load_environment,
    source_version,
)


def test_load_environment_keeps_first_file_precedence(tmp_path: Path, monkeypatch):
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text("ORDER_TEST=first\n", encoding="utf-8")
    second.write_text("ORDER_TEST=second\n", encoding="utf-8")
    monkeypatch.delenv("ORDER_TEST", raising=False)

    loaded = load_environment((first, second))

    assert loaded == (first.resolve(), second.resolve())
    assert os.environ["ORDER_TEST"] == "first"


def test_pytest_defaults_to_isolated_test_environment():
    assert os.environ["APP_ENV"] == "test"
    assert default_env_paths() == ()


def test_test_environment_file_is_exclusive(tmp_path: Path, monkeypatch):
    test_env = tmp_path / "test.env"
    production_env = tmp_path / "production.env"
    test_env.write_text("TEST_ENV_MARKER=isolated\n", encoding="utf-8")
    production_env.write_text("PRODUCTION_ENV_MARKER=must-not-load\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GLOBEMIND_TEST_ENV_FILE", str(test_env))
    monkeypatch.setenv("GLOBEMIND_ENV_FILE", str(production_env))
    monkeypatch.setenv("GLOBEMIND_ENV_FILES", os.pathsep.join((str(production_env), str(tmp_path / "other.env"))))
    monkeypatch.delenv("TEST_ENV_MARKER", raising=False)
    monkeypatch.delenv("PRODUCTION_ENV_MARKER", raising=False)

    loaded = load_environment()

    assert loaded == (test_env.resolve(),)
    assert os.environ["TEST_ENV_MARKER"] == "isolated"
    assert "PRODUCTION_ENV_MARKER" not in os.environ


def test_production_environment_files_are_explicit_and_ordered(tmp_path: Path, monkeypatch):
    primary = tmp_path / "primary.env"
    integration = tmp_path / "integration.env"
    primary.write_text("ORDERED_SETTING=primary\nPRIMARY_ONLY=yes\n", encoding="utf-8")
    integration.write_text("ORDERED_SETTING=integration\nINTEGRATION_ONLY=yes\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GLOBEMIND_ENV_FILES", os.pathsep.join((str(primary), str(integration))))
    monkeypatch.delenv("ORDERED_SETTING", raising=False)
    monkeypatch.delenv("PRIMARY_ONLY", raising=False)
    monkeypatch.delenv("INTEGRATION_ONLY", raising=False)

    loaded = load_environment()

    assert loaded == (primary.resolve(), integration.resolve())
    assert os.environ["ORDERED_SETTING"] == "primary"
    assert os.environ["PRIMARY_ONLY"] == "yes"
    assert os.environ["INTEGRATION_ONLY"] == "yes"


def test_integer_settings_fail_safe_and_apply_minimum(monkeypatch):
    monkeypatch.setenv("TEST_INTEGER_SETTING", "invalid")
    assert int_setting("TEST_INTEGER_SETTING", 12, minimum=10) == 12

    monkeypatch.setenv("TEST_INTEGER_SETTING", "3")
    assert int_setting("TEST_INTEGER_SETTING", 12, minimum=10) == 10


def test_float_settings_fail_safe_and_apply_minimum(monkeypatch):
    monkeypatch.delenv("TEST_FLOAT_SETTING", raising=False)
    assert float_setting("TEST_FLOAT_SETTING", 1.5, minimum=0.5) == 1.5

    monkeypatch.setenv("TEST_FLOAT_SETTING", "invalid")
    assert float_setting("TEST_FLOAT_SETTING", 1.5, minimum=0.5) == 1.5

    monkeypatch.setenv("TEST_FLOAT_SETTING", "0.25")
    assert float_setting("TEST_FLOAT_SETTING", 1.5, minimum=0.5) == 0.5


def test_boolean_settings_are_strict_and_fail_safe(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("TEST_BOOLEAN_SETTING", value)
        assert bool_setting("TEST_BOOLEAN_SETTING") is True

    for value in ("0", "false", "NO", "off"):
        monkeypatch.setenv("TEST_BOOLEAN_SETTING", value)
        assert bool_setting("TEST_BOOLEAN_SETTING", True) is False

    monkeypatch.setenv("TEST_BOOLEAN_SETTING", "invalid")
    assert bool_setting("TEST_BOOLEAN_SETTING", True) is True
    monkeypatch.delenv("TEST_BOOLEAN_SETTING")
    assert bool_setting("TEST_BOOLEAN_SETTING") is False


def test_app_version_uses_version_file_and_explicit_override(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert source_version() == (PROJECT_ROOT / "VERSION").read_text(encoding="ascii").strip()
    assert app_version() == source_version()

    monkeypatch.setenv("APP_VERSION", "9.8.7-candidate")
    assert app_version() == "9.8.7-candidate"


def test_api_package_uses_only_explicit_test_environment(tmp_path: Path):
    test_env = tmp_path / "subprocess-test.env"
    production_env = tmp_path / "subprocess-production.env"
    test_env.write_text("SUBPROCESS_TEST_MARKER=isolated\n", encoding="utf-8")
    production_env.write_text("SUBPROCESS_PRODUCTION_MARKER=must-not-load\n", encoding="utf-8")
    env = os.environ.copy()
    env["APP_ENV"] = "test"
    env["GLOBEMIND_TEST_ENV_FILE"] = str(test_env)
    env["GLOBEMIND_ENV_FILE"] = str(production_env)
    env.pop("SUBPROCESS_TEST_MARKER", None)
    env.pop("SUBPROCESS_PRODUCTION_MARKER", None)
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    script = (
        "import os; import api; "
        "assert os.environ['APP_ENV'] == 'test'; "
        "assert os.environ['SUBPROCESS_TEST_MARKER'] == 'isolated'; "
        "assert 'SUBPROCESS_PRODUCTION_MARKER' not in os.environ"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
