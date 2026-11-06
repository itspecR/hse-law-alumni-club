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
