#!/usr/bin/env python3
"""Synchronize non-secret web target metadata with the bound Lark Base.

The Base account table is the shared source of truth. Passwords and other
credentials are never read, written, serialized, or passed to lark-cli here.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from state_io import atomic_write_json, load_json
from update_account_catalog import normalize_rows, write_catalog

DEFAULT_PENDING = Path.home() / ".config" / "web-data-to-base" / "pending_accounts.local.json"
DEFAULT_CATALOG = Path.home() / ".config" / "web-data-to-base" / "account_catalog.local.json"
DEFAULT_LAYOUT = Path.home() / ".config" / "web-data-to-base" / "base_layout.local.json"
WRITABLE_FIELDS = (
    "网站名称",
    "登录网址",
    "登录方式",
    "账号标识",
    "密钥引用ID",
    "密码状态",
    "是否启用",
    "执行顺序",
)
REQUIRED_FIELD_TYPES = {
    "网站名称": "text",
    "登录网址": "text",
    "登录方式": "text",
    "账号标识": "text",
    "密钥引用ID": "text",
    "密码状态": "select",
    "是否启用": "checkbox",
    "执行顺序": "number",
}


class AccountSyncError(RuntimeError):
    pass


def _run_json(arguments: list[str], timeout: int = 120) -> dict[str, Any]:
    command = ["lark-cli", "base", *arguments, "--as", "user"]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise AccountSyncError("lark_cli_not_found") from exc
    except subprocess.TimeoutExpired as exc:
        raise AccountSyncError("lark_cli_timeout") from exc
    raw = completed.stdout.strip()
    if completed.returncode != 0:
        detail = (completed.stderr or raw or "unknown_error").strip()
        raise AccountSyncError(f"lark_cli_failed:{detail[:500]}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AccountSyncError("lark_cli_returned_invalid_json") from exc
    if not payload.get("ok", False):
        raise AccountSyncError(f"lark_cli_operation_failed:{json.dumps(payload, ensure_ascii=False)[:500]}")
    return payload


def load_layout(layout_path: Path) -> dict[str, str]:
    try:
        layout = load_json(layout_path)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise AccountSyncError("base_layout_unavailable") from exc
    base = layout.get("base") or {}
    vault = layout.get("account_vault") or {}
    values = {
        "base_name": str(base.get("name") or "网页数据采集结果"),
        "base_url": str(base.get("url") or "").strip(),
        "base_token": str(base.get("token") or "").strip(),
        "table_name": str(vault.get("table_name") or "账号密码本").strip(),
        "table_id": str(vault.get("table_id") or "").strip(),
    }
    if not values["base_url"] or not values["base_token"] or not values["table_id"]:
        raise AccountSyncError("base_layout_not_bound")
    return values


def layout_is_bound(layout_path: Path) -> bool:
    try:
        load_layout(layout_path)
        return True
    except AccountSyncError:
        return False


def _plain_url(value: Any) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"\[(.*?)\]\((https?://.*?)\)", text)
    return match.group(2) if match else text


def _select_text(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def validate_account_table_schema(layout: dict[str, str]) -> None:
    response = _run_json([
        "+field-list",
        "--base-token", layout["base_token"],
        "--table-id", layout["table_id"],
        "--format", "json",
    ])
    fields = response.get("data", {}).get("fields", [])
    actual = {str(field.get("name")): str(field.get("type")) for field in fields if isinstance(field, dict)}
    missing = [name for name in REQUIRED_FIELD_TYPES if name not in actual]
    if missing:
        raise AccountSyncError(f"account_table_missing_fields:{','.join(missing)}")
    mismatched = [name for name, expected in REQUIRED_FIELD_TYPES.items() if actual.get(name) != expected]
    if mismatched:
        raise AccountSyncError(f"account_table_field_type_mismatch:{','.join(mismatched)}")
    forbidden = {"密码", "验证码", "Cookie", "Token", "会话", "密钥内容"} & set(actual)
    if forbidden:
        raise AccountSyncError(f"account_table_contains_forbidden_fields:{','.join(sorted(forbidden))}")


def read_base_rows(layout: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = _run_json([
            "+record-list",
            "--base-token", layout["base_token"],
            "--table-id", layout["table_id"],
            "--format", "json",
            "--limit", "200",
            "--offset", str(offset),
        ])
        data = response.get("data", {})
        fields = data.get("fields", [])
        values = data.get("data", [])
        record_ids = data.get("record_id_list", [])
        if not (isinstance(fields, list) and isinstance(values, list) and isinstance(record_ids, list)):
            raise AccountSyncError("account_table_record_list_shape_invalid")
        if len(values) != len(record_ids):
            raise AccountSyncError("account_table_record_id_count_mismatch")
        for record_id, row_values in zip(record_ids, values):
            if not isinstance(row_values, list):
                raise AccountSyncError("account_table_row_shape_invalid")
            projected = dict(zip(fields, row_values))
            projected["登录网址"] = _plain_url(projected.get("登录网址"))
            projected["密码状态"] = _select_text(projected.get("密码状态"))
            projected["record_id"] = str(record_id)
            rows.append(projected)
        if not data.get("has_more"):
            break
        if not values:
            raise AccountSyncError("account_table_pagination_stalled")
        offset += len(values)
    return rows


def _normalized_pending_rows(pending_path: Path) -> list[dict[str, Any]]:
    try:
        data = load_json(pending_path)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise AccountSyncError("pending_accounts_invalid") from exc
    raw_rows = data.get("账号列表", [])
    if not isinstance(raw_rows, list):
        raise AccountSyncError("pending_accounts_rows_must_be_array")
    try:
        return normalize_rows(raw_rows)
    except (TypeError, ValueError) as exc:
        raise AccountSyncError(f"pending_accounts_validation_failed:{exc}") from exc


def _writable(row: dict[str, Any]) -> dict[str, Any]:
    output = {field: row[field] for field in WRITABLE_FIELDS}
    output["密码状态"] = [str(row["密码状态"])]
    return output


def _canonical(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "网站名称": str(row.get("网站名称") or ""),
        "登录网址": _plain_url(row.get("登录网址")),
        "登录方式": str(row.get("登录方式") or "account_password"),
        "账号标识": str(row.get("账号标识") or ""),
        "密钥引用ID": str(row.get("密钥引用ID") or ""),
        "密码状态": _select_text(row.get("密码状态")),
        "是否启用": bool(row.get("是否启用")),
        "执行顺序": int(row.get("执行顺序") or 0),
    }


def refresh_catalog_from_base(layout_path: Path, catalog_path: Path) -> dict[str, Any]:
    layout = load_layout(layout_path)
    validate_account_table_schema(layout)
    rows = read_base_rows(layout)
    normalized = normalize_rows(rows)
    write_catalog(normalized, catalog_path, layout["base_name"], layout["base_url"], layout["table_name"])
    return {"refreshed": True, "catalog_rows": len(normalized), "base_url": layout["base_url"]}


def sync_pending_to_base(pending_path: Path, catalog_path: Path, layout_path: Path) -> dict[str, Any]:
    layout = load_layout(layout_path)
    validate_account_table_schema(layout)
    pending_rows = _normalized_pending_rows(pending_path)
    before = read_base_rows(layout)
    by_ref: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for row in before:
        ref = str(row.get("密钥引用ID") or "").strip()
        if not ref:
            continue
        if ref in by_ref:
            duplicates.add(ref)
        by_ref[ref] = row
    if duplicates:
        raise AccountSyncError(f"duplicate_secret_refs_in_base:{','.join(sorted(duplicates))}")

    updates: dict[str, dict[str, Any]] = {}
    creates: list[dict[str, Any]] = []
    for row in pending_rows:
        existing = by_ref.get(row["密钥引用ID"])
        if existing:
            updates[existing["record_id"]] = _writable(row)
        else:
            creates.append(_writable(row))

    if updates:
        _run_json([
            "+record-batch-update",
            "--base-token", layout["base_token"],
            "--table-id", layout["table_id"],
            "--json", json.dumps({"update_records": updates}, ensure_ascii=False, separators=(",", ":")),
            "--format", "json",
        ])
    if creates:
        _run_json([
            "+record-batch-create",
            "--base-token", layout["base_token"],
            "--table-id", layout["table_id"],
            "--json", json.dumps({"create_records": creates}, ensure_ascii=False, separators=(",", ":")),
            "--format", "json",
        ])

    verified_rows = read_base_rows(layout)
    verified_by_ref: dict[str, dict[str, Any]] = {}
    for row in verified_rows:
        ref = str(row.get("密钥引用ID") or "").strip()
        if ref in verified_by_ref:
            raise AccountSyncError(f"duplicate_secret_ref_after_write:{ref}")
        if ref:
            verified_by_ref[ref] = row
    mismatches: list[str] = []
    for expected in pending_rows:
        actual = verified_by_ref.get(expected["密钥引用ID"])
        if actual is None or _canonical(actual) != _canonical(expected):
            mismatches.append(expected["密钥引用ID"])
    if mismatches:
        raise AccountSyncError(f"base_readback_mismatch:{','.join(mismatches)}")

    normalized_verified = normalize_rows(verified_rows)
    write_catalog(
        normalized_verified,
        catalog_path,
        layout["base_name"],
        layout["base_url"],
        layout["table_name"],
    )
    atomic_write_json(pending_path, {
        "账号列表": [],
        "同步状态": {
            "状态": "已同步",
            "同步时间": datetime.now(timezone.utc).isoformat(),
            "已验证记录数": len(pending_rows),
        },
    })
    return {
        "synced": True,
        "updated": len(updates),
        "created": len(creates),
        "verified": len(pending_rows),
        "catalog_rows": len(normalized_verified),
        "base_url": layout["base_url"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("refresh", "sync"))
    parser.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    args = parser.parse_args()
    try:
        if args.action == "refresh":
            result = refresh_catalog_from_base(args.layout.expanduser().resolve(), args.catalog.expanduser().resolve())
        else:
            result = sync_pending_to_base(
                args.pending.expanduser().resolve(),
                args.catalog.expanduser().resolve(),
                args.layout.expanduser().resolve(),
            )
    except AccountSyncError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
