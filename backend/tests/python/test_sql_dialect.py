import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SOURCES = (REPO / "backend/club_api", REPO / "scripts/club_ops")
POSTGRES_ONLY = re.compile(
    r"::(?:int|text|uuid|jsonb|date|numeric|bigint)\b"
    r"|ON CONFLICT|FOR UPDATE OF|FOR SHARE\b|DISTINCT ON"
    r"|\bpg_[a-z]|\bjsonb\b|\bto_jsonb\b|\bjson_build_object\b|\bgen_random_uuid\b"
    r"|interval '|\bstrpos\(|=\s*ANY\("
    r"|\bpsycopg\b|\bJsonb\b|\bQuery\("
)


def postgres_only_lines():
    for root in SOURCES:
        for path in sorted(root.rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if POSTGRES_ONLY.search(line):
                    yield f"{path.relative_to(REPO)}:{number}"


def test_server_code_uses_only_mariadb_sql():
    assert list(postgres_only_lines()) == []
