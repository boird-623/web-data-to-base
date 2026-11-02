#!/usr/bin/env python3
"""Persist a cross-site, cross-task execution plan with a resumable cursor."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from base_layout import DEFAULT_LAYOUT, load as load_layout, table_name_for_url
from state_io import atomic_write_json, load_json

DEFAULT_CATALOG = Path.home() / ".config" / "web-data-to-base" / "account_catalog.local.json"
DEFAULT_TASKS = Path.home() / ".config" / "web-data-to-base" / "tasks.local.json"
MODES = {"trial", "run"}
STAGES = {
    "pending", "login", "navigate", "filter", "download", "locate_download", "parse_file",
    "collect", "write", "verify", "logout", "done"
}
ACTIVE_STATUSES = {"running", "blocked", "awaiting_confirmation"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path) -> dict:
    return load_json(path)


def save(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def plan_items(plan: dict) -> list[dict]:
    # Read legacy batches without destroying their resumable state.
    items = plan.get("items")
    if isinstance(items, list):
        return items
    accounts = plan.get("accounts")
    return accounts if isinstance(accounts, list) else []


def public_state(plan: dict) -> dict:
    items = plan_items(plan)
    current = plan.get("current_position")
    item = items[current] if isinstance(current, int) and 0 <= current < len(items) else None
    target_refs = {str(x.get("secret_ref", "")) for x in items if x.get("secret_ref")}
    completed_target_count = sum(
        1 for ref in target_refs
        if all(x.get("status") == "completed" for x in items if x.get("secret_ref") == ref)
    )
    return {
        "exists": True,
        "plan_type": plan.get("plan_type", "legacy_account_batch"),
        "mode": plan.get("mode"),
        "task_name": plan.get("task_name"),
        "task_names": plan.get("task_names", []),
        "status": plan.get("status"),
        "total_items": len(items),
        "completed_items": sum(x.get("status") == "completed" for x in items),
        "remaining_items": sum(x.get("status") in {"pending", "in_progress", "blocked"} for x in items),
        "total_targets": len(target_refs),
        "completed_targets": completed_target_count,
        "total_accounts": len(target_refs),
        "completed_accounts": completed_target_count,
        "current": None if item is None else {
            "site_name": item.get("site_name"),
            "account_label": item.get("account_label"),
            "secret_ref": item.get("secret_ref"),
            "login_url": item.get("login_url"),
            "login_mode": item.get("login_mode", "account_password"),
            "task_name": item.get("task_name"),
            "task": item.get("task"),
            "status": item.get("status"),
            "stage": item.get("stage"),
            "login_required": item.get("login_required", True),
            "logout_required": item.get("logout_required", True),
        },
    }


def get_plan(data: dict) -> dict:
    plan = data.get("execution_plan")
    if isinstance(plan, dict):
        return plan
    legacy = data.get("batch")
    if isinstance(legacy, dict):
        return legacy
    raise SystemExit("execution_plan_not_initialized")


def get_current(plan: dict) -> dict:
    pos = plan.get("current_position")
    items = plan_items(plan)
    if not isinstance(pos, int) or pos < 0 or pos >= len(items):
        raise SystemExit("no_current_plan_item")
    return items[pos]


def normalize_targets(value) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def task_matches_account(task: dict, account: dict) -> bool:
    targets = normalize_targets(task.get("网页"))
    if not targets:
        return False
    site_name = str(account.get("网站名称", "")).casefold()
    login_url = str(account.get("登录网址", ""))
    host = (urlparse(login_url).hostname or "").casefold()
    secret_ref = str(account.get("密钥引用ID", ""))
    for target in targets:
        candidate = target.casefold()
        target_host = (urlparse(target).hostname or "").casefold()
        if candidate in {"*", "全部", "所有网页", "all"}:
            return True
        if candidate == site_name or candidate == host or target_host == host or target == secret_ref:
            return True
        if candidate and (candidate in site_name or candidate in host):
            return True
    return False


def selected_tasks(data: dict, mode: str, only_task: str | None) -> list[dict]:
    tasks = [x for x in data.get("任务列表", []) if isinstance(x, dict) and x.get("是否启用", True) is not False]
    if only_task:
        tasks = [x for x in tasks if x.get("任务名称") == only_task]
    tasks.sort(key=lambda x: (int(x.get("执行顺序", 999999)), str(x.get("任务名称", ""))))
    return tasks


def verified_account_refs(data: dict) -> set[str]:
    """Return account-level trial approvals, including legacy per-task records."""
    setup = data.setdefault("setup", {})
    refs = {str(x) for x in setup.get("trial_verified_accounts", []) if str(x)}
    refs.update(str(x) for x in setup.get("trial_skipped_accounts", []) if str(x))
    for task in data.get("任务列表", []):
        runtime = task.get("runtime", {}) if isinstance(task, dict) else {}
        refs.update(str(x) for x in runtime.get("trial_verified_accounts", []) if str(x))
    return refs


def recalculate_trial_status(data: dict, catalog: dict) -> bool:
    """Recompute setup readiness, preserving question/task/trial precedence."""
    enabled_refs = {
        str(row.get("密钥引用ID", ""))
        for row in catalog.get("账号列表", [])
        if isinstance(row, dict) and row.get("是否启用") is True and row.get("密钥引用ID")
    }
    setup = data.setdefault("setup", {})
    pending = setup.get("pending_new_account_questions", {})
    unresolved = False
    task_pending = False
    trial_pending = False
    if isinstance(pending, dict):
        for ref, entry in pending.items():
            if ref not in enabled_refs or not isinstance(entry, dict):
                continue
            if entry.get("是否需要新增或调整任务") is None or entry.get("是否需要试运行") is None:
                unresolved = True
                continue
            if entry.get("任务处理状态") == "pending":
                task_pending = True
            if entry.get("是否需要试运行") is True and ref not in verified_account_refs(data):
                trial_pending = True
    approved = verified_account_refs(data)
    ready = bool(enabled_refs) and enabled_refs.issubset(approved) and not unresolved and not task_pending and not trial_pending
    if unresolved:
        setup["status"] = "account_decision_pending"
    elif task_pending:
        setup["status"] = "requirements_pending"
    elif trial_pending:
        setup["status"] = "trial_pending"
    else:
        setup["status"] = "ready" if ready else "account_decision_pending"
    return ready


def result_target(task: dict, login_url: str, layout: dict) -> dict:
    collection_mode = str(task.get("采集模式") or "页面采集")
    location = task.get("保存位置") if isinstance(task.get("保存位置"), dict) else {}
    default_table = "按工作表策略确定" if collection_mode == "下载文件导入" else "按网址自动分表"
    explicit_base = str(location.get("多维表格") or task.get("目标多维表格") or "网页数据采集结果").strip()
    explicit_table = str(location.get("数据表") or task.get("目标数据表") or default_table).strip()
    source = str(location.get("来源") or ("默认" if explicit_base == "网页数据采集结果" and explicit_table == default_table else "用户指定"))
    use_default_base = explicit_base in {"", "网页数据采集结果"}
    base_name = layout.get("base", {}).get("name") or "网页数据采集结果" if use_default_base else explicit_base
    base_link = str(location.get("多维表格链接") or location.get("base_url") or "").strip()
    if not base_link and explicit_base.startswith(("http://", "https://")):
        base_link = explicit_base
    base_url = layout.get("base", {}).get("url", "") if use_default_base else base_link
    base_token = layout.get("base", {}).get("token", "") if use_default_base else ""

    if collection_mode == "下载文件导入":
        config = task.get("文件导入", {}) if isinstance(task.get("文件导入"), dict) else {}
        return {
            "base_name": base_name,
            "base_url": base_url,
            "base_token": base_token,
            "table_name": explicit_table,
            "table_id": str(location.get("table_id") or ""),
            "auto_partitioned_by_host": False,
            "needs_table_creation": explicit_table == "按工作表策略确定" or not bool(location.get("table_id")),
            "target_confirmed": True,
            "target_mode": source,
            "target_needs_validation": source == "用户指定",
            "table_strategy": config.get("多工作表策略", "每个工作表新建数据表"),
        }

    use_auto_table = explicit_table in {"", "按网址自动分表"}
    table_name = table_name_for_url(login_url) if use_auto_table else explicit_table
    table_id = str(location.get("table_id") or "")
    if use_auto_table:
        host = (urlparse(login_url).hostname or "").casefold()
        if host.startswith("www."):
            host = host[4:]
        table = next((x for x in layout.get("site_tables", {}).values() if x.get("host") == host), None)
        if table:
            table_name = table.get("table_name") or table_name
            table_id = table.get("table_id", "")
    return {
        "base_name": base_name,
        "base_url": base_url,
        "base_token": base_token,
        "table_name": table_name,
        "table_id": table_id,
        "auto_partitioned_by_host": use_auto_table,
        "needs_table_creation": not bool(table_id),
        "target_confirmed": True,
        "target_mode": source,
        "target_needs_validation": source == "用户指定",
    }


def selected_account_rows(catalog: dict, scope_json: str | None) -> list[dict]:
    rows = [row for row in catalog.get("账号列表", []) if isinstance(row, dict) and row.get("是否启用") is True]
    rows.sort(key=lambda row: (int(row.get("执行顺序", 999999)), str(row.get("网站名称", "")), str(row.get("账号标识", ""))))
    if scope_json:
        scope = json.loads(scope_json)
        if not isinstance(scope, list) or not scope:
            raise SystemExit("scope_json_must_be_nonempty_array")
        wanted = {str(x) for x in scope}
        rows = [row for row in rows if str(row.get("密钥引用ID", "")) in wanted]
    return rows


def build_items(catalog: dict, tasks: list[dict], scope_json: str | None, layout: dict | None = None, mode: str = "run") -> list[dict]:
    layout = layout or {"base": {"name": "网页数据采集结果", "url": "", "token": ""}, "site_tables": {}}
    rows = selected_account_rows(catalog, scope_json)
    items: list[dict] = []
    for row in rows:
        matched = [task for task in tasks if task_matches_account(task, row)]
        for index, task in enumerate(matched):
            login_url = str(row.get("登录网址", ""))
            login_mode = str(row.get("登录方式") or "account_password")
            requires_login = login_mode == "account_password"
            collection_mode = str(task.get("采集模式") or "页面采集")
            target = result_target(task, login_url, layout)
            file_config = task.get("文件导入", {}) if isinstance(task.get("文件导入"), dict) else {}
            if collection_mode == "下载文件导入" and mode == "run" and task.get("写入方式") == "新增或更新":
                if not task.get("重复字段") and file_config.get("采用内容指纹兜底") is not True:
                    raise SystemExit(f"download_import_unique_field_confirmation_required:{task.get('任务名称', '')}")
            items.append({
                "site_name": str(row.get("网站名称", "")),
                "login_url": login_url,
                "login_mode": login_mode,
                "account_label": str(row.get("账号标识", "")) if requires_login else "无需登录",
                "secret_ref": str(row.get("密钥引用ID", "")),
                "task_name": str(task.get("任务名称", "")),
                "task": {
                    "采集模式": collection_mode,
                    "操作需求": task.get("操作需求"),
                    "每账号采集条数": 1 if mode == "trial" else task.get("每账号采集条数"),
                    "正式采集条数": task.get("每账号采集条数"),
                    "采集字段": task.get("采集字段"),
                    "目标多维表格": target["base_name"],
                    "目标数据表": target["table_name"],
                    "目标位置": target,
                    "重复字段": task.get("重复字段"),
                    "写入方式": task.get("写入方式"),
                    "文件导入": file_config if collection_mode == "下载文件导入" else None,
                    "试运行规则": "每个工作表预览1条且不写入" if collection_mode == "下载文件导入" else "采集1条且不写入",
                },
                "login_required": requires_login and index == 0,
                "logout_required": requires_login and index == len(matched) - 1,
                "status": "pending",
                "stage": "pending",
                "result": None,
                "recipe_version": None,
                "last_error": None,
            })
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "status", "set-stage", "block", "resume", "record-result", "complete-current", "finalize", "mark-account-trial", "mark-new-account-decisions", "reset"])
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--mode", choices=sorted(MODES))
    parser.add_argument("--task-name", help="Optional single task; omitted means all enabled matching tasks")
    parser.add_argument("--scope-json", help="Optional array of allowed secret reference IDs")
    parser.add_argument("--force-trial", action="store_true", help="Repeat trial for already approved accounts when explicitly requested")
    parser.add_argument("--stage", choices=sorted(STAGES))
    parser.add_argument("--reason")
    parser.add_argument("--success", choices=["true", "false"])
    parser.add_argument("--decision", choices=["verified", "skipped"], help="Account-level trial decision")
    parser.add_argument("--task-decision", choices=["yes", "no", "configured"], help="Whether task work is needed, not needed, or completed for the new account")
    parser.add_argument("--trial-decision", choices=["yes", "no"], help="Whether the new account should run a trial")
    parser.add_argument("--secret-ref", help="Account secret reference for account-level decisions")
    args = parser.parse_args()

    tasks_path = args.tasks.expanduser().resolve()
    data = load(tasks_path)

    if args.action == "mark-new-account-decisions":
        if not args.secret_ref or args.task_decision is None or args.trial_decision is None:
            raise SystemExit("secret_ref_task_decision_and_trial_decision_required")
        catalog = load(args.catalog)
        account = next((row for row in catalog.get("账号列表", []) if str(row.get("密钥引用ID", "")) == args.secret_ref), None)
        if account is None:
            raise SystemExit("account_not_found")
        setup = data.setdefault("setup", {})
        pending = setup.setdefault("pending_new_account_questions", {})
        entry = pending.setdefault(args.secret_ref, {
            "网站名称": str(account.get("网站名称", "")),
            "账号标识": str(account.get("账号标识", "")),
        })
        entry["是否需要新增或调整任务"] = args.task_decision != "no"
        entry["任务处理状态"] = {"yes": "pending", "no": "not_needed", "configured": "completed"}[args.task_decision]
        entry["是否需要试运行"] = args.trial_decision == "yes"
        entry["决定时间"] = now()
        if args.trial_decision == "no":
            verified = {str(x) for x in setup.get("trial_verified_accounts", []) if str(x)}
            skipped = {str(x) for x in setup.get("trial_skipped_accounts", []) if str(x)}
            verified.discard(args.secret_ref)
            skipped.add(args.secret_ref)
            setup["trial_verified_accounts"] = sorted(verified)
            setup["trial_skipped_accounts"] = sorted(skipped)
            setup.setdefault("trial_account_decisions", {})[args.secret_ref] = {
                "decision": "skipped",
                "account_label": str(account.get("账号标识", "")),
                "site_name": str(account.get("网站名称", "")),
                "decided_at": now(),
            }
        recalculate_trial_status(data, catalog)
        if args.trial_decision == "yes":
            setup["status"] = "trial_pending"
        save(tasks_path, data)
        print(json.dumps({
            "updated": True,
            "account_label": str(account.get("账号标识", "")),
            "task_decision": args.task_decision,
            "trial_decision": args.trial_decision,
            "next_action": "confirm_or_create_task_then_trial" if args.trial_decision == "yes" else "confirm_or_create_task_then_run",
            "setup_status": setup.get("status"),
        }, ensure_ascii=False))
        return 0

    if args.action == "mark-account-trial":
        if not args.secret_ref or not args.decision:
            raise SystemExit("secret_ref_and_decision_required")
        catalog = load(args.catalog)
        account = next((row for row in catalog.get("账号列表", []) if str(row.get("密钥引用ID", "")) == args.secret_ref), None)
        if account is None:
            raise SystemExit("account_not_found")
        setup = data.setdefault("setup", {})
        pending_questions = setup.get("pending_new_account_questions", {})
        entry = pending_questions.get(args.secret_ref) if isinstance(pending_questions, dict) else None
        if args.decision == "skipped":
            if not isinstance(entry, dict) or entry.get("是否需要新增或调整任务") is None or entry.get("是否需要试运行") is not False:
                raise SystemExit("new_account_skip_requires_completed_two_question_decision")
            if entry.get("任务处理状态") == "pending":
                raise SystemExit("new_account_task_configuration_still_pending")
        else:
            plan = data.get("execution_plan") or data.get("batch")
            matching_completed_trial = isinstance(plan, dict) and plan.get("mode") == "trial" and plan.get("status") == "completed" and any(
                str(item.get("secret_ref", "")) == args.secret_ref and item.get("status") == "completed"
                for item in plan_items(plan)
            )
            if not matching_completed_trial:
                raise SystemExit("verified_decision_requires_completed_trial_for_account")
        verified = {str(x) for x in setup.get("trial_verified_accounts", []) if str(x)}
        skipped = {str(x) for x in setup.get("trial_skipped_accounts", []) if str(x)}
        verified.discard(args.secret_ref)
        skipped.discard(args.secret_ref)
        if args.decision == "verified":
            verified.add(args.secret_ref)
        else:
            skipped.add(args.secret_ref)
        setup["trial_verified_accounts"] = sorted(verified)
        setup["trial_skipped_accounts"] = sorted(skipped)
        decisions = setup.setdefault("trial_account_decisions", {})
        decisions[args.secret_ref] = {
            "decision": args.decision,
            "account_label": str(account.get("账号标识", "")),
            "site_name": str(account.get("网站名称", "")),
            "decided_at": now(),
        }
        recalculate_trial_status(data, catalog)
        save(tasks_path, data)
        print(json.dumps({
            "updated": True,
            "decision": args.decision,
            "account_label": str(account.get("账号标识", "")),
            "setup_status": data.get("setup", {}).get("status"),
        }, ensure_ascii=False))
        return 0

    if args.action == "init":
        if not args.mode:
            raise SystemExit("mode_required")
        existing = data.get("execution_plan") or data.get("batch")
        if isinstance(existing, dict) and existing.get("status") in ACTIVE_STATUSES:
            raise SystemExit("execution_plan_already_active")
        tasks = selected_tasks(data, args.mode, args.task_name)
        catalog = load(args.catalog)
        recalculate_trial_status(data, catalog)
        setup = data.setdefault("setup", {})
        pending_questions = setup.setdefault("pending_new_account_questions", {})
        selected_rows = selected_account_rows(catalog, args.scope_json)
        selected_refs = {str(row.get("密钥引用ID", "")) for row in selected_rows}
        approved_before_questions = verified_account_refs(data)
        for row in selected_rows:
            ref = str(row.get("密钥引用ID", ""))
            if ref and ref not in approved_before_questions and ref not in pending_questions:
                pending_questions[ref] = {
                    "网站名称": str(row.get("网站名称", "")),
                    "账号标识": str(row.get("账号标识", "")),
                    "登录方式": str(row.get("登录方式") or "account_password"),
                    "是否需要新增或调整任务": None,
                    "任务处理状态": "unanswered",
                    "是否需要试运行": None,
                }
        unresolved = []
        pending_task_work = []
        for ref, entry in pending_questions.items() if isinstance(pending_questions, dict) else []:
            if ref not in selected_refs or not isinstance(entry, dict):
                continue
            if entry.get("是否需要新增或调整任务") is None or entry.get("是否需要试运行") is None:
                unresolved.append({"secret_ref": ref, "site_name": entry.get("网站名称", ""), "account_label": entry.get("账号标识", "")})
            elif entry.get("任务处理状态") == "pending":
                pending_task_work.append({"secret_ref": ref, "site_name": entry.get("网站名称", ""), "account_label": entry.get("账号标识", "")})
        if unresolved:
            setup["status"] = "account_decision_pending"
            save(tasks_path, data)
            print(json.dumps({
                "init": False,
                "reason": "new_account_questions_unanswered",
                "message": "检测到新增网页目标尚未完成两项独立确认：是否新增或调整采集任务；是否对新增目标试运行。",
                "new_accounts": unresolved,
            }, ensure_ascii=False))
            return 2
        if pending_task_work:
            setup["status"] = "requirements_pending"
            save(tasks_path, data)
            print(json.dumps({
                "init": False,
                "reason": "new_account_task_configuration_pending",
                "message": "用户已选择为新增网页目标新增或调整任务，请先完成任务配置，再将任务决定标记为 configured。",
                "new_accounts": pending_task_work,
            }, ensure_ascii=False))
            return 2
        if not tasks:
            raise SystemExit("no_enabled_matching_tasks")
        layout = load_layout(args.layout.expanduser().resolve())
        items = build_items(catalog, tasks, args.scope_json, layout, args.mode)
        if args.mode == "trial" and not args.force_trial:
            approved = verified_account_refs(data)
            items = [item for item in items if str(item.get("secret_ref", "")) not in approved]
            if not items:
                print(json.dumps({
                    "init": False,
                    "reason": "all_accounts_already_decided",
                    "message": "所有目标都已试运行或已由用户明确跳过；无需重复试运行。只有用户显性要求时才使用 --force-trial。",
                }, ensure_ascii=False))
                return 0
        if not items:
            raise SystemExit("no_account_task_items_in_scope")
        # 正式运行前：账号试运行决定必须明确；同一账号不会因新增/修改任务而重复试运行。
        if args.mode == "run":
            approved = verified_account_refs(data)
            unapproved: list[dict] = []
            seen_refs: set[str] = set()
            for item in items:
                ref = str(item.get("secret_ref", ""))
                if ref not in approved and ref not in seen_refs:
                    seen_refs.add(ref)
                    unapproved.append({
                        "site_name": item.get("site_name", ""),
                        "account_label": item.get("account_label", ""),
                        "secret_ref": ref,
                    })
            if unapproved:
                labels = [f"{x['site_name']}／{x['account_label']}" for x in unapproved]
                data.setdefault("setup", {})["status"] = "account_decision_pending"
                save(tasks_path, data)
                print(json.dumps({
                    "init": False,
                    "reason": "new_accounts_need_user_decision",
                    "message": "检测到新增网页目标，请先询问用户：是否新增或调整采集任务；是否对新增目标试运行。若用户明确跳过试运行，调用 mark-account-trial --decision skipped 后再正式运行。目标：" + "、".join(labels),
                    "new_accounts": unapproved,
                }, ensure_ascii=False))
                return 2
        items[0]["status"] = "in_progress"
        plan = {
            "plan_type": "continuous_cross_site_v2",
            "mode": args.mode,
            "task_name": args.task_name,
            "task_names": [str(x.get("任务名称", "")) for x in tasks],
            "status": "running",
            "current_position": 0,
            "items": items,
            "started_at": now(),
            "updated_at": now(),
        }
        data["execution_plan"] = plan
        data.pop("batch", None)
        save(tasks_path, data)
        print(json.dumps(public_state(plan), ensure_ascii=False))
        return 0

    if args.action == "reset":
        data.pop("execution_plan", None)
        data.pop("batch", None)
        save(tasks_path, data)
        print(json.dumps({"reset": True}, ensure_ascii=False))
        return 0

    if args.action == "status":
        try:
            plan = get_plan(data)
        except SystemExit:
            print(json.dumps({"exists": False, "status": "not_initialized"}, ensure_ascii=False))
            return 0
        print(json.dumps(public_state(plan), ensure_ascii=False))
        return 0

    plan = get_plan(data)

    if args.action == "finalize":
        success = args.success == "true"
        if plan.get("mode") != "trial" or plan.get("status") != "awaiting_confirmation":
            raise SystemExit("trial_not_awaiting_confirmation")
        plan["status"] = "completed" if success else "failed"
        plan["completed_at"] = now()
        plan["updated_at"] = now()
        if success:
            # 试运行通过：按账号记录一次性验证；同一账号后续任务变化不再自动要求重跑。
            items = plan_items(plan)
            setup = data.setdefault("setup", {})
            verified = {str(x) for x in setup.get("trial_verified_accounts", []) if str(x)}
            skipped = {str(x) for x in setup.get("trial_skipped_accounts", []) if str(x)}
            decisions = setup.setdefault("trial_account_decisions", {})
            for item in items:
                if item.get("status") != "completed":
                    continue
                ref = str(item.get("secret_ref", ""))
                if not ref:
                    continue
                verified.add(ref)
                skipped.discard(ref)
                decisions[ref] = {
                    "decision": "verified",
                    "account_label": str(item.get("account_label", "")),
                    "site_name": str(item.get("site_name", "")),
                    "decided_at": now(),
                }
            setup["trial_verified_accounts"] = sorted(verified)
            setup["trial_skipped_accounts"] = sorted(skipped)
            try:
                catalog = load(args.catalog)
                recalculate_trial_status(data, catalog)
            except (FileNotFoundError, json.JSONDecodeError):
                setup["status"] = "account_decision_pending"
        save(tasks_path, data)
        print(json.dumps(public_state(plan), ensure_ascii=False))
        return 0

    current = get_current(plan)
    if args.action == "set-stage":
        if plan.get("status") != "running" or not args.stage:
            raise SystemExit("running_plan_and_stage_required")
        current["stage"] = args.stage
    elif args.action == "block":
        if plan.get("status") != "running":
            raise SystemExit("execution_plan_not_running")
        plan["status"] = "blocked"
        current["status"] = "blocked"
        current["last_error"] = args.reason or "需要用户处理"
    elif args.action == "resume":
        if plan.get("status") != "blocked":
            raise SystemExit("execution_plan_not_blocked")
        plan["status"] = "running"
        current["status"] = "in_progress"
        current["last_error"] = None
    elif args.action == "record-result":
        if plan.get("status") != "running":
            raise SystemExit("execution_plan_not_running")
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, (dict, list)):
            raise SystemExit("result_must_be_object_or_array")
        # 自动注入账号与任务标识，避免多账号数据写入同表后无法区分
        account_label = str(current.get("account_label", ""))
        task_name = str(current.get("task_name", ""))
        collected_at = now()
        if isinstance(payload, list):
            for index, row in enumerate(payload, 1):
                if not isinstance(row, dict):
                    raise SystemExit(f"result_row_{index}_must_be_object")
                row["采集账号"] = account_label
                row["采集任务"] = task_name
                row["采集时间"] = collected_at
        elif isinstance(payload, dict):
            payload["采集账号"] = account_label
            payload["采集任务"] = task_name
            payload["采集时间"] = collected_at
        current["result"] = payload
    elif args.action == "complete-current":
        if plan.get("status") != "running":
            raise SystemExit("execution_plan_not_running")
        if current.get("result") is None:
            raise SystemExit("result_must_be_recorded_before_completion")
        required_stage = "logout" if current.get("logout_required", True) else "verify"
        if current.get("stage") != required_stage:
            raise SystemExit(f"{required_stage}_must_be_verified_before_completion")
        current["status"] = "completed"
        current["stage"] = "done"
        pos = int(plan["current_position"]) + 1
        items = plan_items(plan)
        if pos < len(items):
            plan["current_position"] = pos
            items[pos]["status"] = "in_progress"
            plan["status"] = "running"
        else:
            plan["current_position"] = None
            plan["status"] = "awaiting_confirmation" if plan.get("mode") == "trial" else "completed"
            plan["completed_at"] = now() if plan["status"] == "completed" else None
    plan["updated_at"] = now()
    save(tasks_path, data)
    print(json.dumps(public_state(plan), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
