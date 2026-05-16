"""
GitHub Actions workflow dispatch helper.
Triggers workflow_dispatch events via the GitHub REST API.
"""
import os
import requests


def trigger_workflow(workflow_file: str, inputs: dict) -> bool:
    """
    Dispatch a workflow_dispatch event on the default branch.
    All input values are coerced to strings (GitHub API requirement).
    Returns True on success (HTTP 204), False otherwise.
    """
    token = os.environ.get("GITHUB_ACTIONS_TOKEN", "")
    repo  = os.environ.get("GITHUB_REPO", "willfriel/content-distributor")
    if not token:
        print(f"[github] GITHUB_ACTIONS_TOKEN not set — cannot dispatch {workflow_file}")
        return False

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    try:
        r = requests.post(
            url,
            headers={
                "Authorization":        f"Bearer {token}",
                "Accept":               "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "ref":    "master",
                "inputs": {k: str(v) for k, v in inputs.items()},
            },
            timeout=15,
        )
        if r.status_code == 204:
            print(f"[github] ✅ Dispatched {workflow_file} with {list(inputs.keys())}")
            return True
        print(f"[github] Dispatch failed {r.status_code}: {r.text[:300]}")
        return False
    except Exception as e:
        print(f"[github] Dispatch error: {e}")
        return False
