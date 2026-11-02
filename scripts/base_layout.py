#!/usr/bin/env python3
"""Manage the non-secret unified Lark Base layout and per-host result table routing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse

from state_io import atomic_write_json

DEFAULT_LAYOUT = Path.home() / ".config" / "web-data-to-base" / "base_layout.local.json"
DEFAULT_BASE_NAME = "网页数据采集结果"
DEFAULT_ACCOUNT_TABLE = "账号密码本"
MAX_TABLE_NAME = 100


def normalized_host(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_http_url")
    host = parsed.hostname.casefold().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def site_key(url: str) -> str:
    host = normalized_host(url)
    return "host-" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:16]


def table_name_for_url(url: str) -> str:
    host = normalized_host(url)
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", host).strip("-._") or "网页"
    prefix = "数据｜"
    room = MAX_TABLE_NAME - len(prefix)
    if len(safe) > room:
        digest = hashlib.sha256(host.encode("utf-8")).hexdigest()[:8]
        safe = safe[: max(1, room - 9)] + "-" + digest
    return prefix + safe


def empty_layout() -> dict:
    return {
        "schema_version": 1,
        "base": {"name": DEFAULT_BASE_NAME, "url": "", "token": ""},
        "account_vault": {"table_name": DEFAULT_ACCOUNT_TABLE, "table_id": ""},
        "site_tables": {},
    }


def load(path: Path) -> dict:
    if not path.exists():
        return empty_layout()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("layout_must_be_object")
    result = empty_layout()
    result.update(data)
    result["base"] = {**empty_layout()["base"], **(data.get("base") or {})}
    result["account_vault"] = {**empty_layout()["account_vault"], **(data.get("account_vault") or {})}
    result["site_tables"] = data.get("site_tables") if isinstance(data.get("site_tables"), dict) else {}
    return result


def save(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def assert_no_sensitive(value) -> None:
    forbidden = {"password", "passwd", "密码", "验证码", "cookie", "token_value", "secret_value", "credential"}
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in forbidden:
                raise SystemExit("sensitive_value_forbidden")
            assert_no_sensitive(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_sensitive(item)


def read_object() -> dict:
    payload = json.loads(sys.stdin.read())
    if not isinstance(payload, dict):
        raise SystemExit("payload_must_be_object")
    assert_no_sensitive(payload)
    return payload


def public(data: dict) -> dict:
    return {
        "base": data["base"],
        "account_vault": data["account_vault"],
        "site_tables": list(data["site_tables"].values()),
        "site_table_count": len(data["site_tables"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["show", "site-route", "bind-base", "bind-account-table", "bind-site-table"])
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--url")
    args = parser.parse_args()
    path = args.layout.expanduser().resolve()
    data = load(path)

    if args.action == "show":
        print(json.dumps(public(data), ensure_ascii=False))
        return 0

    if args.action == "site-route":
        if not args.url:
            raise SystemExit("url_required")
        key = site_key(args.url)
        current = data["site_tables"].get(key, {})
        print(json.dumps({
            "site_key": key,
            "host": normalized_host(args.url),
            "table_name": current.get("table_name") or table_name_for_url(args.url),
            "table_id": current.get("table_id", ""),
            "base_name": data["base"]["name"],
            "base_url": data["base"].get("url", ""),
            "base_token": data["base"].get("token", ""),
            "needs_table_creation": not bool(current.get("table_id")),
        }, ensure_ascii=False))
        return 0

    payload = read_object()
    if args.action == "bind-base":
        name = str(payload.get("name") or DEFAULT_BASE_NAME).strip()
        url = str(payload.get("url") or "").strip()
        token = str(payload.get("base_token") or "").strip()
        if not url or not token:
            raise SystemExit("base_url_and_token_required")
        data["base"] = {"name": name, "url": url, "token": token}
    elif args.action == "bind-account-table":
        table_id = str(payload.get("table_id") or "").strip()
        if not table_id:
            raise SystemExit("table_id_required")
        data["account_vault"] = {
            "table_name": str(payload.get("table_name") or DEFAULT_ACCOUNT_TABLE).strip(),
            "table_id": table_id,
        }
    elif args.action == "bind-site-table":
        url = str(payload.get("url") or "").strip()
        table_id = str(payload.get("table_id") or "").strip()
        if not url or not table_id:
            raise SystemExit("url_and_table_id_required")
        key = site_key(url)
        data["site_tables"][key] = {
            "site_key": key,
            "host": normalized_host(url),
            "table_name": str(payload.get("table_name") or table_name_for_url(url)).strip(),
            "table_id": table_id,
        }
    save(path, data)
    print(json.dumps({"updated": True, **public(data)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
