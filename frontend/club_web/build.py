import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from club_web.offline import PWA_PARAMS

ROOT = Path(__file__).parent
THEME_LAYER = "lux.css"


def responsive_css(css):
    additions = []
    for match in re.finditer(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{", css):
        depth, end = 1, match.end()
        while depth and end < len(css):
            depth += (css[end] == "{") - (css[end] == "}")
            end += 1
        if not depth:
            additions.append(f"@container club-layout (max-width:{match[1]}px){{" + css[match.end() : end])
    return css + "\n" + "\n".join(additions)


def build_public(destination, *, mirror=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source = ROOT.parent / "public"
    if not source.exists():
        source = Path("/repo/frontend/public")
    if source != destination:
        shutil.copytree(source, destination, dirs_exist_ok=True)
    assets = destination / "assets"
    assets.mkdir(exist_ok=True)
    if not mirror:
        (assets / "mirror.js").unlink(missing_ok=True)
    styles = ROOT / "styles"
    files = [
        styles / "tokens.css",
        styles / "index.css",
        *sorted(
            path
            for path in styles.glob("*.css")
            if path.name not in ("tokens.css", "index.css", THEME_LAYER) and not path.name.startswith(".")
        ),
        styles / THEME_LAYER,
    ]
    (assets / "site.css").write_text(
        "\n".join(responsive_css(path.read_text(encoding="utf-8")) for path in files), encoding="utf-8"
    )
    for browser_file in (ROOT / "browser").glob("*.js"):
        if (
            browser_file.name.startswith(".")
            or browser_file.name == "service-worker.js"
            or browser_file.name == "mirror.js"
            and not mirror
        ):
            continue
        shutil.copyfile(browser_file, assets / browser_file.name)
    worker = (ROOT / "browser/service-worker.js").read_text(encoding="utf-8")
    files = ["./assets/" + path.name for path in sorted(assets.glob("*.js"))]
    files += [
        "./assets/site.css",
        *["./fonts/" + path.name for path in sorted((destination / "fonts").glob("*.woff2"))],
    ]
    worker = worker.replace("const BUILT_ASSETS = [];", "const BUILT_ASSETS = " + json.dumps(files) + ";")
    worker = worker.replace("const PWA_PARAMS = {};", "const PWA_PARAMS = " + json.dumps(PWA_PARAMS) + ";")
    digest = hashlib.sha256(worker.encode())
    static_files = [path for path in destination.iterdir() if path.is_file() and path.name != "sw.js"]
    for directory in (assets, destination / "fonts"):
        static_files.extend(path for path in directory.rglob("*") if path.is_file())
    for path in sorted(static_files, key=lambda path: path.relative_to(destination).as_posix()):
        content = path.read_bytes()
        digest.update(path.relative_to(destination).as_posix().encode() + b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    worker = re.sub(
        r"const CACHE = `\$\{CACHE_PREFIX\}[^`]*`;",
        "const CACHE = `${CACHE_PREFIX}" + digest.hexdigest()[:16] + "`;",
        worker,
    )
    (destination / "sw.js").write_text(worker, encoding="utf-8")


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT.parent / "public"))
    args = parser.parse_args()
    build_public(args.output)


if __name__ == "__main__":
    run()
