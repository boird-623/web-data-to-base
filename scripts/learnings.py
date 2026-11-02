#!/usr/bin/env python3
"""Append-only learning log for task requirements, optimizations and verified fixes.

This is non-secret, per-machine experience. It records *what was improved and why*
so future runs get easier. Secrets (password/cookie/token) are never stored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from state_io import atomic_write_json, load_json

DEFAULT_LEARNINGS = Path.home() / ".config" / "web-data-to-base" / "learnings.local.json"
CATEGORIES = {
    "操作优化", "业务规则", "口径字段", "异常解法", "范围", "定时", "账号", "其他",
}
FORBIDDEN_KEY_PARTS = (
    "password", "passwd", "pwd", "cookie", "token", "authorization",
    "secret", "验证码", "密码", "口令", "令牌",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def assert_safe(value, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if any(part in str(key).casefold() for part in FORBIDDEN_KEY_PARTS):
                raise SystemExit(f"sensitive_field_forbidden:{path}.{key}")
            assert_safe(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_safe(child, f"{path}[{index}]")
    elif isinstance(value, str) and len(value) > 4000:
        raise SystemExit(f"oversized_value_forbidden:{path}")


def load(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "learnings": []}
    data = load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("learnings"), list):
        raise SystemExit("invalid_learnings_file")
    data.setdefault("schema_version", 1)
    return data


def make_id(platform: str, task: str, finding: str) -> str:
    basis = f"{platform.casefold()}\0{task.casefold()}\0{finding.casefold()}\0{now()}"
    return "lrn-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def add(path: Path) -> dict:
    payload = json.loads(sys.stdin.read())
    if not isinstance(payload, dict):
        raise SystemExit("payload_must_be_object")
    assert_safe(payload)
    category = str(payload.get("category", "其他")).strip()
    if category not in CATEGORIES:
        raise SystemExit(f"invalid_category:{category}")
    platform = str(payload.get("platform", "")).strip()
    task = str(payload.get("task", "")).strip()
    finding = str(payload.get("finding", "")).strip()
    improvement = str(payload.get("improvement", "")).strip()
    if not improvement:
        raise SystemExit("improvement_required")
    entry = {
        "id": make_id(platform, task, finding),
        "recorded_at": now(),
        "platform": platform,
        "task": task,
        "category": category,
        "finding": finding,
        "improvement": improvement,
        "verified": bool(payload.get("verified", False)),
        "source": str(payload.get("source", "")).strip(),
        "promoted_to_preset": False,
        "notes": str(payload.get("notes", "")).strip(),
    }
    data = load(path)
    data["learnings"].append(entry)
    atomic_write_json(path, data)
    return entry


def listing(path: Path, platform: str, category: str, limit: int) -> list:
    data = load(path)
    items = data["learnings"]
    if platform:
        items = [x for x in items if x.get("platform") == platform]
    if category:
        items = [x for x in items if x.get("category") == category]
    return list(reversed(items))[: max(1, limit)]


def mark_promoted(path: Path, learning_id: str) -> dict:
    data = load(path)
    entry = next((x for x in data["learnings"] if x.get("id") == learning_id), None)
    if entry is None:
        raise SystemExit("learning_not_found")
    entry["promoted_to_preset"] = True
    entry["promoted_at"] = now()
    atomic_write_json(path, data)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["add", "list", "mark-promoted"])
    parser.add_argument("--learnings", type=Path, default=DEFAULT_LEARNINGS)
    parser.add_argument("--platform", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--id", default="")
    args = parser.parse_args()
    path = args.learnings.expanduser().resolve()

    if args.action == "add":
        print(json.dumps(add(path), ensure_ascii=False))
    elif args.action == "list":
        items = listing(path, args.platform.strip(), args.category.strip(), args.limit)
        print(json.dumps({"learnings": items, "count": len(items)}, ensure_ascii=False))
    else:
        if not args.id:
            raise SystemExit("id_required")
        print(json.dumps(mark_promoted(path, args.id), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
