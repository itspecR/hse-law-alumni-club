import fcntl
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
spec = importlib.util.spec_from_file_location("autodeploy", LIB / "autodeploy.py")
autodeploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autodeploy)

OLD = "a" * 40
NEW = "b" * 40
NOW = 1_000_000


def runs(conclusion="success", status="completed", names=autodeploy.REQUIRED_CHECKS):
    return [{"name": name, "status": status, "conclusion": conclusion} for name in names]


class CiState(unittest.TestCase):
    def test_all_required_checks_green(self):
        self.assertEqual(autodeploy.ci_state(runs() + runs("failure", names=("other",))), "green")

    def test_missing_or_running_check_is_pending(self):
        self.assertEqual(autodeploy.ci_state(runs(names=autodeploy.REQUIRED_CHECKS[:-1])), "pending")
        self.assertEqual(autodeploy.ci_state(runs()[:-1] + runs(None, "in_progress", ("secrets",))), "pending")
        self.assertEqual(autodeploy.ci_state([]), "pending")

    def test_any_failed_required_check_is_red(self):
        for conclusion in ("failure", "cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                failed = runs()[:-1] + runs(conclusion, names=("secrets",))
                self.assertEqual(autodeploy.ci_state(failed), "red")
        self.assertEqual(autodeploy.ci_state(runs("failure", names=("build-test",))), "red")


class Decision(unittest.TestCase):
    def decide(self, deployed=OLD, ci="green", state=None, now=NOW, busy=False):
        return autodeploy.decide(deployed, NEW, ci, state or {}, now, busy)

    def test_current_commit_is_left_alone(self):
        self.assertEqual(self.decide(deployed=NEW, ci="red"), "current")

    def test_new_commit_waits_for_green_ci(self):
        self.assertEqual(self.decide(ci="pending"), "wait-ci")
        self.assertEqual(self.decide(ci="red"), "ci-failed")
        self.assertEqual(self.decide(), "deploy")

    def test_busy_maintenance_does_not_spend_an_attempt(self):
        self.assertEqual(self.decide(busy=True), "busy")

    def test_failed_commit_is_retried_after_pause(self):
        state = {"commit": NEW, "attempts": 1, "last_attempt": NOW}
        self.assertEqual(self.decide(state=state, now=NOW + autodeploy.RETRY_PAUSE_SECONDS - 1), "pause")
        self.assertEqual(self.decide(state=state, now=NOW + autodeploy.RETRY_PAUSE_SECONDS), "deploy")

    def test_attempts_stop_at_limit_until_next_commit(self):
        state = {"commit": NEW, "attempts": autodeploy.MAX_ATTEMPTS, "last_attempt": 0}
        self.assertEqual(self.decide(state=state), "exhausted")
        self.assertEqual(autodeploy.decide(OLD, "c" * 40, "green", state, NOW, False), "deploy")
        self.assertEqual(autodeploy.attempts_for(state, "c" * 40), 0)


class Files(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_state_survives_round_trip_and_bad_file(self):
        path = self.root / autodeploy.STATE_FILE
        self.assertEqual(autodeploy.load_state(path), {})
        autodeploy.save_state(path, {"commit": NEW, "attempts": 2, "last_attempt": NOW})
        self.assertEqual(autodeploy.load_state(path)["attempts"], 2)
        path.write_text("{broken")
        self.assertEqual(autodeploy.load_state(path), {})

    def test_state_dir_follows_installed_config(self):
        env = self.root / "compose.env"
        self.assertEqual(autodeploy.state_dir(env), autodeploy.DEFAULT_STATE_DIR)
        env.write_text("CLUB_STATE_DIR=/srv/old\nOTHER=1\nCLUB_STATE_DIR=/srv/club-state\n")
        self.assertEqual(autodeploy.state_dir(env), pathlib.Path("/srv/club-state"))

    def test_maintenance_lock_is_detected_and_released(self):
        lock = self.root / autodeploy.LOCK_FILE
        self.assertFalse(autodeploy.maintenance_busy(lock))
        with lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(autodeploy.maintenance_busy(lock))
        self.assertFalse(autodeploy.maintenance_busy(lock))


class Repository(unittest.TestCase):
    def test_github_remote_forms(self):
        for url in (
            "https://github.com/itspecR/hse-law-alumni-club",
            "https://github.com/itspecR/hse-law-alumni-club.git",
            "git@github.com:itspecR/hse-law-alumni-club.git",
        ):
            with self.subTest(url=url):
                self.assertEqual(autodeploy.repository(url), "itspecR/hse-law-alumni-club")

    def test_non_github_remote_is_rejected(self):
        with self.assertRaises(ValueError):
            autodeploy.repository("https://example.com/club.git")


def git(directory, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class Cycle(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temporary.name)
        seed, origin, self.work, self.state = root / "seed", root / "origin.git", root / "work", root / "state"
        git(root, "init", "-q", "-b", "main", str(seed))
        git(seed, "commit", "-q", "--allow-empty", "-m", "first")
        git(root, "clone", "-q", "--bare", str(seed), str(origin))
        git(root, "clone", "-q", str(origin), str(self.work))
        git(seed, "commit", "-q", "--allow-empty", "-m", "second")
        git(seed, "push", "-q", str(origin), "main")
        self.target = git(seed, "rev-parse", "HEAD")
        env = root / "compose.env"
        env.write_text(f"CLUB_STATE_DIR={self.state}\n")
        self.ci, self.deployments, self.deploy_result = "green", [], False
        self.saved = {
            name: getattr(autodeploy, name)
            for name in ("REPO", "INSTALLED_ENV_FILE", "check_runs", "deploy", "repository")
        }
        autodeploy.REPO, autodeploy.INSTALLED_ENV_FILE = self.work, env
        autodeploy.repository = lambda url: "owner/club"
        autodeploy.check_runs = lambda slug, revision, token: runs() if self.ci == "green" else runs(None, "queued")
        autodeploy.deploy = lambda target: self.deployments.append(target) or self.deploy_result

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(autodeploy, name, value)
        self.temporary.cleanup()

    def state_file(self):
        return json.loads((self.state / autodeploy.STATE_FILE).read_text())

    def test_pending_ci_does_not_deploy(self):
        self.ci = "pending"
        self.assertEqual(autodeploy.main(), 0)
        self.assertEqual(self.deployments, [])

    def test_failed_deploy_is_counted_and_paused(self):
        self.assertEqual(autodeploy.main(), 1)
        self.assertEqual(self.deployments, [self.target])
        self.assertEqual(self.state_file()["attempts"], 1)
        self.assertEqual(autodeploy.main(), 0)
        self.assertEqual(self.deployments, [self.target])

    def test_successful_deploy_reaches_current(self):
        self.deploy_result = True
        self.assertEqual(autodeploy.main(), 0)
        git(self.work, "fetch", "-q", "origin")
        git(self.work, "checkout", "-q", "--detach", self.target)
        self.assertEqual(autodeploy.main(), 0)
        self.assertEqual(self.deployments, [self.target])


if __name__ == "__main__":
    unittest.main()
