#!/usr/bin/env python3
"""Create or refresh the non-secret local account catalog from JSON on stdin."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from state_io import atomic_write_json

DEFAULT_CATALOG = Path.home() / ".config" / "web-data-to-base" / "account_catalog.local.json"
FIELDS = ("网站名称", "登录网址", "账号标识", "密钥引用ID", "是否启用", "执行顺序")
LOGIN_MODES = {"account_password", "none"}


def normalize_rows(rows: list[dict]) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("rows_must_be_array")
    normalized = []
    refs = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"row_{index}_must_be_object")
        missing = [key for key in FIELDS if key not in row or row[key] in (None, "")]
        if missing:
            raise ValueError(f"row_{index}_missing_{'_'.join(missing)}")
        ref = str(row["密钥引用ID"])
        if ref in refs:
            raise ValueError(f"row_{index}_duplicate_secret_ref")
        refs.add(ref)
        order = int(row["执行顺序"])
        if order < 1:
            raise ValueError(f"row_{index}_invalid_order")
        enabled = row["是否启用"]
        if not isinstance(enabled, bool):
            raise ValueError(f"row_{index}_enabled_must_be_boolean")
        login_mode = str(row.get("登录方式") or "account_password")
        if login_mode not in LOGIN_MODES:
            raise ValueError(f"row_{index}_invalid_login_mode")
        account_label = str(row["账号标识"])
        if login_mode == "account_password" and not account_label.strip():
            raise ValueError(f"row_{index}_account_label_required")
        if login_mode == "none":
            account_label = "无需登录"
        password_status = row.get("密码状态")
        if isinstance(password_status, list):
            password_status = password_status[0] if password_status else ""
        normalized.append({
            "网站名称": str(row["网站名称"]),
            "登录网址": str(row["登录网址"]),
            "账号标识": account_label,
            "登录方式": login_mode,
            "密钥引用ID": ref,
            "密码状态": str(password_status or ("无需密码" if login_mode == "none" else "缺失")),
            "是否启用": enabled,
            "执行顺序": order,
        })
    normalized.sort(key=lambda row: (row["执行顺序"], row["网站名称"], row["账号标识"]))
    return normalized


def write_catalog(normalized: list[dict], path: Path, base_name: str, base_url: str, table_name: str) -> None:
    data = {
        "填写说明": "由系统从统一飞书多维表格的账号密码本同步；真实密码只存在系统安全存储，本表不得增加密码列。",
        "账号清单多维表格": {"名称": base_name, "网址": base_url, "数据表": table_name},
        "账号列表": normalized,
    }
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--base-name", default="网页数据采集结果")
    parser.add_argument("--table-name", default="账号密码本")
    args = parser.parse_args()
    try:
        rows = json.loads(sys.stdin.read())
        normalized = normalize_rows(rows)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise SystemExit(str(exc))
    write_catalog(normalized, args.catalog, args.base_name, args.base_url, args.table_name)
    print(json.dumps({"updated": True, "catalog_rows": len(normalized), "enabled_accounts": sum(x["是否启用"] for x in normalized)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
