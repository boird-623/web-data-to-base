#!/usr/bin/env python3
"""Open a one-time localhost form and configure web targets.

Targets may use account/password login or require no login. Secrets go directly
to the OS secret backend. Non-secret target rows are staged locally, then
upserted into the bound Lark Base account table and verified before success is
reported.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import secrets
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from account_sync import AccountSyncError, layout_is_bound, refresh_catalog_from_base, sync_pending_to_base
from secure_credentials import backend_name, credential_exists, set_credential, validate_ref
from state_io import atomic_write_json

DEFAULT_PENDING = Path.home() / ".config" / "web-data-to-base" / "pending_accounts.local.json"
DEFAULT_CATALOG = Path.home() / ".config" / "web-data-to-base" / "account_catalog.local.json"
DEFAULT_LAYOUT = Path.home() / ".config" / "web-data-to-base" / "base_layout.local.json"
DEFAULT_TASKS = Path.home() / ".config" / "web-data-to-base" / "tasks.local.json"
DEFAULT_INSTANCE = Path.home() / ".config" / "web-data-to-base" / "configure_account.instance.json"
MAX_ACCOUNTS = 50
MAX_REQUEST_BYTES = 1024 * 1024


def page(token: str, existing_accounts: list[dict], message: str = "") -> bytes:
    safe_message = html.escape(message)
    existing_json = json.dumps(existing_accounts, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store">
<title>批量配置网页目标</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:980px;margin:32px auto;padding:0 20px;color:#1f2329;background:#f7f8fa}
h2{margin-bottom:8px}.note{background:#fff;padding:14px 16px;border-radius:10px;border:1px solid #dee0e3}.msg{color:#d14343}
.account{background:#fff;border:1px solid #dfe1e5;border-radius:12px;padding:18px;margin:16px 0;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.account-head{display:flex;align-items:center;justify-content:space-between}.account-title{font-weight:650}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 16px}
label{display:block;margin:10px 0 5px;font-size:14px}input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #bbbfc4;border-radius:6px}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}button{padding:10px 16px;border-radius:7px;border:1px solid #bbbfc4;background:#fff;cursor:pointer}
.primary{background:#1456f0;color:#fff;border-color:#1456f0}.danger{color:#d14343}.hint{font-size:13px;color:#646a73}@media(max-width:720px){.grid{grid-template-columns:1fr}}
</style></head><body>
<h2>批量配置网页</h2>
<p class="note">每条只需填写网页名和网页网址。需要登录时再填写账号名、密码；公开网页将账号名和密码同时留空即可。账号信息会新增或更新到“网页数据采集结果”的“账号密码本”表，真实密码只进入当前电脑的系统安全存储。</p>
<p class="msg">__MESSAGE__</p>
<form method="post" autocomplete="off" id="accounts-form">
<input type="hidden" name="token" value="__TOKEN__">
<div id="accounts"></div>
<div class="actions"><button type="button" id="add">＋ 添加网页</button><button type="submit" class="primary">全部安全保存</button><button type="button" id="close-page">关闭配置页</button></div>
<p class="hint">最多一次保存 50 个网页目标。提交顺序就是默认执行顺序，之后可在多维表格中启用、停用或调整顺序。</p>
</form>
<template id="account-template">
<section class="account">
  <div class="account-head"><span class="account-title">网页目标</span><button type="button" class="danger remove">删除</button></div>
  <div class="grid">
    <div><label>网页名</label><input name="site_name" required placeholder="例如：某电商平台"></div>
    <div><label>网页网址</label><input name="login_url" type="url" required placeholder="https://example.com/page"></div>
    <div class="credential-field"><label>账号名（公开网页可留空）</label><input name="username" autocomplete="username" placeholder="留空即无需登录"></div>
    <div class="credential-field"><label>密码（公开网页可留空）</label><input name="password" type="password" autocomplete="new-password" placeholder="留空即无需登录"></div>
    <div><label>网页状态</label><select name="enabled" style="width:100%;box-sizing:border-box;padding:10px;border:1px solid #bbbfc4;border-radius:6px"><option value="true">启用</option><option value="false">停用（不删除已保存凭据）</option></select></div>
  </div>
</section>
</template>
<script>
const container=document.getElementById('accounts');
const template=document.getElementById('account-template');
const form=document.getElementById('accounts-form');
const TOKEN=form.querySelector('[name=token]').value;
const EXISTING=__EXISTING_JSON__;
let submitting=false;
let closing=false;
function renumber(){[...container.children].forEach((row,i)=>{row.querySelector('.account-title').textContent='网页目标 '+(i+1);});}
function addAccount(copyFrom=null){if(container.children.length>=50){alert('一次最多配置 50 个网页');return;}const row=template.content.firstElementChild.cloneNode(true);if(copyFrom){row.querySelector('[name=site_name]').value=copyFrom.querySelector('[name=site_name]').value;row.querySelector('[name=login_url]').value=copyFrom.querySelector('[name=login_url]').value;}row.querySelector('.remove').onclick=()=>{if(container.children.length===1){alert('至少保留一个网页');return;}row.remove();renumber();};container.appendChild(row);renumber();}
function addExisting(data){addAccount();const row=container.lastElementChild;row.querySelector('[name=site_name]').value=data.site_name;row.querySelector('[name=login_url]').value=data.login_url;row.querySelector('[name=username]').value=data.login_mode==='none'?'':data.username;row.querySelector('[name=enabled]').value=data.enabled===false?'false':'true';const pw=row.querySelector('[name=password]');if(data.login_mode==='none'){pw.placeholder='留空即无需登录';}else if(data.secret_status==='available'){pw.placeholder='留空保持原密码';}else if(data.secret_status==='unavailable'){pw.placeholder='密钥库不可用，请先恢复后再保存';pw.readOnly=true;}else{pw.placeholder='本机密钥缺失，请重新填写密码';}}
document.getElementById('add').onclick=()=>addAccount(container.lastElementChild);
form.addEventListener('submit',()=>{submitting=true;});
function notifyClose(){if(submitting||closing)return;closing=true;navigator.sendBeacon('/'+TOKEN+'/close','');}
document.getElementById('close-page').onclick=()=>{notifyClose();document.body.innerHTML='<h2>配置页已关闭</h2><p>现在可以关闭此标签页。</p>';window.close();};
window.addEventListener('pagehide',notifyClose);
setInterval(()=>{if(!closing&&!submitting){fetch('/'+TOKEN+'/ping',{method:'POST',cache:'no-store',keepalive:true}).catch(()=>{});}},5000);
if(EXISTING.length){EXISTING.forEach(addExisting);}else{addAccount();}
</script></body></html>"""
    return template.replace("__TOKEN__", html.escape(token, quote=True)).replace("__EXISTING_JSON__", existing_json).replace("__MESSAGE__", safe_message).encode("utf-8")


def upsert_pending(path: Path, new_rows: list[dict], existing_accounts: list[dict] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"账号列表": []}
    rows = data.setdefault("账号列表", [])
    submitted_refs = {row["密钥引用ID"] for row in new_rows}
    for existing in existing_accounts or []:
        old_ref = str(existing.get("secret_ref") or "")
        if not old_ref or old_ref in submitted_refs:
            continue
        disabled_row = {
            "网站名称": existing["site_name"],
            "登录网址": existing["login_url"],
            "账号标识": existing["username"] if existing.get("login_mode") != "none" else "无需登录",
            "登录方式": existing.get("login_mode", "account_password"),
            "密钥引用ID": old_ref,
            "密码状态": "无需密码" if existing.get("login_mode") == "none" else ("已安全保存" if existing.get("secret_available") else "缺失"),
            "是否启用": False,
            "执行顺序": next((int(x.get("执行顺序", 999999)) for x in rows if x.get("密钥引用ID") == old_ref), 999999),
        }
        match = next((i for i, old in enumerate(rows) if old.get("密钥引用ID") == old_ref), None)
        if match is None:
            rows.append(disabled_row)
        else:
            rows[match] = disabled_row
    for row in new_rows:
        match = next((i for i, old in enumerate(rows) if old.get("密钥引用ID") == row["密钥引用ID"]), None)
        if match is None:
            rows.append(row)
        else:
            rows[match] = row
    atomic_write_json(path, data)


def existing_orders(*paths: Path) -> tuple[dict[str, int], int]:
    orders: dict[str, int] = {}
    max_order = 0
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for row in data.get("账号列表", []):
            if not isinstance(row, dict):
                continue
            ref = str(row.get("密钥引用ID") or "")
            try:
                order = int(row.get("执行顺序"))
            except (TypeError, ValueError):
                continue
            if ref and order > 0:
                orders[ref] = order
                max_order = max(max_order, order)
    return orders, max_order


def load_existing_accounts(*paths: Path) -> tuple[list[dict], set[str]]:
    """Load non-secret existing accounts from catalog / pending files (ref-deduped).

    Paths are applied in order; later pending data overrides the earlier synced
    catalog because it represents the newest unsynced user edits. Passwords are
    never read or returned.
    """
    seen: dict[str, dict] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for row in data.get("账号列表", []):
            if not isinstance(row, dict):
                continue
            ref = str(row.get("密钥引用ID") or "").strip()
            if not ref:
                continue
            entry = {
                "site_name": str(row.get("网站名称") or "").strip(),
                "login_url": str(row.get("登录网址") or "").strip(),
                "username": str(row.get("账号标识") or "").strip(),
                "secret_ref": ref,
                "login_mode": str(row.get("登录方式") or "account_password").strip(),
                "enabled": row.get("是否启用") is not False,
            }
            if not all(entry[k] for k in ("site_name", "login_url")):
                continue
            if entry["login_mode"] not in {"account_password", "none"}:
                entry["login_mode"] = "account_password"
            if entry["login_mode"] == "account_password" and not entry["username"]:
                continue
            # Later paths (pending) override earlier synced catalog values.
            seen[ref] = entry
    existing_refs = set(seen)
    return list(seen.values()), existing_refs


def mark_new_account_questions(tasks_path: Path, new_accounts: list[dict]) -> None:
    """Persist required post-configuration questions without storing secrets."""
    if not new_accounts:
        return
    if tasks_path.exists():
        try:
            data = json.loads(tasks_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise RuntimeError("tasks_state_invalid_cannot_record_new_account_questions")
    else:
        data = {"setup": {}, "任务列表": []}
    setup = data.setdefault("setup", {})
    pending = setup.setdefault("pending_new_account_questions", {})
    for account in new_accounts:
        pending[account["secret_ref"]] = {
            "网站名称": account["site_name"],
            "账号标识": account["username"] if account["login_mode"] == "account_password" else "无需登录",
            "登录方式": account["login_mode"],
            "是否需要新增或调整任务": None,
            "任务处理状态": "unanswered",
            "是否需要试运行": None,
        }
    setup["status"] = "account_decision_pending"
    atomic_write_json(tasks_path, data)


def make_target_ref(site_name: str, login_url: str, username: str, login_mode: str) -> str:
    """Build a stable non-secret reference for a login account or public target."""
    parsed = urllib.parse.urlparse(login_url.strip())
    host = (parsed.hostname or login_url).casefold()
    if login_mode == "account_password":
        identity_name = username.strip().casefold()
        identity = "\0".join((site_name.strip().casefold(), host, identity_name))
        prefix = "web-account-"
    else:
        public_url = urllib.parse.urlunparse((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path or "/", "", parsed.query, ""))
        identity = "\0".join((site_name.strip().casefold(), public_url))
        prefix = "web-public-"
    return validate_ref(prefix + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20])


def make_secret_ref(site_name: str, login_url: str, username: str) -> str:
    """Backward-compatible helper for account/password targets."""
    return make_target_ref(site_name, login_url, username, "account_password")


def parse_accounts(form: dict[str, list[str]], existing_secret_refs: set[str]) -> list[dict]:
    fields = ("site_name", "login_url", "username", "password", "enabled")
    values = {key: [value.strip() for value in form.get(key, [])] for key in fields}
    counts = {len(items) for items in values.values()}
    if len(counts) != 1 or not counts or next(iter(counts)) < 1:
        raise ValueError("网页配置字段数量不一致")
    count = next(iter(counts))
    if count > MAX_ACCOUNTS:
        raise ValueError("一次最多保存 50 个网页目标")
    accounts: list[dict] = []
    refs: set[str] = set()
    for index in range(count):
        item = {key: values[key][index] for key in fields}
        if not all(item[k] for k in ("site_name", "login_url")):
            raise ValueError(f"第 {index + 1} 个网页未填写完整")
        parsed_url = urllib.parse.urlparse(item["login_url"])
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"第 {index + 1} 个网页网址无效")
        login_mode = "account_password" if item["username"] else "none"
        item["login_mode"] = login_mode
        if login_mode == "none" and item["password"]:
            raise ValueError(f"第 {index + 1} 个网页填写了密码但没有账号名；请补充账号名，或同时留空以设为无需登录")
        secret_ref = make_target_ref(item["site_name"], item["login_url"], item["username"], item["login_mode"])
        if secret_ref in refs:
            raise ValueError("同一网页目标不能重复")
        refs.add(secret_ref)
        is_existing = secret_ref in existing_secret_refs
        if item["enabled"] not in {"true", "false"}:
            raise ValueError(f"第 {index + 1} 个网页目标状态无效")
        enabled = item["enabled"] == "true"
        if not item["password"] and enabled and item["login_mode"] == "account_password" and not is_existing:
            raise ValueError(f"第 {index + 1} 个启用登录账号为新账号或本机密钥缺失，必须填写密码")
        account_label = item["username"] if item["login_mode"] == "account_password" else "无需登录"
        accounts.append({
            **item,
            "enabled": enabled,
            "account_label": account_label,
            "secret_ref": secret_ref,
            "order": index + 1,
            "is_existing": is_existing,
        })
    return accounts


def active_instance(instance_path: Path) -> dict | None:
    """Return a verified live localhost configuration page, never trusting arbitrary URLs."""
    try:
        data = json.loads(instance_path.read_text(encoding="utf-8"))
        url = str(data.get("url") or "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            return None
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=1) as response:
            status = json.loads(response.read().decode("utf-8"))
        if status.get("status") == "waiting" and status.get("token") == data.get("token"):
            return data
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None
    return None


def clear_instance(instance_path: Path, token: str) -> None:
    """Remove only this server's registry entry, preserving a newer concurrent instance."""
    try:
        current = json.loads(instance_path.read_text(encoding="utf-8"))
        if current.get("token") == token:
            instance_path.unlink(missing_ok=True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--instance", type=Path, default=DEFAULT_INSTANCE)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--idle-timeout", type=int, default=90, help="Close after this many seconds without a page heartbeat")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    args = parser.parse_args()
    pending = args.pending.expanduser().resolve()
    catalog = args.catalog.expanduser().resolve()
    layout_path = args.layout.expanduser().resolve()
    tasks_path = args.tasks.expanduser().resolve()
    instance_path = args.instance.expanduser().resolve()
    current_instance = active_instance(instance_path)
    if current_instance:
        url = current_instance["url"]
        if not args.no_open:
            webbrowser.open(url)
        print(json.dumps({"status": "already_open", "config_url": url, "reused_existing_page": True}, ensure_ascii=False), flush=True)
        return 0
    instance_path.unlink(missing_ok=True)
    refresh_warning = ""
    if layout_is_bound(layout_path):
        try:
            refresh_catalog_from_base(layout_path, catalog)
        except AccountSyncError as exc:
            refresh_warning = f"无法从账号密码本刷新最新配置（{exc}）；请先检查多维表格访问权限或网络，修复后再保存。"
    existing, original_refs = load_existing_accounts(catalog, pending)
    existing_secret_refs: set[str] = set()
    try:
        secret_backend = backend_name()
        secret_backend_available = True
        backend_message = ""
    except Exception:
        secret_backend = None
        secret_backend_available = False
        backend_message = "当前系统密钥库不可用或未解锁；这只影响账号密码登录。无需登录网页仍可新增、修改和运行。请勿重新填写或覆盖已有密码。"
    for account in existing:
        if account["login_mode"] == "none":
            account["secret_available"] = False
            account["secret_status"] = "not_required"
            continue
        if not secret_backend_available:
            account["secret_available"] = None
            account["secret_status"] = "unavailable"
            continue
        try:
            available = credential_exists(account["secret_ref"])
            account["secret_status"] = "available" if available else "missing"
        except Exception:
            secret_backend_available = False
            backend_message = "当前系统密钥库不可用或未解锁；这只影响账号密码登录。无需登录网页仍可新增、修改和运行。请勿重新填写或覆盖已有密码。"
            available = None
            account["secret_status"] = "unavailable"
        account["secret_available"] = available
        if available is True:
            existing_secret_refs.add(account["secret_ref"])
    if refresh_warning:
        backend_message = " ".join(part for part in (refresh_warning, backend_message) if part)
    token = secrets.token_urlsafe(24)
    completed = threading.Event()
    result: dict = {}
    last_activity = {"at": time.monotonic(), "page_loaded": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def send_html_headers(self, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()

        def do_GET(self):
            if self.path == f"/{token}/status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "waiting", "token": token}, ensure_ascii=False).encode("utf-8"))
                return
            if self.path != f"/{token}":
                self.send_html_headers(404)
                return
            last_activity["at"] = time.monotonic()
            last_activity["page_loaded"] = True
            self.send_html_headers()
            self.wfile.write(page(token, existing, backend_message))

        def do_POST(self):
            if self.path in {f"/{token}/ping", f"/{token}/close"}:
                last_activity["at"] = time.monotonic()
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.path.endswith("/close"):
                    result.update({"saved": False, "reason": "page_closed"})
                    completed.set()
                return
            if self.path != f"/{token}":
                self.send_html_headers(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_REQUEST_BYTES:
                self.send_html_headers(413)
                return
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            if form.get("token", [""])[0].strip() != token:
                self.send_html_headers(403)
                return
            try:
                accounts = parse_accounts(form, original_refs if not secret_backend_available else existing_secret_refs)
                if not secret_backend_available and any(account["login_mode"] == "account_password" and account["password"] for account in accounts):
                    raise RuntimeError("secret_backend_unavailable_restore_before_saving")
                orders, max_order = existing_orders(catalog, pending)
                rows: list[dict] = []
                next_order = max_order + 1
                updated_secrets = 0
                for account in accounts:
                    if account["login_mode"] == "account_password" and account["password"]:
                        set_credential(account["secret_ref"], account["username"], account["password"])
                        existing_secret_refs.add(account["secret_ref"])
                        updated_secrets += 1
                    order = orders.get(account["secret_ref"])
                    if order is None:
                        order = next_order
                        next_order += 1
                    rows.append({
                        "网站名称": account["site_name"],
                        "登录网址": account["login_url"],
                        "账号标识": account["account_label"],
                        "登录方式": account["login_mode"],
                        "密钥引用ID": account["secret_ref"],
                        "密码状态": "无需密码" if account["login_mode"] == "none" else ("密钥库不可用" if not secret_backend_available else ("已安全保存" if (account["password"] or account["secret_ref"] in existing_secret_refs) else "缺失")),
                        "是否启用": account["enabled"],
                        "执行顺序": order,
                    })
                upsert_pending(pending, rows, existing)
                if layout_is_bound(layout_path):
                    sync_result = sync_pending_to_base(pending, catalog, layout_path)
                else:
                    # 当前阶段用户不建 Base：密码已入系统密钥库、配置已写本地，跳过同步。
                    sync_result = {"synced": False, "reason": "base_not_bound_local_only"}
                new_refs = sorted({row["密钥引用ID"] for row in rows} - original_refs)
                new_accounts = [account for account in accounts if account["secret_ref"] in new_refs]
                mark_new_account_questions(tasks_path, new_accounts)
                result.update({
                    "saved": True,
                    "saved_count": len(rows),
                    "updated_secrets": updated_secrets,
                    "new_account_count": len(new_refs),
                    "new_account_refs": new_refs,
                    "requires_task_confirmation": bool(new_refs),
                    "requires_trial_confirmation": bool(new_refs),
                    "backend": secret_backend if secret_backend_available else "not_required_for_public_targets",
                    "pending_path": str(pending),
                    "base_sync": sync_result,
                })
                self.send_html_headers()
                success_msg = f"已保存 {len(rows)} 个网页配置"
                if updated_secrets:
                    success_msg += f"，其中 {updated_secrets} 个更新了密码"
                if layout_is_bound(layout_path):
                    success_msg += "，已同步并回读验证账号密码本，可以关闭此页面并返回豆包。"
                else:
                    success_msg += "，已保存到本机系统密钥库与本地配置（当前未绑定多维表格），可以关闭此页面并返回豆包。"
                self.wfile.write(f"<meta charset='utf-8'><h2>保存成功</h2><p>{success_msg}</p>".encode("utf-8"))
                completed.set()
            except Exception as exc:
                error_text = html.escape(str(exc))
                self.send_html_headers(400)
                self.wfile.write(page(token, existing, f"保存失败：{error_text}。本地待同步内容已保留；请修复多维表格权限或网络后重试。"))

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    url = f"http://127.0.0.1:{server.server_port}/{token}"
    atomic_write_json(instance_path, {"url": url, "token": token, "pid": os.getpid(), "created_at": int(time.time())})
    print(json.dumps({"status": "waiting", "config_url": url, "reused_existing_page": False, "supports_multiple_targets": True, "supports_public_targets": True, "max_targets": MAX_ACCOUNTS}, ensure_ascii=False), flush=True)
    if not args.no_open:
        webbrowser.open(url)
    deadline = time.monotonic() + max(1, args.timeout)
    try:
        while not completed.is_set() and time.monotonic() < deadline:
            server.handle_request()
            if last_activity["page_loaded"] and time.monotonic() - last_activity["at"] > max(15, args.idle_timeout):
                result.update({"saved": False, "reason": "page_closed_or_inactive"})
                completed.set()
    finally:
        server.server_close()
        clear_instance(instance_path, token)
    if not completed.is_set():
        print(json.dumps({"saved": False, "reason": "timeout"}, ensure_ascii=False))
        return 2
    if result.get("reason") == "page_closed":
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
