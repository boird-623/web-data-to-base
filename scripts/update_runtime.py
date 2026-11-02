#!/usr/bin/env python3
"""Update non-secret setup/runtime state in tasks.local.json."""

from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from state_io import atomic_write_json, load_json

DEFAULT_TASKS = Path.home() / ".config" / "web-data-to-base" / "tasks.local.json"
ALLOWED_STATUSES = {"requirements_pending", "trial_pending", "account_decision_pending", "ready", "needs_reconfiguration"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--task-name")
    parser.add_argument("--setup-status", choices=sorted(ALLOWED_STATUSES))
    parser.add_argument("--profile-json", help="Non-secret runtime profile JSON object")
    parser.add_argument("--trial-passed", choices=["true", "false"])
    args = parser.parse_args()

    path = args.tasks.expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))

    if args.trial_passed is not None:
        raise SystemExit("task_level_trial_status_deprecated_use_batch_state_account_decisions")

    if args.setup_status == "ready":
        raise SystemExit("ready_status_is_derived_use_batch_state_account_decisions")

    if args.setup_status:
        data.setdefault("setup", {})["status"] = args.setup_status

    if args.task_name:
        task = next((item for item in data.get("任务列表", []) if item.get("任务名称") == args.task_name), None)
        if task is None:
            raise SystemExit("task_not_found")
        runtime = task.setdefault("runtime", {})
        if args.profile_json:
            profile = json.loads(args.profile_json)
            if not isinstance(profile, dict):
                raise SystemExit("profile_json_must_be_object")
            runtime["site_profile"] = profile
            runtime["configured"] = True

    atomic_write_json(path, data)
    print(json.dumps({"updated": True, "tasks_path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
