import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

import tree_sitter_javascript
from tree_sitter import Language, Parser

ROOT = Path(__file__).resolve().parents[2]
PARSER = Parser(Language(tree_sitter_javascript.language()))


def javascript_failures(code):
    tree = PARSER.parse(code.encode())
    failures = ["Некорректный JavaScript"] if tree.root_node.has_error else []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        fragment = code.encode()[node.start_byte : node.end_byte].decode()
        if (
            node.type == "comment"
            and not fragment.startswith("#!")
            and not re.search(r"@license|@preserve|Copyright", fragment)
        ):
            failures.append(f"Комментарий: строка {node.start_point.row + 1}")
        stack.extend(node.children)
    return failures


def python_failures(code):
    failures = [
        f"Комментарий: строка {token.start[0]}"
        for token in tokenize.generate_tokens(io.StringIO(code).readline)
        if token.type == tokenize.COMMENT and not (token.start[0] == 1 and token.string.startswith("#!"))
    ]
    tree = ast.parse(code)
    failures.extend(
        f"Docstring: строка {node.lineno if hasattr(node, 'lineno') else 1}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and ast.get_docstring(node)
    )
    return failures


def main():
    paths = (
        subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    checked, failures = 0, []
    for name in paths:
        path = ROOT / name
        if not path.is_file() or path.suffix not in (".py", ".js", ".mjs", ".css", ".html", ".sh", ".sql"):
            continue
        code = path.read_text(encoding="utf-8")
        if path.suffix in (".sh", ".sql"):
            marker = r"^\s*#[^!]" if path.suffix == ".sh" else r"^\s*--|/\*"
            found = ["Комментарий"] if re.search(marker, code, re.MULTILINE) else []
        else:
            found = (
                python_failures(code)
                if path.suffix == ".py"
                else javascript_failures(code)
                if path.suffix in (".js", ".mjs")
                else ["Комментарий"]
                if "/*" in code or "<!--" in code
                else []
            )
        failures.extend(f"{name}: {failure}" for failure in found)
        checked += 1
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Проверено {checked} файлов: комментариев и docstring в собственном коде нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
