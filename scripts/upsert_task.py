#!/usr/bin/env python3
"""Create or update a non-secret collection task from a JSON payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from state_io import atomic_write_json, load_json

DEFAULT_TASKS = Path.home() / ".config" / "web-data-to-base" / "tasks.local.json"
DEFAULT_BASE_NAME = "网页数据采集结果"
COMMON_REQUIRED = ("任务名称", "网页", "操作需求", "写入方式")
COLLECTION_MODES = {"页面采集", "下载文件导入"}
FILE_TYPES = {"xlsx", "xls", "csv"}
TABLE_STRATEGIES = {"每个工作表新建数据表", "逐表指定", "兼容工作表合并"}


def error(message: str) -> int:
    print(json.dumps({"updated": False, "errors": [message]}, ensure_ascii=False))
    return 2


def destination_defaults(collection_mode: str) -> tuple[str, str]:
    if collection_mode == "下载文件导入":
        return "按工作表策略确定", "下载文件工作表映射"
    return "按网址自动分表", "登录网址主机"


def normalize_destination(task: dict, collection_mode: str, old_task: dict | None) -> dict:
    """Return one canonical task-level destination while preserving existing custom settings."""
    supplied = task.get("保存位置")
    old_location = old_task.get("保存位置") if isinstance(old_task, dict) else None
    if supplied is None and isinstance(old_location, dict):
        supplied = old_location
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("保存位置必须是对象")
    supplied = dict(supplied or {})

    # Compatibility with v1.1 fields and legacy file-import target objects.
    legacy_file = task.get("文件导入") if isinstance(task.get("文件导入"), dict) else {}
    legacy_target = legacy_file.get("导入目标") if isinstance(legacy_file.get("导入目标"), dict) else {}
    old_file = old_task.get("文件导入") if isinstance(old_task, dict) and isinstance(old_task.get("文件导入"), dict) else {}
    old_legacy_target = old_file.get("导入目标") if isinstance(old_file.get("导入目标"), dict) else {}
    default_table, default_rule = destination_defaults(collection_mode)

    base = str(
        supplied.get("多维表格")
        or task.get("目标多维表格")
        or legacy_target.get("多维表格")
        or (old_task or {}).get("目标多维表格")
        or old_legacy_target.get("多维表格")
        or DEFAULT_BASE_NAME
    ).strip()
    table = str(
        supplied.get("数据表")
        or task.get("目标数据表")
        or legacy_target.get("数据表")
        or (old_task or {}).get("目标数据表")
        or old_legacy_target.get("数据表")
        or default_table
    ).strip()
    rule = str(
        supplied.get("分表规则")
        or task.get("目标分表规则")
        or (old_task or {}).get("目标分表规则")
        or default_rule
    ).strip()
    if not base or not table:
        raise ValueError("保存位置必须包含非空的多维表格和数据表")

    explicit_keys = {"保存位置", "目标多维表格", "目标数据表", "目标分表规则"}
    user_explicit = any(key in task for key in explicit_keys)
    if legacy_target and task.get("文件导入", {}).get("目标已确认") is True:
        user_explicit = True
    old_source = str(old_location.get("来源")) if isinstance(old_location, dict) else ""
    if not user_explicit and old_source:
        source = old_source
    else:
        source = "默认" if base == DEFAULT_BASE_NAME and table == default_table and rule == default_rule else "用户指定"
    base_link = str(supplied.get("多维表格链接") or supplied.get("base_url") or "").strip()
    if not base_link and base.startswith(("http://", "https://")):
        base_link = base
    result = {"多维表格": base, "数据表": table, "分表规则": rule, "来源": source}
    if base_link:
        result["多维表格链接"] = base_link
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--json", help="Task JSON object; omit to read from stdin")
    args = parser.parse_args()

    payload_text = args.json if args.json is not None else sys.stdin.read()
    task = json.loads(payload_text)
    if not isinstance(task, dict):
        raise SystemExit("task_must_be_object")

    path = args.tasks.expanduser().resolve()
    if path.exists():
        data = load_json(path)
    else:
        data = {"schema_version": 3, "填写说明": "不要手动填写。这个文件由系统根据用户回答自动创建和更新。", "setup": {"status": "requirements_pending"}, "任务列表": []}
    tasks = data.setdefault("任务列表", [])
    old_task = next((item for item in tasks if isinstance(item, dict) and item.get("任务名称") == task.get("任务名称")), None)

    task.setdefault("采集模式", old_task.get("采集模式", "页面采集") if isinstance(old_task, dict) else "页面采集")
    collection_mode = task.get("采集模式")
    if collection_mode not in COLLECTION_MODES:
        return error("采集模式必须是页面采集或下载文件导入")

    # A natural-language edit may omit unchanged fields. Preserve them from the current task.
    if isinstance(old_task, dict):
        for key in COMMON_REQUIRED + ("每账号采集条数", "采集字段", "重复字段", "是否启用", "执行顺序", "文件导入"):
            if key not in task and key in old_task:
                task[key] = old_task[key]

    missing = [key for key in COMMON_REQUIRED if task.get(key) in (None, "", [])]
    if collection_mode == "页面采集":
        for key in ("每账号采集条数", "采集字段", "重复字段"):
            if task.get(key) in (None, "", []):
                missing.append(key)
    else:
        task.setdefault("采集字段", [])
        task.setdefault("重复字段", [])
    if missing:
        print(json.dumps({"updated": False, "errors": [f"缺少字段：{key}" for key in missing]}, ensure_ascii=False))
        return 2

    if not isinstance(task.get("网页"), (str, list)):
        return error("网页必须是字符串或字符串数组")
    if isinstance(task["网页"], list) and not all(isinstance(x, str) and x.strip() for x in task["网页"]):
        return error("网页数组只能包含非空字符串")
    for list_field in ("采集字段", "重复字段"):
        value = task.get(list_field)
        if not isinstance(value, list) or (collection_mode == "页面采集" and not value) or not all(isinstance(x, str) and x.strip() for x in value):
            qualifier = "非空" if collection_mode == "页面采集" else ""
            return error(f"{list_field}必须是{qualifier}字符串数组")
    if task.get("写入方式") not in {"新增", "新增或更新"}:
        return error("写入方式必须是新增或新增或更新")

    if collection_mode == "页面采集":
        if not isinstance(task["每账号采集条数"], int) or isinstance(task["每账号采集条数"], bool) or task["每账号采集条数"] <= 0:
            return error("每账号采集条数必须是正整数")
        task.pop("文件导入", None)
    else:
        task["每账号采集条数"] = 1
        file_config = task.setdefault("文件导入", {})
        if not isinstance(file_config, dict):
            return error("文件导入必须是对象")
        formats = file_config.setdefault("支持格式", ["xlsx", "xls", "csv"])
        normalized_formats = [str(x).casefold().lstrip(".") for x in formats] if isinstance(formats, list) else []
        if not normalized_formats or any(x not in FILE_TYPES for x in normalized_formats):
            return error("支持格式只能包含 xlsx、xls、csv")
        file_config["支持格式"] = normalized_formats
        file_config.setdefault("工作表", "全部")
        file_config.setdefault("表头识别", "自动")
        file_config.setdefault("字段映射", {})
        file_config.setdefault("多工作表策略", "每个工作表新建数据表")
        file_config.setdefault("采用内容指纹兜底", False)
        if file_config["多工作表策略"] not in TABLE_STRATEGIES:
            return error("多工作表策略不受支持")
        if not isinstance(file_config["字段映射"], dict):
            return error("字段映射必须是对象")
        if not isinstance(file_config["采用内容指纹兜底"], bool):
            return error("采用内容指纹兜底必须是布尔值")

    task.pop("runtime", None)
    try:
        location = normalize_destination(task, collection_mode, old_task)
    except ValueError as exc:
        return error(str(exc))
    task["保存位置"] = location
    task["目标多维表格"] = location["多维表格"]
    task["目标数据表"] = location["数据表"]
    task["目标分表规则"] = location["分表规则"]
    if collection_mode == "下载文件导入":
        file_config = task["文件导入"]
        legacy_mapping = None
        existing_import_target = file_config.get("导入目标")
        if isinstance(existing_import_target, dict):
            legacy_mapping = existing_import_target.get("工作表数据表映射")
        file_config["目标已确认"] = True
        file_config["导入目标"] = {
            "方式": "默认多维表格" if location["来源"] == "默认" else "用户指定多维表格",
            "多维表格": location["多维表格"],
            "数据表": location["数据表"],
        }
        if location.get("多维表格链接"):
            file_config["导入目标"]["多维表格链接"] = location["多维表格链接"]
        if file_config["多工作表策略"] == "逐表指定":
            mapping = legacy_mapping or file_config.get("工作表数据表映射")
            if not isinstance(mapping, dict) or not mapping:
                return error("逐表指定策略必须提供工作表数据表映射")
            file_config["导入目标"]["工作表数据表映射"] = mapping
        if file_config["多工作表策略"] == "兼容工作表合并" and location["数据表"] in {"", "按工作表策略确定"}:
            return error("兼容工作表合并策略必须指定目标数据表")
        task["目标确认状态"] = "已确认（默认）" if location["来源"] == "默认" else "已确认（用户指定）"
    else:
        task.pop("目标确认状态", None)

    task.setdefault("是否启用", True)
    task.setdefault("执行顺序", 999999)
    fields = list(task.get("采集字段") or [])
    system_fields = ("采集账号", "采集任务", "采集时间")
    if collection_mode == "下载文件导入":
        system_fields += ("来源网址", "来源文件", "来源工作表", "导入批次")
    for system_field in system_fields:
        if system_field not in fields:
            fields.append(system_field)
    task["采集字段"] = fields
    dup = list(task.get("重复字段") or [])
    if collection_mode == "页面采集" and "采集账号" not in dup:
        dup.append("采集账号")
    task["重复字段"] = dup
    if not isinstance(task["是否启用"], bool):
        return error("是否启用必须是布尔值")
    if not isinstance(task["执行顺序"], int) or task["执行顺序"] < 1:
        return error("执行顺序必须是正整数")
    task["runtime"] = {
        "configured": False,
        "trial_passed": False,
        "trial_verified_accounts": [],
        "site_profile": {},
        "last_trial_at": None,
    }

    match = next((i for i, item in enumerate(tasks) if item.get("任务名称") == task["任务名称"]), None)
    if match is None:
        tasks.append(task)
        action = "created"
    else:
        task["runtime"] = tasks[match].get("runtime", task["runtime"])
        tasks[match] = task
        action = "updated"

    data["schema_version"] = max(int(data.get("schema_version", 1)), 3)
    data.pop("execution_plan", None)
    data.pop("batch", None)
    atomic_write_json(path, data)
    print(json.dumps({
        "updated": True,
        "action": action,
        "trial_invalidated": False,
        "save_location": location,
        "tasks_path": str(path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
