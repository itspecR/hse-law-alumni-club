import ast
import json
import re
import sys
from pathlib import Path

import tree_sitter_javascript
from tree_sitter import Language, Parser

ROOT = Path(__file__).resolve().parents[2]
PARSER = Parser(Language(tree_sitter_javascript.language()))
STYLE = re.compile("[ёЁ–—]")
PYTHON_ROOTS = ("backend/club_api", "frontend/club_web")
OPERATOR_FILES = {"backend/club_api/core/config.py"}
SKIP_FUNCTIONS = {"support_config", "slugify"}
LOGGER_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
SCRIPTS = "frontend/club_web/browser"
TEMPLATES = "frontend/club_web/templates"
LEGAL_TEMPLATES = "legal"
DATA_FILES = ("data/domain-data.json", "data/faq-data.json")
SEARCH_KEYS = {"triggers"}


def skipped_call(node):
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    regex = isinstance(func.value, ast.Name) and func.value.id == "re"
    return regex or func.attr == "replace" or func.attr in LOGGER_METHODS


def python_strings(tree):
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in SKIP_FUNCTIONS:
            continue
        if isinstance(node, ast.Call) and skipped_call(node):
            stack.append(node.func)
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        stack.extend(ast.iter_child_nodes(node))


def python_failures(code):
    return [(line, text) for line, text in python_strings(ast.parse(code)) if STYLE.search(text)]


def javascript_failures(code):
    source = code.encode()
    stack, failures = [PARSER.parse(source).root_node], []
    while stack:
        node = stack.pop()
        text = source[node.start_byte : node.end_byte].decode()
        if node.type in ("string_fragment", "template_string") and STYLE.search(text):
            failures.append((node.start_point.row + 1, text))
        stack.extend(node.children)
    return failures


def template_failures(code):
    return [(number, line.strip()) for number, line in enumerate(code.splitlines(), 1) if STYLE.search(line)]


def data_failures(value, path="$"):
    if isinstance(value, dict):
        return [
            failure
            for key, item in value.items()
            if key not in SEARCH_KEYS
            for failure in data_failures(item, f"{path}.{key}")
        ]
    if isinstance(value, list):
        return [failure for index, item in enumerate(value) for failure in data_failures(item, f"{path}[{index}]")]
    return [(path, value)] if isinstance(value, str) and STYLE.search(value) else []


def sources():
    for root in PYTHON_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if path.relative_to(ROOT).as_posix() not in OPERATOR_FILES:
                yield path, python_failures
    for path in sorted((ROOT / SCRIPTS).glob("*.js")):
        yield path, javascript_failures
    for path in sorted((ROOT / TEMPLATES).rglob("*.html")):
        if LEGAL_TEMPLATES not in path.relative_to(ROOT / TEMPLATES).parts:
            yield path, template_failures
    for name in DATA_FILES:
        yield ROOT / name, lambda code: data_failures(json.loads(code))


def main():
    checked, failures = 0, []
    for path, check in sources():
        name = path.relative_to(ROOT).as_posix()
        failures.extend(f"{name}:{place}: {text[:80]}" for place, text in check(path.read_text(encoding="utf-8")))
        checked += 1
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Проверено {checked} файлов: в текстах сайта нет «ё» и длинных тире")
    return 0


if __name__ == "__main__":
    sys.exit(main())
