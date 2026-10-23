import pathlib
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = "scripts/deploy.sh"


class DeployEntrypoint(unittest.TestCase):
    def test_deploy_is_executable_in_git(self):
        entry = subprocess.run(["git", "ls-files", "-s", DEPLOY], cwd=REPO, capture_output=True, text=True, check=True)
        self.assertTrue(entry.stdout.startswith("100755 "), entry.stdout)

    def test_branch_argument_is_rejected_before_any_step(self):
        result = subprocess.run([str(REPO / DEPLOY), "feat/themis-alive"], cwd=REPO, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("не принимает аргументы", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
