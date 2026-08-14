from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.services import file_store


def test_missing_catalog_read_is_zero_write_and_does_not_materialize_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "missing-data"
    monkeypatch.setattr(file_store, "_DATA_DIR", data_root)

    assert file_store.read_json("sites.json") == []
    assert file_store.read_json("members.json") == []
    assert not data_root.exists()


def test_catalog_read_rejects_parent_traversal_without_creating_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    outside = tmp_path / "outside.json"
    outside.write_text('[{"secret":"outside-canary"}]', encoding="utf-8")
    monkeypatch.setattr(file_store, "_DATA_DIR", data_root)

    assert file_store.read_json("../outside.json") == []
    assert not data_root.exists()


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_catalog_read_rejects_linked_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_kind: str,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text('[{"secret":"outside-canary"}]', encoding="utf-8")
    path = data_root / "sites.json"
    if link_kind == "symlink":
        path.symlink_to(victim)
    else:
        path.hardlink_to(victim)
    monkeypatch.setattr(file_store, "_DATA_DIR", data_root)

    assert file_store.read_json("sites.json") == []
    assert victim.read_text(encoding="utf-8") == '[{"secret":"outside-canary"}]'


@pytest.mark.parametrize(
    "source",
    (
        '[{"id":1,"id":2}]',
        '[{"value":NaN}]',
        '[{"value":1e400}]',
        "[" * 1100 + "0" + "]" * 1100,
        '[{"padding":"' + "x" * (1024 * 1024) + '"}]',
        '{"not":"a-list"}',
        '["not-an-object"]',
    ),
    ids=(
        "duplicate-key",
        "nan",
        "overflow",
        "deep",
        "oversized",
        "object-root",
        "non-object-item",
    ),
)
def test_catalog_read_rejects_ambiguous_or_unbounded_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    path = data_root / "sites.json"
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(file_store, "_DATA_DIR", data_root)

    assert file_store.read_json("sites.json") == []


def test_catalog_read_accepts_a_bounded_list_of_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    expected = [{"id": 1, "name": "verified fixture"}]
    (data_root / "sites.json").write_text(
        json.dumps(expected),
        encoding="utf-8",
    )
    monkeypatch.setattr(file_store, "_DATA_DIR", data_root)

    assert file_store.read_json("sites.json") == expected
