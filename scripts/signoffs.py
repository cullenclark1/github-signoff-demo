#!/usr/bin/env python3
"""Plan explicit environment sign-offs and validate recorded approval identities."""

import fnmatch
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def required_gates(policy, target_branch, changed_paths):
    if target_branch not in policy["target_branches"]:
        return []
    return [
        key for key, gate in policy["gates"].items()
        if any(fnmatch.fnmatchcase(path, pattern)
               for path in changed_paths for pattern in gate["paths"])
    ]


def validate_approval_records(policy, required, reviews, environments):
    """Return one authentic user per required environment plus any violations.

    Require one unambiguous reviewer for each gate; fail closed on overrides,
    missing protection, rejected environments, and conflicting identities.
    """
    errors, approved_by = [], {}
    for key in required:
        gate = policy["gates"][key]
        name = gate["environment"]
        config = environments.get(name, {})
        allowed = {
            reviewer["reviewer"]["id"]
            for rule in config.get("protection_rules", [])
            if rule.get("type") == "required_reviewers"
            for reviewer in rule.get("reviewers", [])
            if reviewer.get("type") == "User"
        }
        if not allowed:
            errors.append(f'{gate["name"]}: no configured user reviewers; refusing to treat this as approval.')
            continue
        # This personal-account demo intentionally configures users, not teams.
        relevant = [r for r in reviews if any(
            e.get("name", "").casefold() == name.casefold()
            and e.get("id") == config.get("id")
            for e in r.get("environments", [])
        )]
        if not relevant:
            errors.append(f'{gate["name"]}: no explicit approval was recorded.')
            continue
        if any(r.get("state") != "approved" for r in relevant):
            errors.append(f'{gate["name"]}: review history includes a rejection or unrecognized state. Start a fresh run.')
            continue
        users = {}
        for review in relevant:
            user = review.get("user", {})
            if user.get("id") not in allowed:
                errors.append(f'{gate["name"]}: approval was not from a configured reviewer (or was bypassed).')
            else:
                users[user["id"]] = user.get("login", str(user["id"]))
        if len(users) != 1:
            errors.append(f'{gate["name"]}: expected exactly one unambiguous approving identity.')
        else:
            user_id, login = next(iter(users.items()))
            approved_by[key] = {"id": user_id, "login": login}
    for left, right in policy["incompatible"]:
        if left in approved_by and right in approved_by:
            if approved_by[left]["id"] == approved_by[right]["id"]:
                errors.append(
                    f'@{approved_by[left]["login"]} approved both '
                    f'{policy["gates"][left]["name"]} and {policy["gates"][right]["name"]}. '
                    'Different people must approve these sign-offs.'
                )
    return approved_by, errors


def api_json(path):
    url = os.environ.get("GITHUB_API_URL", "https://api.github.com") + "/" + path.lstrip("/")
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json",
        "User-Agent": "explicit-signoff-demo",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def snapshot():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if "pull_request" not in event:
        raise ValueError("This demonstration only accepts pull_request events.")
    pr = event["pull_request"]
    repo = os.environ["GITHUB_REPOSITORY"]
    current = api_json(f'repos/{repo}/pulls/{pr["number"]}')
    if current["head"]["sha"] != pr["head"]["sha"]:
        raise ValueError("This run is for an older commit. Use the latest PR run.")
    if current["base"]["ref"] != pr["base"]["ref"] or current["base"]["sha"] != pr["base"]["sha"]:
        raise ValueError("The target branch changed. Update the PR branch to start a fresh run.")
    if current.get("changed_files", 0) > 3000:
        raise ValueError("PR exceeds GitHub's changed-files API limit; refusing an incomplete policy evaluation.")
    files, page = [], 1
    while True:
        batch = api_json(f'repos/{repo}/pulls/{pr["number"]}/files?per_page=100&page={page}')
        files.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    if len(files) != current["changed_files"]:
        raise ValueError("Changed-file list is incomplete or changed during evaluation; start a fresh run.")
    paths = [path for file in files for path in
             [file["filename"], file.get("previous_filename")] if path]
    policy = json.loads((ROOT / "policy.json").read_text())
    required = required_gates(policy, current["base"]["ref"], paths)
    return policy, required, repo, current


def summary(text):
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
            handle.write(text + "\n")


def main(mode):
    # The history endpoint does not expose an attempt identifier. A fresh run is
    # the safe way to reset approvals in this intentionally small demonstration.
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise ValueError("Demo reruns are disabled. Push a fresh commit to reset approvals unambiguously.")
    policy, required, repo, pr = snapshot()
    if mode == "plan":
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key in policy["gates"]:
                output.write(f'{key}={str(key in required).lower()}\n')
        rows = ["## Required sign-offs", "", "| Sign-off | Applies |", "| --- | --- |"]
        rows += [f'| {gate["name"]} | {"Required" if key in required else "Not required"} |'
                 for key, gate in policy["gates"].items()]
        rows += ["", "Open **Review deployments** to approve individual environments.",
                 "Code Check and BLSO must be approved by different people."]
        summary("\n".join(rows))
        return
    results = json.loads(os.environ["JOB_RESULTS"])
    errors = []
    for key in ["plan", "nightly"] + required:
        if results.get(key, {}).get("result") != "success":
            errors.append(f"{key}: job did not complete successfully.")
    for key in set(policy["gates"]) - set(required):
        if results.get(key, {}).get("result") != "skipped":
            errors.append(f"{key}: unexpected job state for an inapplicable gate.")
    reviews = api_json(f'repos/{repo}/actions/runs/{os.environ["GITHUB_RUN_ID"]}/approvals')
    environments = {
        policy["gates"][key]["environment"]: api_json(
            f'repos/{repo}/environments/{policy["gates"][key]["environment"]}'
        ) for key in required
    }
    approved_by, violations = validate_approval_records(policy, required, reviews, environments)
    errors.extend(violations)
    # Recheck after the API reads so an obsolete run cannot certify a newer head.
    latest = api_json(f'repos/{repo}/pulls/{pr["number"]}')
    if (latest["head"]["sha"] != pr["head"]["sha"]
            or latest["base"]["sha"] != pr["base"]["sha"]):
        errors.append("The PR changed while validating. Use a fresh run.")
    rows = ["## Sign-off validation", "", f'Commit: `{pr["head"]["sha"]}`', "",
            "| Sign-off | Approved by |", "| --- | --- |"]
    rows += [f'| {policy["gates"][key]["name"]} | @{who["login"]} |'
             for key, who in approved_by.items()]
    if errors:
        rows += ["", "### Merge blocked", ""] + ["- " + error for error in errors]
        rows += ["", "To reset: push a fresh commit, then have different eligible people approve Code Check and BLSO."]
    else:
        rows += ["", "All applicable sign-offs are explicitly approved and exclusion rules are satisfied."]
    summary("\n".join(rows))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in {"plan", "validate"}:
            raise ValueError("Usage: signoffs.py plan|validate")
        main(sys.argv[1])
    except (ValueError, KeyError, OSError, urllib.error.URLError) as error:
        summary("Sign-off validation could not finish: " + str(error))
        raise SystemExit(1)
