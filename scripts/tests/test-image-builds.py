import pathlib
import re
import unittest

DEPLOY = pathlib.Path(__file__).resolve().parents[2] / "deploy"
LOCAL_PACKAGES = {
    "web.Dockerfile": {"club-web"},
    "api.Dockerfile": {"club-api"},
    "bootstrap.Dockerfile": {"club-ops", "club-api"},
}


def project_installs(dockerfile):
    commands = (DEPLOY / dockerfile).read_text(encoding="utf-8").replace("\\\n", " ").splitlines()
    return [line for line in commands if "uv sync" in line and "--no-install-project" not in line]


class LocalPackagesAreRebuilt(unittest.TestCase):
    def test_every_project_install_reinstalls_local_packages(self):
        for dockerfile, packages in LOCAL_PACKAGES.items():
            installs = project_installs(dockerfile)
            self.assertTrue(installs, dockerfile)
            for line in installs:
                self.assertEqual(set(re.findall(r"--reinstall-package\s+(\S+)", line)), packages, dockerfile)


if __name__ == "__main__":
    unittest.main()
