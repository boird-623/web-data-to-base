#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "scripts" / "vendor"
sys.path.insert(0, str(VENDOR))
from openpyxl import Workbook
import xlwt


def run(*args: str) -> tuple[int, dict]:
    command = [sys.executable, str(ROOT / "scripts" / "spreadsheet_import.py"), *args]
    proc = subprocess.run(command, text=True, capture_output=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"invalid json: {proc.stdout}\n{proc.stderr}") from exc
    return proc.returncode, payload


def make_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "订单"
    ws.append(["订单导出说明"])
    ws.append(["订单号", "日期", "金额", "金额"])
    ws.append([123, datetime(2026, 9, 1, 10, 0), 12.5, 1])
    ws["A3"].number_format = "000000"
    ws.append([124, datetime(2026, 9, 2, 11, 0), 20, 2])
    ws["A4"].number_format = "000000"
    ws.append(["总计", None, 32.5, 3])
    ws.append(["订单号", "日期", "金额", "金额"])
    ws2 = wb.create_sheet("退款")
    ws2.append(["退款单号", "金额"])
    ws2.append(["R001", 5.5])
    wb.save(path)


def make_xls(path: Path) -> None:
    wb = xlwt.Workbook()
    ws = wb.add_sheet("旧格式")
    for c, v in enumerate(["编号", "金额", "日期"]):
        ws.write(0, c, v)
    zero = xlwt.easyxf(num_format_str="00000")
    day = xlwt.easyxf(num_format_str="YYYY-MM-DD")
    ws.write(1, 0, 7, zero)
    ws.write(1, 1, 9.9)
    ws.write(1, 2, datetime(2026, 9, 3), day)
    wb.save(str(path))


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        d = Path(temp)
        xlsx = d / "sample.xlsx"
        xls = d / "sample.xls"
        csv_path = d / "sample.csv"
        make_xlsx(xlsx)
        make_xls(xls)
        csv_path.write_text("编号,名称\n001,甲\n002,乙\n", encoding="utf-8-sig")

        code, xlsx_result = run("inspect", "--file", str(xlsx), "--mode", "trial", "--task-name", "测试")
        assert code == 0, xlsx_result
        assert xlsx_result["unit_count"] == 2
        order = xlsx_result["units"][0]
        assert order["header_row"] == 2
        assert order["full_record_count"] == 2
        assert order["record_count"] == 1
        assert order["trial_preview"][0]["订单号"] == "000123"
        assert order["summary_rows_excluded"] == 1
        assert order["repeated_headers_excluded"] == 1
        assert "金额_2" in order["headers"]
        assert order["requires_unique_field_confirmation"] is True

        code, xls_result = run("inspect", "--file", str(xls), "--unique-field", "编号", "--target-confirmed")
        assert code == 0, xls_result
        old = xls_result["units"][0]
        assert old["trial_preview"][0]["编号"] == "00007"
        assert old["field_types"]["日期"] == "datetime"

        code, csv_result = run("inspect", "--file", str(csv_path), "--mode", "run", "--write-mode", "新增", "--target-confirmed")
        assert code == 0, csv_result
        assert csv_result["units"][0]["full_record_count"] == 2
        assert csv_result["units"][0]["trial_preview"][0]["编号"] == "001"

        output = d / "prepared"
        code, prepared = run(
            "prepare", "--file", str(xlsx), "--mode", "run", "--unique-field", "订单号",
            "--target-confirmed", "--target-base", "测试 Base", "--output-dir", str(output)
        )
        assert code != 0 and "unique_fields_missing:退款:订单号" in prepared["error"]

        code, prepared = run(
            "prepare", "--file", str(xlsx), "--mode", "run", "--sheet", "订单", "--unique-field", "订单号",
            "--target-confirmed", "--target-base", "测试 Base", "--output-dir", str(output)
        )
        assert code == 0, prepared
        manifest = json.loads(Path(prepared["manifest"]).read_text(encoding="utf-8"))
        assert manifest["units"][0]["record_count"] == 2
        assert len(prepared["batch_files"]) == 1

        task_file = d / "tasks.json"
        task_file.write_text(json.dumps({"setup": {}, "任务列表": []}, ensure_ascii=False), encoding="utf-8")
        payload = {
            "任务名称": "下载导入",
            "网页": "示例站",
            "采集模式": "下载文件导入",
            "操作需求": "导出订单并导入",
            "采集字段": [],
            "重复字段": ["订单号"],
            "写入方式": "新增或更新",
            "文件导入": {"目标已确认": False},
        }
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "upsert_task.py"), "--tasks", str(task_file)],
            input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        saved = json.loads(task_file.read_text(encoding="utf-8"))["任务列表"][0]
        assert saved["目标确认状态"] == "已确认（默认）"
        assert saved["保存位置"] == {
            "多维表格": "网页数据采集结果",
            "数据表": "按工作表策略确定",
            "分表规则": "下载文件工作表映射",
            "来源": "默认",
        }
        assert saved["文件导入"]["支持格式"] == ["xlsx", "xls", "csv"]

        # Saving a custom destination must be visible, and later unrelated edits must preserve it.
        custom = dict(payload)
        custom["保存位置"] = {
            "多维表格": "客户订单库",
            "多维表格链接": "https://example.feishu.cn/base/custom",
            "数据表": "订单明细",
            "分表规则": "固定数据表",
        }
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "upsert_task.py"), "--tasks", str(task_file)],
            input=json.dumps(custom, ensure_ascii=False), text=True, capture_output=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        saved = json.loads(task_file.read_text(encoding="utf-8"))["任务列表"][0]
        assert saved["保存位置"]["来源"] == "用户指定"
        assert saved["保存位置"]["数据表"] == "订单明细"

        edited = dict(payload)
        edited["操作需求"] = "导出最新订单并导入"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "upsert_task.py"), "--tasks", str(task_file)],
            input=json.dumps(edited, ensure_ascii=False), text=True, capture_output=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        saved = json.loads(task_file.read_text(encoding="utf-8"))["任务列表"][0]
        assert saved["保存位置"]["数据表"] == "订单明细"
        assert saved["保存位置"]["来源"] == "用户指定"

        layout_file = d / "layout.json"
        layout_file.write_text(json.dumps({"base": {"name": "网页数据采集结果", "url": "https://example.feishu.cn/base/default"}}, ensure_ascii=False), encoding="utf-8")
        nav = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "navigation.py"), "2", "--tasks", str(task_file), "--catalog", str(d / "missing-catalog.json"), "--layout", str(layout_file)],
            text=True, capture_output=True,
        )
        assert nav.returncode == 0, nav.stdout + nav.stderr
        navigation = json.loads(nav.stdout)
        assert navigation["tasks"][0]["save_location"]["base_link"] == "https://example.feishu.cn/base/custom"
        assert navigation["tasks"][0]["save_location"]["display"] == "客户订单库（https://example.feishu.cn/base/custom） → 订单明细"
        assert "quick_actions" not in navigation
        assert "下载 XLSX、XLS、CSV" in navigation["capability_hint"]
        assert "freeform_hint" not in navigation

        main_nav = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "navigation.py"), "--tasks", str(task_file), "--catalog", str(d / "missing-catalog.json"), "--layout", str(layout_file)],
            text=True, capture_output=True,
        )
        assert main_nav.returncode == 0, main_nav.stdout + main_nav.stderr
        main_payload = json.loads(main_nav.stdout)
        assert len(main_payload["menu"]) == 4
        assert "quick_actions" not in main_payload
        assert "freeform_hint" not in main_payload

        print(json.dumps({"ok": True, "formats": ["xlsx", "xls", "csv"], "multi_sheet": True, "default_destination": True, "custom_destination_preserved": True, "task_navigation": True, "base_link_displayed": True, "sub_navigation_removed": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
