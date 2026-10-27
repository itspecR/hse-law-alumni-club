import re
from pathlib import Path

import pytest

from club_web import build


@pytest.fixture
def build_source(tmp_path, monkeypatch):
    root = tmp_path / "frontend" / "club_web"
    public = root.parent / "public"
    (root / "styles").mkdir(parents=True)
    (root / "browser").mkdir()
    (root / "styles" / "tokens.css").write_text(":root { --color: blue; }")
    (root / "styles" / "index.css").write_text("body { color: var(--color); }")
    (root / "styles" / "alpha.css").write_text(".alpha { color: red; }")
    (root / "styles" / build.THEME_LAYER).write_text("html.theme { --color: black; }")
    worker = (Path(build.ROOT) / "browser" / "service-worker.js").read_text()
    (root / "browser" / "service-worker.js").write_text(worker)
    (root / "browser" / "site.js").write_text("export const version = 1;")
    for name in (
        "assets/photos/nested/image.webp",
        "fonts/font.woff2",
        "fonts/fonts.css",
        "offline.html",
        "manifest.webmanifest",
        "icon-192.png",
    ):
        path = public / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original")
    monkeypatch.setattr(build, "ROOT", root)
    return public


@pytest.mark.parametrize(
    "asset",
    [
        "assets/photos/nested/image.webp",
        "fonts/font.woff2",
        "fonts/fonts.css",
        "offline.html",
        "manifest.webmanifest",
        "icon-192.png",
    ],
)
def test_static_content_change_updates_worker_version(build_source, tmp_path, asset):
    destination = tmp_path / "output"
    build.build_public(destination)
    original_worker = (destination / "sw.js").read_bytes()
    original_version = re.search(rb"const CACHE = `\$\{CACHE_PREFIX\}([a-f0-9]+)`;", original_worker)[1]
    build.build_public(destination)
    assert (destination / "sw.js").read_bytes() == original_worker

    (build_source / asset).write_bytes(b"replacement at the same URL")
    build.build_public(destination)
    changed_worker = (destination / "sw.js").read_bytes()
    changed_version = re.search(rb"const CACHE = `\$\{CACHE_PREFIX\}([a-f0-9]+)`;", changed_worker)[1]
    assert changed_version != original_version
    build.build_public(destination)
    assert (destination / "sw.js").read_bytes() == changed_worker

    other_destination = tmp_path / "other-output"
    build.build_public(other_destination)
    assert (other_destination / "sw.js").read_bytes() == changed_worker


def test_theme_layer_is_built_last(build_source, tmp_path):
    destination = tmp_path / "output"
    build.build_public(destination)
    css = (destination / "assets" / "site.css").read_text()
    assert css.index(":root") < css.index("body {") < css.index(".alpha") < css.index("html.theme")


def test_modules_and_worker_use_content_version(build_source, tmp_path):
    browser = Path(build.ROOT) / "browser"
    (browser / "home.js").write_text("export const ready = true;")
    (browser / "site.js").write_text('import { ready } from "./home.js";\nexport const version = ready;')
    destination = tmp_path / "output"
    build.build_public(destination)
    version = (destination / "assets" / "version.txt").read_text()
    assert re.fullmatch(r"[0-9a-f]{12}", version)
    assert f'from "./home.js?v={version}"' in (destination / "assets" / "site.js").read_text()
    worker = (destination / "sw.js").read_text()
    assert f'"./assets/site.css?v={version}"' in worker and f'"./assets/site.js?v={version}"' in worker
    build.build_public(destination)
    assert (destination / "assets" / "version.txt").read_text() == version
    assert (destination / "assets" / "site.js").read_text().count("?v=") == 1
    (browser / "home.js").write_text("export const ready = false;")
    build.build_public(destination)
    assert (destination / "assets" / "version.txt").read_text() != version
