from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from api.static_cache import (  # noqa: E402
    dataset_static_headers,
    resolve_path_under,
    spa_indexing_headers,
)
from backend import serve_prod  # noqa: E402


def test_spa_fallback_roots_match_production_router_contract():
    router_source = (
        PROJECT_ROOT / "frontend/vue_project/src/router/index.js"
    ).read_text(encoding="utf-8")
    development_routes = re.search(
        r"const DEVELOPMENT_ONLY_ROUTES\s*=\s*\[(.*?)\]\s*\n\s*const router",
        router_source,
        flags=re.DOTALL,
    )
    assert development_routes is not None
    development_paths = set(
        re.findall(r"\bpath:\s*['\"](/[^'\"]*)", development_routes.group(1))
    )
    absolute_paths = re.findall(r"\bpath:\s*['\"](/[^'\"]*)", router_source)
    router_roots = {
        path.lstrip("/").split("/", 1)[0]
        for path in absolute_paths
        if path != "/"
        and path not in development_paths
        and not path.startswith("/:pathMatch(")
    }

    assert router_roots == serve_prod._SPA_FALLBACK_ROOTS
    assert {"/showcase", "/showcase/delta-force", "/story-graph-handle-debug"} == development_paths
    assert "showcase" not in serve_prod._SPA_FALLBACK_ROOTS


def test_resolve_path_under_accepts_files_inside_root(tmp_path: Path):
    asset = tmp_path / "assets" / "app.js"
    asset.parent.mkdir()
    asset.write_text("ok", encoding="utf-8")

    assert resolve_path_under(tmp_path, "/assets/app.js") == asset


@pytest.mark.parametrize("path", ["/../outside.txt", "/assets/../../../outside.txt", "bad\x00name"])
def test_resolve_path_under_rejects_unsafe_paths(tmp_path: Path, path: str):
    with pytest.raises(ValueError):
        resolve_path_under(tmp_path, path)


@pytest.mark.parametrize(
    "path",
    [
        "/..%2F..%2F..%2F..%2Fetc%2Fpasswd",
        "/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
    ],
)
def test_production_wrapper_does_not_serve_files_outside_dist(path: str):
    with TestClient(serve_prod.app) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert not response.text.startswith("root:")


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("viewer.html", '<script src="https://attacker.invalid/x.js"></script>'),
        ("plugin.js", "globalThis.compromised = true"),
        ("theme.css", "body { background: url(https://attacker.invalid/leak) }"),
        ("module.wasm", "not-really-wasm"),
    ],
)
def test_dataset_active_content_is_downloaded_as_plain_text(
    monkeypatch, tmp_path: Path, filename: str, content: str
):
    (tmp_path / "index.html").write_text("<main>app</main>", encoding="utf-8")
    viewer = tmp_path / "datasets" / "third-party" / filename
    viewer.parent.mkdir(parents=True)
    viewer.write_text(content, encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        response = client.get(f"/datasets/third-party/{filename}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_dataset_pdf_is_download_only_without_corrupting_media_type(monkeypatch, tmp_path: Path):
    (tmp_path / "index.html").write_text("<main>app</main>", encoding="utf-8")
    document = tmp_path / "datasets" / "third-party" / "reference.pdf"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        response = client.get("/datasets/third-party/reference.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == "attachment"


def test_dataset_catalog_remains_fetchable_json(monkeypatch, tmp_path: Path):
    (tmp_path / "index.html").write_text("<main>app</main>", encoding="utf-8")
    catalog = tmp_path / "datasets" / "expert-skills" / "catalog.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text('{"skills": []}', encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        response = client.get("/datasets/expert-skills/catalog.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "content-disposition" not in response.headers
    assert response.json() == {"skills": []}


def test_dataset_svg_receives_non_executable_headers():
    headers = dataset_static_headers("/datasets/template.svg")

    assert headers["Content-Disposition"] == "attachment"
    assert headers["Content-Security-Policy"] == "default-src 'none'; sandbox"


def test_generated_images_are_served_outside_release(monkeypatch, tmp_path: Path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>app</main>", encoding="utf-8")
    generated_root = tmp_path / "generated"
    image = generated_root / "imgs" / "hermes-generated" / "sample.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    monkeypatch.setenv("FRONTEND_DIST", str(frontend))
    monkeypatch.setattr(serve_prod, "GENERATED_ASSET_ROOT", generated_root)

    with TestClient(serve_prod.make_app()) as client:
        response = client.get("/imgs/hermes-generated/sample.png")

    assert response.status_code == 200
    assert response.content == b"png"


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/.git/config",
        "/backend/.env",
        "/production/.env",
        "/key.pem",
        "/secrets.json",
        "/docker-compose.yaml",
        "/terraform.tfstate",
    ],
)
def test_frontend_wrapper_never_serves_sensitive_public_paths(
    monkeypatch, tmp_path: Path, path: str
):
    (tmp_path / "index.html").write_text("<main>app-index</main>", encoding="utf-8")
    exposed = tmp_path / path.lstrip("/")
    exposed.parent.mkdir(parents=True, exist_ok=True)
    exposed.write_text("sensitive-fixture", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert "sensitive-fixture" not in response.text
    assert "app-index" not in response.text
    assert response.headers["cache-control"].startswith("no-cache, no-store")


def test_security_txt_is_the_only_reviewed_public_dot_path(monkeypatch, tmp_path: Path):
    (tmp_path / "index.html").write_text("<main>app-index</main>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        security = client.get("/.well-known/security.txt")
        hidden = client.get("/.well-known/.env")

    assert security.status_code == 200
    assert security.headers["content-type"].startswith("text/plain")
    assert security.headers["x-content-type-options"] == "nosniff"
    assert "Contact: mailto:contact@globemind.top" in security.text
    assert "Policy: https://globemind.top/security" in security.text
    assert "Expires: 2027-02-09T00:00:00Z" in security.text
    assert "SLA" not in security.text
    assert hidden.status_code == 404
    assert "app-index" not in hidden.text


@pytest.mark.parametrize("path", ["/graphql", "/console", "/wp-login.php", "/server-status"])
def test_unknown_scanner_paths_do_not_fall_through_to_spa(
    monkeypatch, tmp_path: Path, path: str
):
    (tmp_path / "index.html").write_text("<main>app-index</main>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert "app-index" not in response.text


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_frontend_routes_reject_non_read_methods(monkeypatch, tmp_path: Path, method: str):
    (tmp_path / "index.html").write_text("<main>app-index</main>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        response = client.request(method, "/login")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
    assert "app-index" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/login",
        "/privacy",
        "/terms",
        "/security",
        "/corrections",
        "/methodology",
        "/sources",
        "/status",
        "/data-assistant",
        "/entity-governance",
        "/model-assurance",
        "/research-workspace",
        "/data-service/help-docs",
        "/user-center/personal-center",
        "/data-service/ground-news-source/reuters.com",
    ],
)
def test_declared_frontend_routes_still_use_spa_fallback(
    monkeypatch, tmp_path: Path, path: str
):
    (tmp_path / "index.html").write_text("<main>app-index</main>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert "app-index" in response.text


def test_spa_indexing_headers_are_canonical_only_for_reviewed_public_routes(
    monkeypatch, tmp_path: Path
):
    (tmp_path / "index.html").write_text("<main>app-index</main>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        public = client.get("/sources?ignored=query")
        private = client.get("/research-workspace")
        entity_governance = client.get("/entity-governance")

    assert public.headers["x-robots-tag"] == "index, follow"
    assert public.headers["link"] == (
        '<https://globemind.top/sources>; rel="canonical"'
    )
    assert private.headers["x-robots-tag"] == "noindex, nofollow"
    assert "link" not in private.headers
    assert entity_governance.headers["x-robots-tag"] == "noindex, nofollow"
    assert "link" not in entity_governance.headers
    assert spa_indexing_headers("//sources//") == {
        "X-Robots-Tag": "noindex, nofollow"
    }


@pytest.mark.parametrize(
    "path",
    ["/showcase", "/showcase/delta-force", "/story-graph-handle-debug"],
)
def test_development_only_frontend_routes_are_not_exposed_by_production_fallback(
    monkeypatch, tmp_path: Path, path: str
):
    (tmp_path / "index.html").write_text("<main>app-index</main>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))

    with TestClient(serve_prod.make_app()) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert "app-index" not in response.text
