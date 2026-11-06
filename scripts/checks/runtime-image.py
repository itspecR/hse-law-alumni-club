import importlib.util
import pathlib
import shutil
import sys


def check(mode):
    root = pathlib.Path("/opt/venv/lib/python3.14/site-packages")
    packages = {"api": ["club_api"], "bootstrap": ["club_api", "club_ops"], "web": ["club_web"]}[mode]
    web_types = {
        ".pyc",
        ".json",
        ".html",
        ".css",
        ".js",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".avif",
        ".ico",
        ".woff",
        ".woff2",
        ".webmanifest",
        ".pdf",
        ".txt",
        ".mp3",
    }
    for name in packages:
        package = root / name
        assert (package / "__init__.pyc").is_file(), name
        for path in package.rglob("*"):
            if not path.is_file():
                continue
            assert path.suffix in (web_types if mode == "web" else {".pyc", ".json"}), str(path)
            assert not any(
                part in ("tests", "__pycache__", ".venv", "browser", "styles", "node_modules") for part in path.parts
            ), str(path)
            assert not path.name.startswith((".env", "._")), str(path)
    if mode == "api":
        assert not (root / "club_ops").exists()
        assert not list((root / "club_api").rglob("*catalog*.json"))
        assert not list((root / "club_api").rglob("*demo*.json"))
    if mode == "web":
        for file in ("build.pyc", "sync_changes.pyc"):
            assert not (root / "club_web" / file).exists(), file
        assert (root / "club_web/templates/base.html").is_file()
        assert (root / "club_web/public/assets/site.js").is_file()
    for module in ("pytest", "ruff", "pip_audit", "playwright", "tree_sitter"):
        assert importlib.util.find_spec(module) is None, module
    for binary in ("node", "npm", "pnpm", "uv", "pip", "pip3"):
        assert shutil.which(binary) is None, binary
    assert not list(pathlib.Path("/app").rglob("*"))
    print(f"{mode}: модули скомпилированы; исходников Python, тестов и инструментов сборки нет")


if __name__ == "__main__":
    check(sys.argv[1])
