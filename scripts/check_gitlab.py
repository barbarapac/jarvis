"""Smoke test: valida o GITLAB_TOKEN e lista os todos pendentes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def main() -> int:
    token = os.environ.get("GITLAB_TOKEN", "")
    base_url = os.environ.get("GITLAB_URL", "https://gitlab.com")
    if not token:
        print("GITLAB_TOKEN ausente no .env", file=sys.stderr)
        return 1

    headers = {"PRIVATE-TOKEN": token}

    r = httpx.get(f"{base_url}/api/v4/user", headers=headers, timeout=10)
    if r.status_code != 200:
        print(f"falha em /user: {r.status_code} {r.text}", file=sys.stderr)
        return 1
    u = r.json()
    print(f"auth ok: id={u['id']} username={u['username']} name={u['name']}")

    r = httpx.get(
        f"{base_url}/api/v4/todos",
        headers=headers,
        params={"state": "pending", "per_page": 50},
        timeout=10,
    )
    r.raise_for_status()
    todos = r.json()
    print(f"todos pendentes: {len(todos)}")
    for t in todos[:5]:
        target = t.get("target") or {}
        title = (target.get("title") or target.get("name") or "?")[:70]
        print(
            f"  - [{t['action_name']}] {t['target_type']}: {title} "
            f"(by {t['author']['name']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
