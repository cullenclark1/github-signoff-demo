# Explicit, parallel GitHub sign-off demo

A public-repository demonstration using GitHub Actions environment approvals.
No GitHub App, custom plugin, external server, deployment target, or stored secret is needed.

## Quick setup

Install [Python 3.10+](https://www.python.org/downloads/) and [GitHub CLI](https://cli.github.com/).
Authenticate GitHub CLI **on your own computer**, then run this directory's setup script:

```sh
gh auth login --hostname github.com --web --git-protocol https --scopes repo,workflow
python setup_demo.py
```

On systems where Python is named `python3`, use `python3 setup_demo.py`.
Do not paste your password, authentication token, or browser cookies into a chat.

This creates a **new public** `github-signoff-demo` repository in your personal account,
four protected approval environments, an active merge ruleset, and an open example PR.
It never edits an existing repository. Choose a different name if that name already exists:

```sh
python setup_demo.py --repo my-signoff-sandbox
```

To invite a second person so a valid two-person approval can be demonstrated:

```sh
python setup_demo.py --repo my-signoff-sandbox --reviewer THEIR_GITHUB_USERNAME
```

They must accept the repository invitation before reviewing. Without a second person,
you can still explore all approval screens and demonstrate the conflict failure.
The setup script uses your local `gh` authentication. It does not ask for, print, or save tokens.

## What to look at

Open the PR URL printed by setup, then **Checks → Conditional sign-offs → workflow run**.
The four manual jobs become ready together after the policy planning job.
Each remains waiting until an eligible user explicitly approves its named environment.

1. Click **Review deployments** in the workflow run.
2. Select **blso** alone and click **Approve and deploy**. This grants only BLSO.
3. Code Check, QA Dev, and Architect remain pending.
4. Approve the other roles to see the final merge check evaluate the recorded identities.

GitHub calls these environments/deployments; the approval jobs only print a message.
**Nothing is deployed.** Approval controls are in the workflow run, rather than a
custom checkbox panel beside the PR's Merge button.

The four roles are independent: **any order is allowed**. There is no requirement
to approve Code Check before BLSO or vice versa.

| Sign-off | When required on the target branch |
| --- | --- |
| Code Check | Changes under `app/`, `tests/`, or `architecture/` |
| QA Dev | Changes under `app/` or `tests/` |
| BLSO | Changes under `app/billing/` |
| Architect | Changes under `app/billing/` or `architecture/` |

The example PR changes `app/billing/pricing.py`, activating all four.
A documentation-only PR requires none of the manual approvals. Nonapplicable jobs
are shown as **skipped**, rather than disappearing from the workflow graph.
The initial setup targets your account's default branch, usually `main`.
Conditions use the **PR target branch**, not the historical branch-creation origin.

"Nightly example tests" runs a real, small unit-test suite on every PR so the demo
does not need to wait until nighttime. It is separate from the manual Code Check sign-off.

## Exclusion rule

`Code Check` and `BLSO` must have **different approving users**. Membership in all
reviewer lists does not automatically sign anything: the user explicitly selects environments.
The same person may approve QA Dev and Architect, or either alongside one conflicting role.

The validator reads GitHub's approval history and checks actual GitHub user IDs:

| Action | Result |
| --- | --- |
| Alice approves Code Check, Bob approves BLSO | Exclusion satisfied |
| Alice approves Code Check and BLSO | Required sign-offs fails; merge blocked |
| Alice bulk-selects all four environments | Same exclusion failure |
| A required approval is missing or rejected | Merge remains blocked |

This enforcement happens **after the manual jobs finish**. It does not disable
a conflicting approval button before the click.

## Reset or recover from a conflict

Push a fresh commit to the example PR branch. A new workflow run requests fresh approvals.
For example, after cloning the demo repository locally:

```sh
git switch demo/billing-signoffs
git commit --allow-empty -m "Reset demo sign-offs"
git push
```

Use a fresh run instead of **Re-run jobs**: this demo deliberately fails repeated
attempts because the review-history endpoint does not identify the attempt associated
with each approval. This keeps old approvals from accidentally satisfying a new attempt.

## Add a reviewer after setup

In repository **Settings → Collaborators**, invite the other user's exact GitHub account.
After they accept, open **Settings → Environments**, select each environment, and add
them under **Required reviewers**. Keep the existing reviewer and disable administrator bypass.
Do not grant a reviewer broader access than needed for your own production policy.

## Scope and limits

- GitHub Free supports these features in **public** repositories. Private environment
  reviewer gates require GitHub Enterprise.
- This personal-account demo uses explicit **user** reviewers, not organizations,
  AD/LDAP sync, or team membership. Users can belong to every reviewer list.
- The active ruleset requires **Required sign-offs** with no configured bypass actors.
- Policy and validation scripts are checked out from the target branch, and new PR
  commits receive new approval requests. Old concurrent runs are canceled.
- This is a behavioral demo, not a hardened production security boundary against
  a malicious repository writer. GitHub Actions workflow definitions themselves can
  be edited in PRs, and the required-check source is not pinned in the demo ruleset.
  Production needs protected/trusted workflow execution, expected check-source
  enforcement, permission review, and tested recovery/audit-retention policies.
- Self-review is deliberately allowed so the account owner can test their own PR.
  Cross-role separation is enforced by the final validator.
- If setup stops, the new repo remains available. The local `.demo-setup-state.json`
  records its URL. Inspect the error and configure that sandbox manually or choose a
  fresh repo name; the script never silently deletes anything.
- New accounts may need to complete GitHub's account-verification requirements
  before Actions runners are available. No billing change is made by setup.

## Local validation

```sh
python -m unittest discover -s tests -v
```

References: [Environment approvals](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments),
[review UX](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments),
[workflow review-history API](https://docs.github.com/en/rest/actions/workflow-runs#get-the-review-history-for-a-workflow-run),
and [required status checks](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-status-checks-to-pass-before-merging).
