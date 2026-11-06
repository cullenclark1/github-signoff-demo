import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("setup_demo", ROOT / "setup_demo.py")
setup_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_demo)


class SetupTests(unittest.TestCase):
    def test_setup_creates_parallel_environments_rules_and_a_real_pr_request(self):
        calls = []
        # Capture the real file payload before isolating setup's local state file.
        files = setup_demo.repository_files("main")

        def fake_api(path, method="GET", payload=None):
            calls.append((path, method, payload))
            if path == "user":
                return {"login": "demo-owner", "id": 101}
            if path == "user/repos":
                return {"html_url": "https://github.com/demo-owner/sandbox", "default_branch": "main"}
            if "/git/ref/heads/" in path:
                return {"object": {"sha": "initial"}}
            if method == "GET" and "/git/commits/" in path:
                return {"tree": {"sha": "base-tree"}}
            if path.endswith("/pulls"):
                return {"html_url": "https://github.com/demo-owner/sandbox/pull/1"}
            return {"sha": "created-sha"}

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(setup_demo, "ROOT", Path(temp)), \
                patch.object(setup_demo, "api", side_effect=fake_api), \
                patch.object(setup_demo, "command", return_value=""), \
                patch.object(setup_demo.shutil, "which", return_value="/usr/bin/gh"), \
                patch.object(setup_demo, "repository_files", return_value=files), \
                patch("builtins.print"):
            state = setup_demo.create_demo("sandbox", [])
        self.assertTrue(state["completed"])
        envs = [call for call in calls if "/environments/" in call[0]]
        self.assertEqual(len(envs), 4)
        self.assertTrue(all(call[2]["reviewers"] == [{"type": "User", "id": 101}] for call in envs))
        rules = next(call[2] for call in calls if call[0].endswith("/rulesets"))
        self.assertEqual(rules["bypass_actors"], [])
        required = next(rule for rule in rules["rules"] if rule["type"] == "required_status_checks")
        self.assertEqual(required["parameters"]["required_status_checks"][0]["context"], "Required sign-offs")
        pr = next(call[2] for call in calls if call[0].endswith("/pulls"))
        self.assertEqual(pr["head"], "demo/billing-signoffs")

    def test_existing_repo_is_not_updated_when_creation_fails(self):
        calls = []

        def fake_api(path, method="GET", payload=None):
            calls.append(path)
            if path == "user":
                return {"login": "demo-owner", "id": 101}
            raise RuntimeError("Repository name already exists")

        with patch.object(setup_demo, "api", side_effect=fake_api), \
                patch.object(setup_demo, "command", return_value=""), \
                patch.object(setup_demo.shutil, "which", return_value="/usr/bin/gh"), \
                patch("builtins.print"):
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                setup_demo.create_demo("sandbox", [])
        self.assertEqual(calls, ["user", "user/repos"])
