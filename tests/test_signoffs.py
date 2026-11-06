import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("signoffs", ROOT / "scripts/signoffs.py")
signoffs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signoffs)
POLICY = json.loads((ROOT / "policy.json").read_text())


def environment(name, env_id):
    return {"id": env_id, "protection_rules": [{
        "type": "required_reviewers", "reviewers": [
            {"type": "User", "reviewer": {"id": 101}},
            {"type": "User", "reviewer": {"id": 202}}
        ]
    }]}


def review(name, env_id, user_id, state="approved"):
    return {"state": state, "user": {"id": user_id, "login": "person" + str(user_id)},
            "environments": [{"id": env_id, "name": name}]}


class SignoffPolicyTests(unittest.TestCase):
    def setUp(self):
        self.required = ["code_check", "blso"]
        self.environments = {"code-check": environment("code-check", 1),
                             "blso": environment("blso", 2)}

    def validate(self, reviews, environments=None):
        return signoffs.validate_approval_records(
            POLICY, self.required, reviews,
            self.environments if environments is None else environments)

    def test_billing_changes_activate_all_roles(self):
        self.assertEqual(set(signoffs.required_gates(POLICY, "main", ["app/billing/pricing.py"])),
                         set(POLICY["gates"]))

    def test_docs_only_do_not_require_manual_approvals(self):
        self.assertEqual(signoffs.required_gates(POLICY, "main", ["README.md"]), [])

    def test_other_target_branch_does_not_require_approvals(self):
        self.assertEqual(signoffs.required_gates(POLICY, "feature", ["app/billing/pricing.py"]), [])

    def test_non_billing_changes_do_not_require_blso(self):
        self.assertEqual(signoffs.required_gates(POLICY, "main", ["app/ui/view.py"]),
                         ["code_check", "qa_dev"])

    def test_same_person_cannot_sign_incompatible_roles(self):
        _, errors = self.validate([review("code-check", 1, 101), review("blso", 2, 101)])
        self.assertTrue(any("Different people" in error for error in errors))

    def test_distinct_eligible_approvers_satisfy_exclusion(self):
        approved, errors = self.validate([review("code-check", 1, 101), review("blso", 2, 202)])
        self.assertEqual(errors, [])
        self.assertEqual(approved["blso"]["id"], 202)

    def test_bulk_approval_is_still_checked_per_role(self):
        bulk = review("code-check", 1, 101)
        bulk["environments"].append({"id": 2, "name": "blso"})
        _, errors = self.validate([bulk])
        self.assertTrue(any("Different people" in error for error in errors))

    def test_missing_approval_fails(self):
        _, errors = self.validate([review("code-check", 1, 101)])
        self.assertTrue(any("no explicit approval" in error for error in errors))

    def test_rejected_approval_fails(self):
        _, errors = self.validate([review("code-check", 1, 101), review("blso", 2, 202, "rejected")])
        self.assertTrue(any("rejection" in error for error in errors))

    def test_unknown_or_bypass_identity_fails(self):
        _, errors = self.validate([review("code-check", 1, 999), review("blso", 2, 202)])
        self.assertTrue(any("configured reviewer" in error for error in errors))

    def test_environment_without_reviewers_fails(self):
        configs = copy.deepcopy(self.environments)
        configs["code-check"]["protection_rules"] = []
        _, errors = self.validate([review("code-check", 1, 101), review("blso", 2, 202)], configs)
        self.assertTrue(any("no configured user reviewers" in error for error in errors))

    def test_changed_environment_identity_fails(self):
        _, errors = self.validate([review("code-check", 1234, 101), review("blso", 2, 202)])
        self.assertTrue(any("no explicit approval" in error for error in errors))

    def test_multiple_ambiguous_approvers_fail_closed(self):
        _, errors = self.validate([review("code-check", 1, 101), review("code-check", 1, 202),
                                   review("blso", 2, 202)])
        self.assertTrue(any("unambiguous" in error for error in errors))


class WorkflowValidationTests(unittest.TestCase):
    def run_validation(self, same_user=False, job_failure=False, attempt="1"):
        pr = {"number": 1, "head": {"sha": "abc"}, "base": {"sha": "def"}}
        required = ["code_check", "blso"]
        jobs = {key: {"result": "success" if key in ["plan", "nightly"] + required else "skipped"}
                for key in ["plan", "nightly"] + list(POLICY["gates"])}
        if job_failure:
            jobs["nightly"]["result"] = "failure"
        reviews = [review("code-check", 1, 101), review("blso", 2, 101 if same_user else 202)]

        def fake_api(path):
            if path.endswith("/approvals"):
                return reviews
            if path.endswith("/environments/code-check"):
                return environment("code-check", 1)
            if path.endswith("/environments/blso"):
                return environment("blso", 2)
            return pr

        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": attempt, "GITHUB_RUN_ID": "10",
                                     "JOB_RESULTS": json.dumps(jobs)}), \
                patch.object(signoffs, "snapshot", return_value=(POLICY, required, "owner/demo", pr)), \
                patch.object(signoffs, "api_json", side_effect=fake_api), \
                patch.object(signoffs, "summary") as output:
            signoffs.main("validate")
            return output.call_args.args[0]

    def test_complete_validation_succeeds_for_distinct_people(self):
        self.assertIn("exclusion rules are satisfied", self.run_validation())

    def test_complete_validation_blocks_conflicting_people(self):
        with self.assertRaises(SystemExit):
            self.run_validation(same_user=True)

    def test_failed_tests_cannot_pass_merge_gate(self):
        with self.assertRaises(SystemExit):
            self.run_validation(job_failure=True)

    def test_rerun_cannot_reuse_old_approval_history(self):
        with self.assertRaisesRegex(ValueError, "reruns are disabled"):
            self.run_validation(attempt="2")

    def test_plan_emits_conditional_job_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "outputs"
            with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "1", "GITHUB_OUTPUT": str(output)}), \
                    patch.object(signoffs, "snapshot", return_value=(POLICY, ["qa_dev"], "owner/demo", {})), \
                    patch.object(signoffs, "summary"):
                signoffs.main("plan")
            self.assertEqual(output.read_text().splitlines(),
                             ["code_check=false", "qa_dev=true", "blso=false", "architect=false"])
