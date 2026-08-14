from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
ENGINE_MODULES = (
    "api.core.db",
    "api.routes.story_graph",
    "api.routes.opinion_v2",
    "api.services.news_search_v2",
    "api.services.financial_terminal",
)


def test_all_web_database_exports_share_one_engine_and_session_factory() -> None:
    from api.core import db
    from api.routes import opinion_v2, story_graph
    from api.services import financial_terminal, news_search_v2

    engines = (
        db.engine,
        story_graph._L1_ENGINE,
        news_search_v2.NEWS_ENGINE,
        financial_terminal._L1_ENGINE,
        financial_terminal._get_l1_engine(),
    )

    assert len({id(value) for value in engines}) == 1
    assert story_graph._L1_SESSION_LOCAL is db.SessionLocal
    assert opinion_v2._NEWS_SESSION_LOCAL is db.SessionLocal
    assert story_graph.get_l1_db is db.get_db
    assert news_search_v2.NEWS_DATABASE_URL is db.SQLALCHEMY_DATABASE_URL
    assert story_graph._make_l1_database_url() is db.SQLALCHEMY_DATABASE_URL
    assert opinion_v2._make_news_database_url() is db.SQLALCHEMY_DATABASE_URL
    assert financial_terminal._make_l1_database_url() is db.SQLALCHEMY_DATABASE_URL
    assert db.SessionLocal.kw["bind"] is db.engine


def test_only_core_database_module_constructs_an_engine_or_session_factory() -> None:
    module_paths = tuple(
        BACKEND_ROOT / (module.replace(".", "/") + ".py")
        for module in ENGINE_MODULES
    )
    create_engine_sites = []
    sessionmaker_sites = []
    for path in module_paths:
        source = path.read_text(encoding="utf-8")
        if "create_engine(" in source:
            create_engine_sites.append(path)
        if "sessionmaker(" in source:
            sessionmaker_sites.append(path)

    expected = [PROJECT_ROOT / "backend/api/core/db.py"]
    assert create_engine_sites == expected
    assert sessionmaker_sites == expected


@pytest.mark.parametrize(
    "module_order",
    [
        ENGINE_MODULES,
        tuple(reversed(ENGINE_MODULES)),
        (
            "api.services.news_search_v2",
            "api.routes.opinion_v2",
            "api.services.financial_terminal",
            "api.routes.story_graph",
            "api.core.db",
        ),
    ],
)
def test_fresh_process_import_order_constructs_exactly_one_engine(
    module_order: tuple[str, ...],
) -> None:
    script = f"""
import importlib
import sqlalchemy

real_create_engine = sqlalchemy.create_engine
calls = []

def counted_create_engine(*args, **kwargs):
    calls.append((args, kwargs))
    return real_create_engine(*args, **kwargs)

sqlalchemy.create_engine = counted_create_engine
for module_name in {module_order!r}:
    importlib.import_module(module_name)

from api.core import db
from api.routes import opinion_v2, story_graph
from api.services import financial_terminal, news_search_v2

assert len(calls) == 1, calls
assert story_graph._L1_ENGINE is db.engine
assert news_search_v2.NEWS_ENGINE is db.engine
assert financial_terminal._get_l1_engine() is db.engine
assert story_graph._L1_SESSION_LOCAL is db.SessionLocal
assert opinion_v2._NEWS_SESSION_LOCAL is db.SessionLocal
"""
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "PYTHONPATH": str(BACKEND_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "1",
            "DB_USER": "globemind_test",
            "DB_NAME": "globemind_test",
        }
    )

    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_four_worker_connection_budget_uses_one_pool_per_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.core import db
    from api.db_pool import engine_pool_kwargs
    from api.routes import opinion_v2, story_graph
    from api.services import financial_terminal, news_search_v2

    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "2")
    pool = engine_pool_kwargs()
    engines = {
        id(db.engine),
        id(story_graph._L1_ENGINE),
        id(news_search_v2.NEWS_ENGINE),
        id(financial_terminal._get_l1_engine()),
        id(opinion_v2._NEWS_SESSION_LOCAL.kw["bind"]),
    }
    workers = 4
    consolidated_budget = workers * len(engines) * (
        pool["pool_size"] + pool["max_overflow"]
    )
    previous_budget = workers * 5 * (
        pool["pool_size"] + pool["max_overflow"]
    )

    assert len(engines) == 1
    assert consolidated_budget == 20
    assert consolidated_budget <= 64
    assert previous_budget == 100


def test_fork_child_hook_replaces_inherited_pool_without_closing_parent_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.core import db

    calls: list[bool] = []
    inherited_engine = SimpleNamespace(dispose=lambda *, close: calls.append(close))
    monkeypatch.setattr(db, "engine", inherited_engine)

    db._dispose_inherited_engine_pool()

    assert calls == [False]
