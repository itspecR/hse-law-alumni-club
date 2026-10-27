import os
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = "scripts/deploy.sh"
STUB = """import pathlib, sys
log = pathlib.Path(__file__).resolve().parents[3] / "deploy.log"
if sys.argv[1] == "deploy":
    with log.open("a") as handle:
        handle.write("{label}\\n")
    sys.exit({code})
"""


def git(directory, *args):
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def run(directory, *args):
    return subprocess.run(
        ["bash", str(directory / DEPLOY), *args],
        cwd=directory,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"]},
    )


class DeployArguments(unittest.TestCase):
    def test_deploy_is_executable_in_git(self):
        self.assertTrue(git(REPO, "ls-files", "-s", DEPLOY).startswith("100755 "))

    def test_wrong_arguments_stop_before_any_step(self):
        for arguments in (["main", "extra"], ["a..b"], ["--current", "extra"]):
            result = run(REPO, *arguments)
            self.assertEqual(result.returncode, 2, arguments)
            self.assertIn("Использование", result.stderr)
            self.assertEqual(result.stdout, "")


@unittest.skipUnless(os.geteuid() == 0, "сценарий выкладки запускается от root, как на сервере")
class DeployBranches(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temporary.name)
        self.origin, self.work, self.log = root / "origin.git", root / "work", root / "deploy.log"
        seed = root / "seed"
        (seed / "scripts/lib").mkdir(parents=True)
        (seed / "scripts/deploy.sh").write_bytes((REPO / DEPLOY).read_bytes())
        git(root, "init", "-q", "-b", "main", str(seed))
        self.main = self.commit(seed, "main", 0)
        git(seed, "checkout", "-q", "-b", "good")
        self.good = self.commit(seed, "good", 0)
        git(seed, "checkout", "-q", "-b", "bad", "main")
        self.commit(seed, "bad", 1)
        git(root, "clone", "-q", "--bare", str(seed), str(self.origin))
        git(root, "clone", "-q", "-b", "main", str(self.origin), str(self.work))

    def tearDown(self):
        self.temporary.cleanup()

    def commit(self, directory, label, code):
        (directory / "scripts/lib/mariadb_native.py").write_text(STUB.format(label=label, code=code))
        git(directory, "add", "-A")
        git(directory, "commit", "-q", "-m", label)
        return git(directory, "rev-parse", "HEAD")

    def deployed(self):
        return self.log.read_text().split() if self.log.exists() else []

    def test_branch_is_fetched_and_local_edits_are_wiped(self):
        (self.work / "scripts/lib/mariadb_native.py").write_text("edited")
        (self.work / "stray.txt").write_text("untracked")
        result = run(self.work, "good")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), self.good)
        self.assertEqual(self.deployed(), ["good"])
        self.assertEqual(git(self.work, "status", "--porcelain"), "")

    def test_failed_branch_rolls_back_to_previous_commit(self):
        result = run(self.work, "bad")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), self.main)
        self.assertEqual(self.deployed(), ["bad", "main"])
        self.assertIn("Возвращаем прошлую версию", result.stderr)
        self.assertIn("снова работает", result.stderr)
        self.assertIn("last-backup.json", result.stderr)

    def test_without_branch_main_is_deployed(self):
        git(self.work, "checkout", "-q", "--detach", "origin/good")
        result = run(self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), self.main)
        self.assertEqual(self.deployed(), ["main"])

    def test_current_deploys_checkout_without_fetching(self):
        git(self.work, "checkout", "-q", "--detach", "origin/good")
        result = run(self.work, "--current")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), self.good)
        self.assertEqual(self.deployed(), ["good"])


if __name__ == "__main__":
    unittest.main()
