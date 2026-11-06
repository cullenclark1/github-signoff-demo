#!/usr/bin/env python3
"""Create a new PUBLIC demo repository using your locally authenticated gh CLI.

No token is requested, printed, stored, or passed on the command line.
Existing repositories are never modified by this setup command.
"""

import argparse
import base64
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
GATES = ("code-check", "qa-dev", "blso", "architect")


def command(args, payload=None):
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Command failed")
    return result.stdout.strip()


def api(path, method="GET", payload=None):
    args = ["gh", "api", "--hostname", "github.com", "--method", method, path,
            "-H", "Accept: application/vnd.github+json"]
    if payload is not None:
        args += ["--input", "-"]
    output = command(args, payload)
    return json.loads(output) if output else None


def write_commit(repo, parent_sha, changes, message):
    parent = api(f"repos/{repo}/git/commits/{parent_sha}")
    entries = []
    for path, content in changes.items():
        blob = api(f"repos/{repo}/git/blobs", "POST", {
            "content": base64.b64encode(content).decode("ascii"), "encoding": "base64"
        })
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = api(f"repos/{repo}/git/trees", "POST", {
        "base_tree": parent["tree"]["sha"], "tree": entries
    })
    commit = api(f"repos/{repo}/git/commits", "POST", {
        "message": message, "tree": tree["sha"], "parents": [parent_sha]
    })
    return commit["sha"]


def repository_files(default_branch):
    paths = ["README.md", ".gitignore", "policy.json", "app/billing/pricing.py",
             "tests/test_pricing.py", ".github/workflows/signoffs.yml",
             "scripts/signoffs.py", "tests/test_signoffs.py", "tests/test_setup.py", "setup_demo.py"]
    files = {path: (ROOT / path).read_bytes() for path in paths}
    policy = json.loads(files["policy.json"])
    policy["target_branches"] = [default_branch]
    files["policy.json"] = (json.dumps(policy, indent=2) + "\n").encode()
    return files


def create_demo(name, extra_reviewers):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", name):
        raise ValueError("Use a repository name of 1–80 letters, numbers, dots, hyphens, or underscores.")
    if not shutil.which("gh"):
        raise ValueError("Install GitHub CLI from https://cli.github.com/ first.")
    command(["gh", "auth", "status", "--hostname", "github.com"])
    owner = api("user")
    repo = owner["login"] + "/" + name
    reviewers = {owner["id"]: owner}
    for login in extra_reviewers:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login):
            raise ValueError("Supply the second reviewer's exact GitHub username.")
        user = api("users/" + login)
        reviewers[user["id"]] = user
    if len(reviewers) > 6:
        raise ValueError("This demo supports up to six eligible user reviewers per environment.")
    print(f"Creating PUBLIC repository https://github.com/{repo}", flush=True)
    # GitHub rejects a name collision. Do not modify a pre-existing repository.
    created = api("user/repos", "POST", {
        "name": name, "description": "Demo: explicit parallel sign-offs with separation of duties",
        "private": False, "auto_init": True
    })
    state = {"repository": repo, "url": created["html_url"], "completed": False}
    state_path = ROOT / ".demo-setup-state.json"
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    try:
        branch = created["default_branch"]
        ref = api(f"repos/{repo}/git/ref/heads/{branch}")
        bootstrap_files = repository_files(branch)
        base_sha = write_commit(repo, ref["object"]["sha"], bootstrap_files,
                                "Set up explicit conditional sign-off demo")
        api(f"repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": base_sha, "force": False})
        pending = []
        for user in reviewers.values():
            if user["id"] != owner["id"]:
                api(f'repos/{repo}/collaborators/{user["login"]}', "PUT", {"permission": "push"})
                pending.append(user["login"])
        for environment in GATES:
            print("Configuring approval: " + environment, flush=True)
            api(f"repos/{repo}/environments/{environment}", "PUT", {
                "wait_timer": 0,
                "prevent_self_review": False,
                "can_admins_bypass": False,
                "reviewers": [{"type": "User", "id": user_id} for user_id in reviewers],
                "deployment_branch_policy": None
            })
        api(f"repos/{repo}/rulesets", "POST", {
            "name": "Require valid explicit sign-offs",
            "target": "branch", "enforcement": "active", "bypass_actors": [],
            "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {"type": "pull_request", "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False
                }},
                {"type": "required_status_checks", "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": "Required sign-offs"}]
                }}
            ]
        })
        # Only synthetic application code is changed in the example PR.
        pricing = bootstrap_files["app/billing/pricing.py"].decode()
        demo_change = pricing + '\n\ndef example_invoice():\n    return invoice_total(250, 4)\n'
        demo_sha = write_commit(repo, base_sha,
                                {"app/billing/pricing.py": demo_change.encode()},
                                "Demo billing change requiring all four sign-offs")
        demo_branch = "demo/billing-signoffs"
        api(f"repos/{repo}/git/refs", "POST", {"ref": "refs/heads/" + demo_branch, "sha": demo_sha})
        body = (
            "This synthetic billing change activates **Code Check, QA Dev, BLSO, and Architect**.\n\n"
            "1. Open **Checks**, then the **Conditional sign-offs** workflow run.\n"
            "2. Wait for the four named approval jobs to become ready in parallel.\n"
            "3. Click **Review deployments** and select only the named roles you want to approve.\n"
            "4. To demonstrate the exclusion, approve all four yourself. **Required sign-offs** will fail "
            "because Code Check and BLSO must have different approvers.\n\n"
            "To demonstrate success, use two eligible people for Code Check and BLSO. "
            "Other roles may overlap. To reset an attempted approval, push a fresh commit; "
            "this demo deliberately rejects reruns rather than reusing ambiguous approval history.\n\n"
            "Nightly example tests execute automatically on this PR; they do not wait until nighttime. "
            "The environment approval dialog calls the action **Approve and deploy**, but these jobs "
            "only record sign-offs and deploy nothing.\n\n"
            "The repository's active ruleset blocks merging until **Required sign-offs** passes."
        )
        pr = api(f"repos/{repo}/pulls", "POST", {
            "title": "Demo: billing change requiring parallel sign-offs",
            "head": demo_branch, "base": branch, "body": body, "draft": False
        })
        state.update({"completed": True, "pull_request": pr["html_url"],
                      "actions": created["html_url"] + "/actions", "default_branch": branch})
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        print("\nDemo ready: " + pr["html_url"])
        print("Open the PR's Checks tab to explore the waiting approvals.")
        if pending:
            print("Additional reviewers must accept their repository invitation: " + ", ".join(pending))
        else:
            print("You can test the UI and conflict now. A successful Code Check/BLSO pair needs another reviewer.")
        return state
    except Exception:
        print(f"\nSetup stopped. The new repository remains at {created['html_url']}.", file=sys.stderr)
        print("No existing repository was changed. Setup state is in .demo-setup-state.json.", file=sys.stderr)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="github-signoff-demo", help="Name for a NEW public repository")
    parser.add_argument("--reviewer", action="append", default=[],
                        help="Optional exact GitHub username to invite as an additional reviewer")
    args = parser.parse_args()
    try:
        create_demo(args.repo, args.reviewer)
    except (RuntimeError, ValueError, OSError) as error:
        print("Setup failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
