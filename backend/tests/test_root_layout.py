from scripts.ci.check_root_layout import ALLOWED_ROOT_FILES, unexpected_root_files


def test_root_layout_allows_only_the_reviewed_public_contract() -> None:
    paths = [*ALLOWED_ROOT_FILES, "backend/api/main.py", "docs/README.md"]

    assert unexpected_root_files(paths) == []


def test_root_layout_rejects_new_root_scripts_and_reports_them_stably() -> None:
    paths = ["README.md", "db_explore6.py", "scratch.sh", "scripts/worker.py"]

    assert unexpected_root_files(paths) == ["db_explore6.py", "scratch.sh"]
