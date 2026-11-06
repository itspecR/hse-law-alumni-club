import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def blocking_findings(report):
    if not isinstance(report.get("Results"), list) or not report.get("ArtifactName"):
        raise ValueError("Отчёт Trivy не содержит результатов или имени образа")
    findings = []
    for result in report["Results"]:
        for item in result.get("Vulnerabilities") or []:
            severity = item.get("Severity")
            if severity not in {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                raise ValueError("Не удалось определить критичность находки")
            if severity in {"HIGH", "CRITICAL", "UNKNOWN"}:
                findings.append({"id": item["VulnerabilityID"], "package": item["PkgName"], "severity": severity})
    return findings


def redacted_report(report):
    metadata = report.get("Metadata", {})
    return {
        "SchemaVersion": report.get("SchemaVersion"),
        "ArtifactName": report.get("ArtifactName"),
        "ArtifactType": report.get("ArtifactType"),
        "CollectedAt": datetime.now(UTC).isoformat(),
        "Metadata": {key: metadata[key] for key in ("OS", "ImageID", "RepoTags", "RepoDigests") if key in metadata},
        "Results": [
            {key: item[key] for key in ("Target", "Class", "Type", "Packages", "Vulnerabilities") if key in item}
            for item in report.get("Results", [])
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Сохранение отчёта образа без конфигурации и проверка находок")
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text())
        sanitized = redacted_report(report)
        findings = blocking_findings(report)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(sanitized, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        print("Отчёт образа отсутствует или некорректен", file=sys.stderr)
        return 1
    for finding in findings:
        print(f"{finding['severity']}: {finding['id']} ({finding['package']})")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
