import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory = load("inventory", "scripts/diagnostics/security-inventory.py")
audit = load("image_audit", "scripts/checks/check-image-audit.py")


class SecurityEvidenceTests(unittest.TestCase):
    def test_failed_command_does_not_disclose_stderr(self):
        result = subprocess.CompletedProcess(["docker"], 1, "", "secret-password-in-error")
        with patch.object(inventory.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "кодом 1") as caught:
                inventory.command(["docker", "inspect"])
        self.assertNotIn("secret-password", str(caught.exception))

    def test_stopped_container_is_not_reported_as_runtime(self):
        commands = []

        def run(arguments):
            commands.append(arguments)
            return json.dumps({"running": False, "image_id": "sha256:synthetic", "revision": None})

        with self.assertRaisesRegex(RuntimeError, "остановлен"):
            inventory.container_inventory("api", "synthetic-api", run)
        self.assertEqual(len(commands), 1)

    def test_private_report_does_not_overwrite_previous_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            inventory.write_report(path, {"synthetic": True})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                inventory.write_report(path, {"synthetic": False})
            self.assertTrue(json.loads(path.read_text())["synthetic"])

    def test_unavailable_runtime_returns_incomplete_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with (
                patch.object(
                    inventory.sys, "argv", ["inventory", "--output", str(output), "--container", "api=synthetic-api"]
                ),
                patch.object(inventory, "command", return_value="synthetic-revision"),
                patch.object(inventory, "container_inventory", side_effect=RuntimeError("Контейнер недоступен")),
            ):
                self.assertEqual(inventory.main(), 1)
            report = json.loads(output.read_text())
            self.assertFalse(report["collection_complete"])
            self.assertFalse(report["assessment_performed"])
            self.assertEqual(report["errors"][0]["scope"], "api")

    def test_medium_retained_and_high_blocks_image(self):
        report = {
            "ArtifactName": "synthetic",
            "Results": [
                {
                    "Vulnerabilities": [
                        {"Severity": "MEDIUM", "VulnerabilityID": "synthetic-medium", "PkgName": "package"},
                        {"Severity": "HIGH", "VulnerabilityID": "synthetic-high", "PkgName": "package"},
                    ]
                }
            ],
        }
        self.assertEqual(
            audit.blocking_findings(report), [{"id": "synthetic-high", "package": "package", "severity": "HIGH"}]
        )
        self.assertEqual(len(report["Results"][0]["Vulnerabilities"]), 2)

    def test_missing_scan_is_not_a_clean_result(self):
        with self.assertRaises(ValueError):
            audit.blocking_findings({"ArtifactName": "synthetic"})

    def caddy_report(self):
        return {
            "ArtifactName": "synthetic-caddy",
            "Metadata": {"ImageID": "sha256:" + "a" * 64},
            "Results": [
                {
                    "Target": "usr/bin/caddy",
                    "Vulnerabilities": [
                        {
                            "Severity": "UNKNOWN",
                            "VulnerabilityID": "GO-2026-5932",
                            "PkgName": "golang.org/x/crypto",
                            "InstalledVersion": "v0.58.0",
                        },
                        {
                            "Severity": "HIGH",
                            "VulnerabilityID": "synthetic-other",
                            "PkgName": "golang.org/x/crypto",
                            "InstalledVersion": "v0.58.0",
                        },
                    ],
                }
            ],
        }

    def test_openpgp_exception_requires_same_image_and_exact_package_evidence(self):
        report = self.caddy_report()
        inspected = subprocess.CompletedProcess([], 0, report["Metadata"]["ImageID"] + "\n", "")
        packages = "github.com/caddyserver/caddy/v2/cmd\ngolang.org/x/crypto/acme\n"
        dependencies = subprocess.CompletedProcess([], 0, packages, "")
        with patch.object(audit.subprocess, "run", side_effect=[inspected, dependencies]) as run:
            exclusions, evidence = audit.caddy_applicability(report, "synthetic-caddy")
        self.assertEqual(
            audit.blocking_findings(report, exclusions),
            [{"id": "synthetic-other", "package": "golang.org/x/crypto", "severity": "HIGH"}],
        )
        self.assertEqual(evidence[0]["ImageID"], report["Metadata"]["ImageID"])
        self.assertEqual(evidence[0]["DependencyPackages"], packages.splitlines())
        self.assertIn(report["Metadata"]["ImageID"], run.call_args.args[0])
        self.assertEqual(len(report["Results"][0]["Vulnerabilities"]), 2)

    def test_missing_or_mismatched_image_proof_never_exempts_unknown(self):
        report = self.caddy_report()
        self.assertEqual(audit.caddy_applicability(report, None), ((), []))
        self.assertEqual(len(audit.blocking_findings(report)), 2)
        result = subprocess.CompletedProcess([], 0, "sha256:" + "b" * 64, "")
        with patch.object(audit.subprocess, "run", return_value=result) as run:
            with self.assertRaisesRegex(ValueError, "Digest"):
                audit.caddy_applicability(report, "synthetic-caddy")
        self.assertEqual(run.call_count, 1)

    def test_openpgp_import_blocks_exception(self):
        report = self.caddy_report()
        inspected = subprocess.CompletedProcess([], 0, report["Metadata"]["ImageID"], "")
        dependencies = subprocess.CompletedProcess(
            [], 0, "github.com/caddyserver/caddy/v2/cmd\ngolang.org/x/crypto/openpgp/packet\n", ""
        )
        with patch.object(audit.subprocess, "run", side_effect=[inspected, dependencies]):
            with self.assertRaisesRegex(ValueError, "OpenPGP"):
                audit.caddy_applicability(report, "synthetic-caddy")

    def test_new_crypto_version_requires_new_applicability_review(self):
        report = self.caddy_report()
        report["Results"][0]["Vulnerabilities"][0]["InstalledVersion"] = "v0.59.0"
        with patch.object(audit.subprocess, "run") as run:
            exclusions, evidence = audit.caddy_applicability(report, "synthetic-caddy")
            run.assert_not_called()
        self.assertEqual((exclusions, evidence), ((), []))
        self.assertEqual(len(audit.blocking_findings(report, exclusions)), 2)

    def go_jose_report(self, version="v3.0.5"):
        report = self.caddy_report()
        report["Results"][0]["Vulnerabilities"] = [
            {
                "Severity": "HIGH",
                "VulnerabilityID": "CVE-2026-116587",
                "PkgName": "github.com/go-jose/go-jose/v3",
                "InstalledVersion": version,
            }
        ]
        return report

    def test_go_jose_v3_exception_requires_caddyfile_without_acme_server(self):
        report = self.go_jose_report()
        inspected = subprocess.CompletedProcess([], 0, report["Metadata"]["ImageID"], "")
        caddyfile = subprocess.CompletedProcess([], 0, "example.test {\n\treverse_proxy web:80\n}\n", "")
        with patch.object(audit.subprocess, "run", side_effect=[inspected, caddyfile]) as run:
            exclusions, evidence = audit.caddy_applicability(report, "synthetic-caddy")
        self.assertEqual(audit.blocking_findings(report, exclusions), [])
        self.assertEqual(evidence[0]["VulnerabilityID"], "CVE-2026-116587")
        self.assertEqual(len(evidence[0]["CaddyfileSHA256"]), 64)
        self.assertIn("/etc/caddy/Caddyfile", run.call_args.args[0])
        self.assertEqual(len(report["Results"][0]["Vulnerabilities"]), 1)

    def test_acme_server_blocks_go_jose_exception(self):
        report = self.go_jose_report()
        inspected = subprocess.CompletedProcess([], 0, report["Metadata"]["ImageID"], "")
        caddyfile = subprocess.CompletedProcess([], 0, "example.test {\n\tacme_server\n}\n", "")
        with patch.object(audit.subprocess, "run", side_effect=[inspected, caddyfile]):
            with self.assertRaisesRegex(ValueError, "ACME"):
                audit.caddy_applicability(report, "synthetic-caddy")

    def test_new_go_jose_version_requires_new_applicability_review(self):
        report = self.go_jose_report("v3.0.6")
        with patch.object(audit.subprocess, "run") as run:
            exclusions, evidence = audit.caddy_applicability(report, "synthetic-caddy")
            run.assert_not_called()
        self.assertEqual((exclusions, evidence), ((), []))
        self.assertEqual(len(audit.blocking_findings(report, exclusions)), 1)

    def test_both_caddy_exceptions_use_one_verified_image(self):
        report = self.caddy_report()
        report["Results"][0]["Vulnerabilities"] += self.go_jose_report()["Results"][0]["Vulnerabilities"]
        inspected = subprocess.CompletedProcess([], 0, report["Metadata"]["ImageID"], "")
        dependencies = subprocess.CompletedProcess([], 0, "github.com/caddyserver/caddy/v2/cmd\n", "")
        caddyfile = subprocess.CompletedProcess([], 0, ":80 {\n\trespond ok\n}\n", "")
        with patch.object(audit.subprocess, "run", side_effect=[inspected, dependencies, caddyfile]) as run:
            exclusions, evidence = audit.caddy_applicability(report, "synthetic-caddy")
        self.assertEqual(run.call_count, 3)
        self.assertEqual([item["VulnerabilityID"] for item in evidence], ["GO-2026-5932", "CVE-2026-116587"])
        self.assertEqual(
            audit.blocking_findings(report, exclusions),
            [{"id": "synthetic-other", "package": "golang.org/x/crypto", "severity": "HIGH"}],
        )

    def test_image_evidence_excludes_environment_and_config(self):
        report = {
            "ArtifactName": "synthetic",
            "Results": [],
            "Metadata": {
                "ImageID": "sha256:synthetic",
                "ImageConfig": {"Env": ["TOKEN=synthetic-secret"]},
            },
        }
        sanitized = audit.redacted_report(report)
        self.assertEqual(sanitized["Metadata"], {"ImageID": "sha256:synthetic"})
        self.assertNotIn("synthetic-secret", json.dumps(sanitized))

    def test_blocking_image_still_saves_redacted_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.json"
            output = Path(directory) / "report.json"
            source.write_text(
                json.dumps(
                    {
                        "ArtifactName": "synthetic",
                        "Metadata": {"ImageConfig": {"Env": ["TOKEN=synthetic-secret"]}},
                        "Results": [
                            {
                                "Vulnerabilities": [
                                    {"Severity": "CRITICAL", "VulnerabilityID": "synthetic", "PkgName": "package"}
                                ]
                            }
                        ],
                    }
                )
            )
            with patch.object(audit.sys, "argv", ["audit", str(source), "--output", str(output)]):
                self.assertEqual(audit.main(), 1)
            report = json.loads(output.read_text())
            self.assertEqual(len(report["Results"][0]["Vulnerabilities"]), 1)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertNotIn("synthetic-secret", output.read_text())


if __name__ == "__main__":
    unittest.main()
