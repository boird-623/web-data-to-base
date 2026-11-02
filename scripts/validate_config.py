#!/usr/bin/env python3
"""Validate a non-secret account catalog and referenced OS credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from secure_credentials import backend_name, credential_exists

DEFAULT_CATALOG = Path.home() / ".config" / "web-data-to-base" / "account_catalog.local.json"
DEFAULT_LAYOUT = Path.home() / ".config" / "web-data-to-base" / "base_layout.local.json"
REQUIRED = ("网站名称", "登录网址", "账号标识", "密钥引用ID", "是否启用", "执行顺序")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    args = parser.parse_args()
    path = args.catalog.expanduser().resolve()
    if not path.exists():
        print(json.dumps({"valid": False, "errors": ["网页目标清单缓存不存在，请先完成配置并同步多维表格"]}, ensure_ascii=False))
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(json.dumps({"valid": False, "errors": [f"账号清单 JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列"]}, ensure_ascii=False))
        return 2
    rows = data.get("账号列表")
    errors: list[str] = []
    layout_path = args.layout.expanduser().resolve()
    layout_ready = False
    base_name = None
    account_table_name = None
    if not layout_path.exists():
        errors.append("统一 Base 布局不存在，请先创建或绑定‘网页数据采集结果’多维表格")
    else:
        try:
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            base = layout.get("base") if isinstance(layout, dict) else None
            vault = layout.get("account_vault") if isinstance(layout, dict) else None
            base_name = base.get("name") if isinstance(base, dict) else None
            account_table_name = vault.get("table_name") if isinstance(vault, dict) else None
            layout_ready = bool(
                isinstance(base, dict) and base.get("url") and base.get("token")
                and isinstance(vault, dict) and vault.get("table_id")
            )
            if not layout_ready:
                errors.append("统一 Base 尚未完成绑定：需要 Base URL/token 和账号密码本 table_id")
        except (json.JSONDecodeError, OSError, AttributeError):
            errors.append("统一 Base 布局文件无效")
    enabled = []
    enabled_login_targets = []
    if not isinstance(rows, list) or not rows:
        errors.append("网页目标清单至少需要一条记录")
    else:
        refs = set()
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                errors.append(f"第{i}条记录格式无效"); continue
            for field in REQUIRED:
                if field not in row or row[field] in (None, ""):
                    errors.append(f"第{i}条记录缺少{field}")
            ref = row.get("密钥引用ID")
            if ref in refs:
                errors.append(f"第{i}条记录的密钥引用ID重复")
            refs.add(ref)
            login_mode = str(row.get("登录方式") or "account_password")
            if login_mode not in {"account_password", "none"}:
                errors.append(f"第{i}条记录的登录方式无效")
            if login_mode == "account_password" and not str(row.get("账号标识") or "").strip():
                errors.append(f"第{i}条登录记录缺少账号标识")
            if row.get("是否启用") is True:
                enabled.append(row)
                if login_mode == "account_password":
                    enabled_login_targets.append(row)
    missing_refs = []
    backend_errors: list[str] = []
    if enabled_login_targets:
        try:
            backend = backend_name()
            backend_available = True
        except Exception as exc:
            backend = None
            backend_available = False
            backend_errors.append(type(exc).__name__)
    else:
        backend = "not_required"
        backend_available = True

    if enabled_login_targets and backend_available:
        for row in enabled_login_targets:
            ref = row.get("密钥引用ID")
            try:
                if not credential_exists(ref):
                    missing_refs.append(ref)
            except (KeyError, FileNotFoundError):
                missing_refs.append(ref)
            except Exception as exc:
                backend_available = False
                backend_errors.append(type(exc).__name__)
                break

    if missing_refs:
        errors.append(f"有{len(missing_refs)}个启用登录目标尚未保存到当前用户的系统安全存储")
    if not backend_available:
        errors.append("当前系统安全存储不可用或未解锁；请先恢复密钥库环境，不要重新填写或覆盖已有凭据")
    result = {
        "valid": not errors,
        "backend": backend,
        "credential_backend_available": backend_available,
        "base_layout_ready": layout_ready,
        "base_name": base_name,
        "account_table_name": account_table_name,
        "catalog_rows": len(rows) if isinstance(rows, list) else 0,
        "enabled_accounts": len(enabled),
        "enabled_login_targets": len(enabled_login_targets),
        "enabled_public_targets": len(enabled) - len(enabled_login_targets),
        "missing_secret_count": len(missing_refs),
        "backend_error_types": sorted(set(backend_errors)),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
