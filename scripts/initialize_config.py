#!/usr/bin/env python3
"""Initialize non-secret account catalog cache and task state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
from pathlib import Path

from state_io import atomic_write_json

DEFAULT_DIR = Path.home() / ".config" / "web-data-to-base"
DEFAULT_CATALOG = DEFAULT_DIR / "account_catalog.local.json"
DEFAULT_TASKS = DEFAULT_DIR / "tasks.local.json"
DEFAULT_PROFILES = DEFAULT_DIR / "operation_profiles.local.json"
DEFAULT_LAYOUT = DEFAULT_DIR / "base_layout.local.json"
ROOT = Path(__file__).resolve().parent.parent
CATALOG_TEMPLATE = ROOT / "config" / "account_catalog.example.json"
TASKS_TEMPLATE = ROOT / "config" / "tasks.example.json"
LAYOUT_TEMPLATE = ROOT / "config" / "base_layout.example.json"


def create_if_missing(template: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return False
    shutil.copyfile(template, target)
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    args = parser.parse_args()
    catalog = args.catalog.expanduser().resolve()
    tasks = args.tasks.expanduser().resolve()
    profiles = args.profiles.expanduser().resolve()
    layout = args.layout.expanduser().resolve()
    profiles.parent.mkdir(parents=True, exist_ok=True)
    profiles_created = False
    if not profiles.exists():
        atomic_write_json(profiles, {"schema_version": 1, "profiles": {}})
        profiles_created = True
    result = {
        "catalog_created": create_if_missing(CATALOG_TEMPLATE, catalog),
        "tasks_created": create_if_missing(TASKS_TEMPLATE, tasks),
        "layout_created": create_if_missing(LAYOUT_TEMPLATE, layout),
        "profiles_created": profiles_created,
        "catalog_path": str(catalog),
        "tasks_path": str(tasks),
        "layout_path": str(layout),
        "profiles_path": str(profiles),
    }
    # 内置电商四平台目标与任务（幂等，已存在则只补齐缺失项）。失败不阻断基础初始化。
    try:
        import seed_ecommerce  # noqa: WPS433
        seed_ecommerce.seed_catalog()
        seed_result = seed_ecommerce.upsert_tasks()
        result["ecommerce_seed"] = {
            "tasks_created": seed_result.get("created", []),
            "tasks_updated": seed_result.get("updated", []),
            "tasks_failed": seed_result.get("failed", []),
        }
    except Exception as exc:  # noqa: BLE001
        result["ecommerce_seed"] = {"error": type(exc).__name__, "detail": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
