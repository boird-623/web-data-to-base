#!/usr/bin/env python3
"""Print the startup menu and show task destinations with Base links."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from state_io import load_json

DEFAULT_CATALOG = Path.home() / ".config" / "web-data-to-base" / "account_catalog.local.json"
DEFAULT_TASKS = Path.home() / ".config" / "web-data-to-base" / "tasks.local.json"
DEFAULT_LAYOUT = Path.home() / ".config" / "web-data-to-base" / "base_layout.local.json"
DEFAULT_BASE_NAME = "网页数据采集结果"
DOWNLOAD_IMPORT_HINT = "现在已经支持从网页下载 XLSX、XLS、CSV 表格并直接导入多维表格；只需要描述要下载什么、如何写入以及保存到哪里即可，未指定保存位置时使用默认多维表格。"

MENU = {
    "1": {"title": "查看并配置账号密码", "action": "configure_targets"},
    "2": {"title": "查看并配置采集任务", "action": "configure_tasks"},
    "3": {"title": "试运行（每个启用网页目标或文件工作表预览 1 条）", "action": "trial"},
    "4": {"title": "正式运行", "action": "run"},
}


def safe_load(path: Path, fallback: dict) -> dict:
    try:
        return load_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def task_location(task: dict, layout: dict) -> dict:
    mode = str(task.get("采集模式") or "页面采集")
    location = task.get("保存位置") if isinstance(task.get("保存位置"), dict) else {}
    base_name = str(location.get("多维表格") or task.get("目标多维表格") or DEFAULT_BASE_NAME)
    default_table = "按工作表策略确定" if mode == "下载文件导入" else "按网址自动分表"
    table_name = str(location.get("数据表") or task.get("目标数据表") or default_table)
    source = str(location.get("来源") or ("默认" if base_name == DEFAULT_BASE_NAME and table_name == default_table else "用户指定"))
    rule = str(location.get("分表规则") or task.get("目标分表规则") or ("下载文件工作表映射" if mode == "下载文件导入" else "登录网址主机"))
    base_link = str(location.get("多维表格链接") or location.get("base_url") or "").strip()
    if not base_link and base_name.startswith(("http://", "https://")):
        base_link = base_name
    if not base_link and source == "默认":
        base_link = str(layout.get("base", {}).get("url") or "").strip()
    link_display = base_link or "链接待绑定"
    return {
        "base": base_name,
        "base_link": base_link,
        "table": table_name,
        "partition_rule": rule,
        "source": source,
        "display": f"{base_name}（{link_display}） → {table_name}",
    }


def task_rows(tasks_data: dict, layout: dict) -> list[dict]:
    tasks = [x for x in tasks_data.get("任务列表", []) if isinstance(x, dict)]
    tasks.sort(key=lambda x: (int(x.get("执行顺序", 999999)), str(x.get("任务名称", ""))))
    rows = []
    for task in tasks:
        rows.append({
            "task_name": str(task.get("任务名称", "")),
            "web_targets": task.get("网页", []),
            "collection_mode": str(task.get("采集模式") or "页面采集"),
            "enabled": task.get("是否启用", True) is not False,
            "write_mode": str(task.get("写入方式") or ""),
            "save_location": task_location(task, layout),
            "order": int(task.get("执行顺序", 999999)),
        })
    return rows


def summary(catalog_path: Path, tasks_path: Path, layout_path: Path) -> dict:
    catalog = safe_load(catalog_path, {"账号列表": []})
    tasks_data = safe_load(tasks_path, {"任务列表": []})
    layout = safe_load(layout_path, {"base": {}})
    targets = [x for x in catalog.get("账号列表", []) if isinstance(x, dict)]
    enabled = [x for x in targets if x.get("是否启用") is True]
    login_targets = [x for x in enabled if str(x.get("登录方式") or "account_password") == "account_password"]
    public_targets = [x for x in enabled if str(x.get("登录方式") or "account_password") == "none"]
    tasks = task_rows(tasks_data, layout)
    active_tasks = [x for x in tasks if x["enabled"]]
    file_import_tasks = [x for x in active_tasks if x["collection_mode"] == "下载文件导入"]
    custom_targets = [x for x in active_tasks if x["save_location"]["source"] == "用户指定"]
    plan = tasks_data.get("execution_plan") or tasks_data.get("batch")
    return {
        "configured_targets": len(targets),
        "enabled_targets": len(enabled),
        "enabled_login_targets": len(login_targets),
        "enabled_public_targets": len(public_targets),
        "configured_tasks": len(tasks),
        "enabled_tasks": len(active_tasks),
        "enabled_file_import_tasks": len(file_import_tasks),
        "custom_destination_tasks": len(custom_targets),
        "active_plan_status": plan.get("status") if isinstance(plan, dict) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("choice", nargs="?", choices=sorted(MENU))
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    args = parser.parse_args()
    catalog_path = args.catalog.expanduser().resolve()
    tasks_path = args.tasks.expanduser().resolve()
    layout_path = args.layout.expanduser().resolve()
    tasks_data = safe_load(tasks_path, {"任务列表": []})
    layout = safe_load(layout_path, {"base": {}})
    state = summary(catalog_path, tasks_path, layout_path)
    if args.choice:
        selected = MENU[args.choice]
        payload = {
            "selected": args.choice,
            **selected,
            "summary": state,
        }
        if args.choice == "2":
            payload["tasks"] = task_rows(tasks_data, layout)
            payload["display_instruction"] = "逐项展示任务名称、匹配网页、采集模式、启用状态、写入方式，以及包含多维表格链接的保存位置。"
            payload["capability_hint"] = DOWNLOAD_IMPORT_HINT
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps({
            "menu": [{"key": key, **value} for key, value in MENU.items()],
            "summary": state,
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
