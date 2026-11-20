"""Drive the running API through N seeded disputes, approving any that pause for a human.

    uv run uvicorn disputedesk.api.app:app --factory      # in another terminal
    uv run python -m disputedesk.db.seed --reset          # fresh cases and checkpoints
    uv run python scripts/run_disputes.py --count 5

Walks case ids upward from --first, skipping any case that is not open.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

TERMINAL = {"completed", "failed"}


def wait_for(client: httpx.Client, case_id: str, states: set[str]) -> dict[str, object]:
    for _ in range(100):
        body: dict[str, object] = client.get(f"/disputes/{case_id}").json()
        if body["workflow"] in states:
            return body
        time.sleep(0.2)
    raise TimeoutError(f"{case_id} did not reach {states}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--first", type=int, default=1, help="first case number")
    args = parser.parse_args()

    failures = done = 0
    with httpx.Client(base_url=args.url, timeout=30) as client:
        n = args.first
        while done < args.count:
            case_id = f"case_{n:04d}"
            n += 1
            resp = client.post("/disputes", json={"case_id": case_id})
            if resp.status_code == 409:  # not open (the seed includes cases already past `open`)
                print(f"{case_id}: skipped ({resp.json()['detail']})")
                continue
            resp.raise_for_status()
            done += 1
            body = wait_for(client, case_id, {"awaiting_approval", *TERMINAL})
            paused = body["workflow"] == "awaiting_approval"
            if paused:
                client.post(
                    f"/disputes/{case_id}/approve",
                    json={"approved": True, "approver": "demo-approver", "note": "demo"},
                ).raise_for_status()
                body = wait_for(client, case_id, TERMINAL)
            failures += body["workflow"] != "completed"
            route = body.get("route") or {}
            risk = route.get("risk_tier", "?")  # type: ignore[attr-defined]
            note = "(paused, approved) " if paused else ""
            path = ">".join(body["path"])  # type: ignore[arg-type]
            print(f"{case_id}: {body['case_status']:<18} risk={risk:<6} {note}path={path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
