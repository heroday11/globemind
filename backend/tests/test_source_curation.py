from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

MODULE_PATH = REPO_ROOT / "scripts" / "curate_source_catalog.py"
SPEC = importlib.util.spec_from_file_location("curate_source_catalog", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

IMPORT_REVIEW_PATH = REPO_ROOT / "scripts" / "import_media_source_review_queue.py"
IMPORT_REVIEW_SPEC = importlib.util.spec_from_file_location(
    "import_media_source_review_queue", IMPORT_REVIEW_PATH
)
assert IMPORT_REVIEW_SPEC and IMPORT_REVIEW_SPEC.loader
IMPORT_REVIEW_MODULE = importlib.util.module_from_spec(IMPORT_REVIEW_SPEC)
sys.modules[IMPORT_REVIEW_SPEC.name] = IMPORT_REVIEW_MODULE
IMPORT_REVIEW_SPEC.loader.exec_module(IMPORT_REVIEW_MODULE)

classify = MODULE.classify
read_rows = MODULE.read_rows
write_curated = MODULE.write_curated
write_whitelist = MODULE.write_whitelist
build_review_update = IMPORT_REVIEW_MODULE.build_update


def test_classify_keeps_major_media():
    row = classify("bbc_com", "https://www.bbc.com/news")

    assert row.decision == "keep"
    assert row.tier == "A"
    assert row.source_type == "major_media"


def test_classify_drops_noise_domains():
    row = classify("dictionary_cambridge_org", "https://dictionary.cambridge.org/dictionary/")

    assert row.decision == "drop"
    assert row.tier == "D"


def test_classify_marks_think_tank_as_review():
    row = classify("brookings_edu", "https://www.brookings.edu/topics/international-affairs/")

    assert row.decision == "review"
    assert row.tier == "C"
    assert row.source_type == "think_tank"


def test_batch_read_and_write(tmp_path: Path):
    input_path = tmp_path / "raw.tsv"
    input_path.write_text(
        "site_id\turl\n"
        "bbc_com\thttps://www.bbc.com/news\n"
        "dropbox_com\thttps://www.dropbox.com/business/solutions/marketing\n",
        encoding="utf-8",
    )

    rows = [classify(site_id, url) for site_id, url in read_rows(input_path)]
    curated_path = tmp_path / "curated.csv"
    whitelist_path = tmp_path / "whitelist.csv"
    write_curated(curated_path, rows)
    write_whitelist(whitelist_path, rows)

    with curated_path.open("r", encoding="utf-8", newline="") as handle:
        curated_rows = list(csv.DictReader(handle))
    with whitelist_path.open("r", encoding="utf-8", newline="") as handle:
        whitelist_rows = list(csv.DictReader(handle))

    assert len(curated_rows) == 2
    assert len(whitelist_rows) == 1
    assert whitelist_rows[0]["site_id"] == "bbc_com"


def test_review_import_ownership_only_does_not_default_confidence():
    update, errors = build_review_update(
        {
            "domain": "example.com",
            "proposed_ownership_type": "private",
            "evidence_url_2": "https://example.com/about",
            "review_note": "ownership reviewed",
        },
        2,
    )

    assert errors == []
    assert update["ownership_type"] == "private"
    assert update["review_status"] == "reviewed"
    assert "label_confidence" not in update


def test_review_import_political_update_defaults_confidence():
    update, errors = build_review_update(
        {
            "domain": "example.com",
            "proposed_political_leaning": "center",
            "evidence_url_1": "https://example.com/rating",
        },
        2,
    )

    assert errors == []
    assert update["political_leaning"] == "center"
    assert update["label_confidence"] == "medium"
