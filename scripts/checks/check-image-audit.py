import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def blocking_findings(report, non_applicable=()):
    if not isinstance(report.get("Results"), list) or not report.get("ArtifactName"):
        raise ValueError("Отчёт Trivy не содержит результатов или имени образа")
    findings = []
    for result in report["Results"]:
        for item in result.get("Vulnerabilities") or []:
            severity = item.get("Severity")
            if severity not in {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                raise ValueError("Не удалось определить критичность находки")
            if severity in {"HIGH", "CRITICAL", "UNKNOWN"}:
                identity = (
                    result.get("Target"),
                    item["VulnerabilityID"],
                    item["PkgName"],
                    item.get("InstalledVersion"),
                )
                if identity not in non_applicable:
                    findings.append({"id": item["VulnerabilityID"], "package": item["PkgName"], "severity": severity})
    return findings


OPENPGP = ("usr/bin/caddy", "GO-2026-5932", "golang.org/x/crypto", "v0.58.0")
GO_JOSE_V3 = ("usr/bin/caddy", "CVE-2026-116587", "github.com/go-jose/go-jose/v3", "v3.0.5")
MAX_EVIDENCE_BYTES = 1024 * 1024
JWK_FROM_CLIENTS = re.compile(r"\bacme_server\b")


def reported(report, candidate):
    return any(
        (result.get("Target"), item.get("VulnerabilityID"), item.get("PkgName"), item.get("InstalledVersion"))
        == candidate
        for result in report.get("Results", [])
        for item in result.get("Vulnerabilities") or []
    )


def docker(*args, timeout):
    return subprocess.run(
        [shutil.which("docker") or "docker", *args], check=True, capture_output=True, text=True, timeout=timeout
    ).stdout


def verified_image(report, image):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", image) or report.get("ArtifactName") != image:
        raise ValueError("Имя проверяемого образа не совпало с отчётом")
    inspected = docker("image", "inspect", "--format", "{{.Id}}", image, timeout=15).strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", inspected) or inspected != report.get("Metadata", {}).get("ImageID"):
        raise ValueError("Digest образа не совпал с отчётом")
    return inspected


def image_file(inspected, path):
    content = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--entrypoint",
        "cat",
        inspected,
        path,
        timeout=30,
    )
    if not content or len(content) > MAX_EVIDENCE_BYTES:
        raise ValueError("Файл доказательства в образе пуст или слишком велик")
    return content


def evidence_for(candidate, inspected, reason, source, **proof):
    return {
        "VulnerabilityID": candidate[1],
        "PkgName": candidate[2],
        "InstalledVersion": candidate[3],
        "Target": candidate[0],
        "ImageID": inspected,
        "Status": "not_affected",
        "Reason": reason,
        "Source": source,
        **proof,
    }


def openpgp_evidence(inspected):
    dependencies = image_file(inspected, "/usr/share/caddy/dependencies.txt")
    packages = dependencies.splitlines()
    if (
        "github.com/caddyserver/caddy/v2/cmd" not in packages
        or any(not package or any(character.isspace() for character in package) for package in packages)
        or any(
            package == "golang.org/x/crypto/openpgp" or package.startswith("golang.org/x/crypto/openpgp/")
            for package in packages
        )
    ):
        raise ValueError("Не подтверждено отсутствие OpenPGP в сборке Caddy")
    return evidence_for(
        OPENPGP,
        inspected,
        "GO-2026-5932 затрагивает только OpenPGP; go list -deps собранного Caddy не содержит этих пакетов.",
        "https://pkg.go.dev/vuln/GO-2026-5932",
        DependenciesSHA256=hashlib.sha256(dependencies.encode()).hexdigest(),
        DependencyPackages=packages,
    )


def go_jose_evidence(inspected):
    caddyfile = image_file(inspected, "/etc/caddy/Caddyfile")
    if JWK_FROM_CLIENTS.search(caddyfile):
        raise ValueError("Caddyfile включает ACME-сервер: разбор чужих JWK достижим")
    return evidence_for(
        GO_JOSE_V3,
        inspected,
        "CVE-2026-116587 – отказ в обслуживании при разборе чужих RSA JWK. В Caddy go-jose v3 используется "
        "провижионерами smallstep и ACME-сервером; Caddyfile образа не включает acme_server, встроенный CA только "
        "подписывает своим ключом. Исправленной версии v3 нет; решение владельца от 2026-10-28.",
        "https://github.com/go-jose/go-jose/security/advisories/GHSA-mgcc-cww5-fp2q",
        CaddyfileSHA256=hashlib.sha256(caddyfile.encode()).hexdigest(),
    )


APPLICABILITY = {OPENPGP: openpgp_evidence, GO_JOSE_V3: go_jose_evidence}


def caddy_applicability(report, image):
    present = tuple(candidate for candidate in APPLICABILITY if reported(report, candidate))
    if not present or not image:
        return (), []
    inspected = verified_image(report, image)
    return present, [APPLICABILITY[candidate](inspected) for candidate in present]


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
    parser.add_argument("--image", help="Образ для доказательства достижимости Go-пакетов; digest сверяется с отчётом")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text())
        sanitized = redacted_report(report)
        findings = blocking_findings(report)
        verification_failed = False
        try:
            exclusions, applicability = caddy_applicability(report, args.image)
            findings = blocking_findings(report, exclusions)
            sanitized["Applicability"] = applicability
        except (OSError, ValueError, subprocess.SubprocessError):
            verification_failed = True
            sanitized["ApplicabilityError"] = "Не удалось проверить применимость находки к digest образа"
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(sanitized, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        print("Отчёт образа отсутствует или некорректен", file=sys.stderr)
        return 1
    for finding in findings:
        print(f"{finding['severity']}: {finding['id']} ({finding['package']})")
    return 1 if findings or verification_failed else 0


if __name__ == "__main__":
    sys.exit(main())
