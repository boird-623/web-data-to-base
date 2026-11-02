#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_sync
from update_account_catalog import normalize_rows


LOGIN_ROW = {
    "网站名称": "千牛",
    "登录网址": "https://login.example.com/",
    "登录方式": "account_password",
    "账号标识": "测试店铺A:子账号",
    "密钥引用ID": "web-account-example",
    "密码状态": "已安全保存",
    "是否启用": True,
    "执行顺序": 1,
}


class AccountSyncTests(unittest.TestCase):
    def test_normalize_select_and_public_target(self):
        public = {
            **LOGIN_ROW,
            "登录方式": "none",
            "账号标识": "ignored",
            "密码状态": ["无需密码"],
            "密钥引用ID": "web-public-example",
        }
        normalized = normalize_rows([public])
        self.assertEqual(normalized[0]["账号标识"], "无需登录")
        self.assertEqual(normalized[0]["密码状态"], "无需密码")

    def test_writable_never_contains_secrets(self):
        row = {**LOGIN_ROW, "password": "must-not-leak", "Cookie": "must-not-leak"}
        writable = account_sync._writable(row)
        self.assertNotIn("password", writable)
        self.assertNotIn("密码", writable)
        self.assertNotIn("Cookie", writable)
        self.assertEqual(writable["密码状态"], ["已安全保存"])

    def test_sync_updates_by_secret_ref_and_verifies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pending = root / "pending.json"
            catalog = root / "catalog.json"
            layout = root / "layout.json"
            layout.write_text(json.dumps({
                "base": {"name": "网页数据采集结果", "url": "https://example/base/test", "token": "base-token"},
                "account_vault": {"table_name": "账号密码本", "table_id": "tbl-test"},
            }), encoding="utf-8")
            pending.write_text(json.dumps({"账号列表": [LOGIN_ROW]}, ensure_ascii=False), encoding="utf-8")
            old_row = {**LOGIN_ROW, "账号标识": "测试店铺B:旧子账号", "record_id": "rec-1"}
            new_row = {**LOGIN_ROW, "record_id": "rec-1"}
            commands = []

            def fake_run(args, timeout=120):
                commands.append(args)
                if args[0] == "+field-list":
                    return {"ok": True, "data": {"fields": [
                        {"name": name, "type": field_type}
                        for name, field_type in account_sync.REQUIRED_FIELD_TYPES.items()
                    ]}}
                if args[0] == "+record-batch-update":
                    body = json.loads(args[args.index("--json") + 1])
                    self.assertEqual(body["update_records"]["rec-1"]["账号标识"], "测试店铺A:子账号")
                    self.assertFalse({"password", "密码", "Cookie", "Token", "验证码", "会话"} & set(body["update_records"]["rec-1"]))
                    return {"ok": True, "data": {"record_id_list": ["rec-1"]}}
                raise AssertionError(args)

            with patch.object(account_sync, "_run_json", side_effect=fake_run), patch.object(
                account_sync, "read_base_rows", side_effect=[[old_row], [new_row]]
            ):
                result = account_sync.sync_pending_to_base(pending, catalog, layout)

            self.assertTrue(result["synced"])
            self.assertEqual(result["updated"], 1)
            self.assertEqual(result["created"], 0)
            self.assertEqual(json.loads(pending.read_text(encoding="utf-8"))["账号列表"], [])
            catalog_data = json.loads(catalog.read_text(encoding="utf-8"))
            self.assertEqual(catalog_data["账号列表"][0]["账号标识"], "测试店铺A:子账号")
            self.assertTrue(any(command[0] == "+record-batch-update" for command in commands))

    def test_sync_rejects_readback_mismatch_and_keeps_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pending = root / "pending.json"
            catalog = root / "catalog.json"
            layout = root / "layout.json"
            layout.write_text(json.dumps({
                "base": {"name": "网页数据采集结果", "url": "https://example/base/test", "token": "base-token"},
                "account_vault": {"table_name": "账号密码本", "table_id": "tbl-test"},
            }), encoding="utf-8")
            pending.write_text(json.dumps({"账号列表": [LOGIN_ROW]}, ensure_ascii=False), encoding="utf-8")
            old_row = {**LOGIN_ROW, "账号标识": "测试店铺B:旧子账号", "record_id": "rec-1"}

            def fake_run(args, timeout=120):
                if args[0] == "+field-list":
                    return {"ok": True, "data": {"fields": [
                        {"name": name, "type": field_type}
                        for name, field_type in account_sync.REQUIRED_FIELD_TYPES.items()
                    ]}}
                if args[0] == "+record-batch-update":
                    return {"ok": True, "data": {"record_id_list": ["rec-1"]}}
                raise AssertionError(args)

            with patch.object(account_sync, "_run_json", side_effect=fake_run), patch.object(
                account_sync, "read_base_rows", side_effect=[[old_row], [old_row]]
            ):
                with self.assertRaisesRegex(account_sync.AccountSyncError, "base_readback_mismatch"):
                    account_sync.sync_pending_to_base(pending, catalog, layout)

            self.assertEqual(len(json.loads(pending.read_text(encoding="utf-8"))["账号列表"]), 1)
            self.assertFalse(catalog.exists())


if __name__ == "__main__":
    unittest.main()
