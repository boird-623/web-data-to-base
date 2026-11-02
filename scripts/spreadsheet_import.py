#!/usr/bin/env python3
"""Safely inspect XLSX/XLS/CSV downloads and build deterministic Base import batches.

The script never executes macros, formulas, external links, or workbook code. XLSX is
opened read-only with cached formula results; XLS is read as static cell values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import unicodedata
import zipfile
from collections import Counter
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

VENDOR = Path(__file__).resolve().parent / "vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

SUPPORTED = {".xlsx", ".xls", ".csv"}
SUMMARY_RE = re.compile(r"^(合计|总计|小计|累计|subtotal|total|grand\s*total)\b", re.I)
TEMP_SUFFIXES = {".crdownload", ".part", ".download", ".tmp"}
SYSTEM_FIELDS = ["采集账号", "采集任务", "采集时间", "来源网址", "来源文件", "来源工作表", "导入批次"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return str(value).strip()


def safe_name(value: str, fallback: str) -> str:
    value = re.sub(r"[\\/:*?\[\]<>|\x00-\x1f]+", "-", value).strip(" .-")
    return (value or fallback)[:100]


def normalize_header(value: Any, index: int) -> str:
    name = unicodedata.normalize("NFKC", text(value)).strip()
    name = re.sub(r"\s+", " ", name)
    return name or f"未命名字段{index}"


def dedupe_headers(values: list[Any]) -> tuple[list[str], list[str]]:
    headers: list[str] = []
    warnings: list[str] = []
    seen: Counter[str] = Counter()
    for index, value in enumerate(values, 1):
        base = normalize_header(value, index)
        seen[base] += 1
        name = base if seen[base] == 1 else f"{base}_{seen[base]}"
        if seen[base] > 1:
            warnings.append(f"重复表头“{base}”已重命名为“{name}”")
        headers.append(name)
    return headers, warnings


def is_empty_row(row: Iterable[Any]) -> bool:
    return not any(text(value) for value in row)


def trim_row(row: list[Any], width: int) -> list[Any]:
    return (row + [None] * width)[:width]


def header_score(row: list[Any], following: list[list[Any]]) -> float:
    values = [text(x) for x in row]
    nonempty = [x for x in values if x]
    if not nonempty:
        return -1000
    textish = sum(not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", x) for x in nonempty)
    unique = len(set(nonempty)) / len(nonempty)
    density = len(nonempty) / max(1, len(values))
    next_density = 0.0
    if following:
        next_density = sum(sum(bool(text(v)) for v in r) / max(1, len(values)) for r in following[:3]) / min(3, len(following))
    return textish * 2 + unique * 3 + density * 2 + next_density


def detect_header(rows: list[list[Any]], explicit: int | None = None) -> int:
    if explicit is not None:
        if explicit < 1 or explicit > len(rows):
            raise ValueError("header_row_out_of_range")
        return explicit - 1
    limit = min(20, len(rows))
    candidates = [(header_score(rows[i], rows[i + 1 : i + 4]), i) for i in range(limit) if not is_empty_row(rows[i])]
    if not candidates:
        raise ValueError("no_nonempty_rows")
    return max(candidates)[1]


def is_summary_row(row: dict[str, Any]) -> bool:
    first = next((text(v) for v in row.values() if text(v)), "")
    return bool(SUMMARY_RE.search(first))


def infer_kind(values: list[Any]) -> str:
    usable = [v for v in values if v not in (None, "") and text(v) != ""]
    if not usable:
        return "text"
    if all(isinstance(v, bool) for v in usable):
        return "checkbox"
    if all(isinstance(v, (datetime, date)) and not isinstance(v, bool) for v in usable):
        return "datetime"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) for v in usable):
        return "number"
    return "text"


def json_value(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "datetime" and isinstance(value, (datetime, date)):
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return value.strftime("%Y-%m-%d 00:00:00")
    if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if kind == "checkbox" and isinstance(value, bool):
        return value
    return text(value)


def restore_zero_format(value: Any, number_format: str | None) -> Any:
    """Preserve Excel identifiers formatted only with zero placeholders (e.g. 000000)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not number_format:
        return value
    fmt = number_format.strip()
    if re.fullmatch(r"0+", fmt) and float(value).is_integer():
        return str(int(value)).zfill(len(fmt))
    return value


def read_xlsx(path: Path) -> list[tuple[str, list[list[Any]]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl_dependency_missing") from exc
    if not zipfile.is_zipfile(path):
        raise ValueError("xlsx_magic_invalid")
    workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    result: list[tuple[str, list[list[Any]]]] = []
    try:
        for ws in workbook.worksheets:
            rows: list[list[Any]] = []
            for excel_row in ws.iter_rows():
                values = [restore_zero_format(cell.value, getattr(cell, "number_format", None)) for cell in excel_row]
                while values and values[-1] is None:
                    values.pop()
                rows.append(values)
            result.append((ws.title, rows))
    finally:
        workbook.close()
    return result


def read_xls(path: Path) -> list[tuple[str, list[list[Any]]]]:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError("xlrd_dependency_missing") from exc
    if path.read_bytes()[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise ValueError("xls_magic_invalid")
    book = xlrd.open_workbook(str(path), on_demand=True, formatting_info=True)
    result: list[tuple[str, list[list[Any]]]] = []
    try:
        for sheet_name in book.sheet_names():
            sh = book.sheet_by_name(sheet_name)
            rows: list[list[Any]] = []
            for r in range(sh.nrows):
                values: list[Any] = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    value: Any = cell.value
                    if cell.ctype == xlrd.XL_CELL_EMPTY:
                        value = None
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(value)
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        value = xlrd.xldate.xldate_as_datetime(value, book.datemode)
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        fmt = None
                        try:
                            xf = book.xf_list[cell.xf_index]
                            fmt = book.format_map[xf.format_key].format_str
                        except Exception:
                            pass
                        value = restore_zero_format(value, fmt)
                        if isinstance(value, float) and value.is_integer():
                            value = int(value)
                    values.append(value)
                while values and values[-1] is None:
                    values.pop()
                rows.append(values)
            result.append((sheet_name, rows))
            book.unload_sheet(sheet_name)
    finally:
        book.release_resources()
    return result


def detect_csv_encoding(path: Path) -> str:
    raw = path.read_bytes()[:65536]
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("csv_encoding_unsupported")


def read_csv_file(path: Path) -> list[tuple[str, list[list[Any]]]]:
    encoding = detect_csv_encoding(path)
    with path.open("r", encoding=encoding, newline="") as handle:
        sample = handle.read(65536)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        rows = [list(row) for row in csv.reader(handle, dialect)]
    return [(path.stem or "CSV", rows)]


def load_source(path: Path) -> list[tuple[str, list[list[Any]]]]:
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED:
        raise ValueError("unsupported_file_type")
    if suffix == ".xlsx":
        return read_xlsx(path)
    if suffix == ".xls":
        return read_xls(path)
    return read_csv_file(path)


def parse_sheet(sheet_name: str, rows: list[list[Any]], header_row: int | None, include_summaries: bool) -> dict[str, Any]:
    while rows and is_empty_row(rows[-1]):
        rows.pop()
    if not rows:
        return {"sheet_name": sheet_name, "status": "empty", "rows": [], "warnings": ["工作表为空"]}
    header_index = detect_header(rows, header_row)
    width = max(len(rows[header_index]), max((len(r) for r in rows[header_index + 1 :]), default=0))
    headers, warnings = dedupe_headers(trim_row(rows[header_index], width))
    original_header_signature = [normalize_header(v, i + 1) for i, v in enumerate(trim_row(rows[header_index], width))]
    records: list[dict[str, Any]] = []
    summary_count = 0
    repeated_header_count = 0
    for source_index, raw in enumerate(rows[header_index + 1 :], header_index + 2):
        row = trim_row(raw, width)
        if is_empty_row(row):
            continue
        normalized = [normalize_header(v, i + 1) for i, v in enumerate(row)]
        if normalized == original_header_signature:
            repeated_header_count += 1
            continue
        record = {headers[i]: row[i] for i in range(width)}
        if is_summary_row(record):
            summary_count += 1
            if not include_summaries:
                continue
        record["__source_row__"] = source_index
        records.append(record)
    if repeated_header_count:
        warnings.append(f"已排除 {repeated_header_count} 行重复表头")
    if summary_count and not include_summaries:
        warnings.append(f"已排除 {summary_count} 行合计/总计/小计")
    kinds = {header: infer_kind([record.get(header) for record in records]) for header in headers}
    normalized_records = [
        {header: json_value(record.get(header), kinds[header]) for header in headers} | {"__source_row__": record["__source_row__"]}
        for record in records
    ]
    return {
        "sheet_name": sheet_name,
        "status": "ready",
        "header_row": header_index + 1,
        "headers": headers,
        "field_types": kinds,
        "source_data_rows": len(normalized_records),
        "summary_rows_excluded": summary_count if not include_summaries else 0,
        "repeated_headers_excluded": repeated_header_count,
        "warnings": warnings,
        "rows": normalized_records,
    }


def stable_fingerprint(row: dict[str, Any], fields: list[str]) -> str:
    payload = {field: row.get(field) for field in fields}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def apply_mapping(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    return {mapping.get(key, key): value for key, value in row.items() if key != "__source_row__"}


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    source = args.file.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    if source.suffix.casefold() in TEMP_SUFFIXES:
        raise ValueError("download_not_complete")
    all_sheets = load_source(source)
    selected = set(args.sheet or [])
    if selected:
        missing = sorted(selected - {name for name, _ in all_sheets})
        if missing:
            raise ValueError("sheet_not_found:" + ",".join(missing))
        all_sheets = [(name, rows) for name, rows in all_sheets if name in selected]
    header_map = json.loads(args.header_rows_json) if args.header_rows_json else {}
    mapping = json.loads(args.field_mapping_json) if args.field_mapping_json else {}
    if not isinstance(header_map, dict) or not isinstance(mapping, dict):
        raise ValueError("mapping_must_be_object")
    unique_fields = list(args.unique_field or [])
    batch_id = args.batch_id or hashlib.sha256(f"{source}:{source.stat().st_size}:{source.stat().st_mtime_ns}".encode()).hexdigest()[:20]
    parsed = [parse_sheet(name, rows, header_map.get(name), args.include_summaries) for name, rows in all_sheets]
    import_units: list[dict[str, Any]] = []
    for sheet in parsed:
        if sheet.get("status") != "ready":
            import_units.append({**sheet, "target_table": safe_name(sheet["sheet_name"], "数据")})
            continue
        mapped_headers = [mapping.get(header, header) for header in sheet["headers"]]
        if len(set(mapped_headers)) != len(mapped_headers):
            raise ValueError(f"field_mapping_collision:{sheet['sheet_name']}")
        mapped_types = {mapping.get(k, k): v for k, v in sheet["field_types"].items()}
        missing_unique = [field for field in unique_fields if field not in mapped_headers]
        if missing_unique:
            raise ValueError(f"unique_fields_missing:{sheet['sheet_name']}:{','.join(missing_unique)}")
        records = [apply_mapping(row, mapping) for row in sheet.pop("rows")]
        if not unique_fields and args.content_fingerprint:
            key_fields = mapped_headers
            for record in records:
                record["内容指纹"] = stable_fingerprint(record, key_fields)
            mapped_headers.append("内容指纹")
            mapped_types["内容指纹"] = "text"
        if args.write_mode == "新增或更新" and not unique_fields and not args.content_fingerprint:
            sheet["warnings"].append("新增或更新缺少唯一字段；正式写入前必须让用户选择唯一字段，或明确采用内容指纹")
            sheet["requires_unique_field_confirmation"] = True
        source_fields = {
            "采集账号": args.account_label or "无需登录",
            "采集任务": args.task_name,
            "采集时间": args.collected_at or now_iso(),
            "来源网址": args.source_url,
            "来源文件": source.name,
            "来源工作表": sheet["sheet_name"],
            "导入批次": batch_id,
        }
        for record in records:
            record.update(source_fields)
        preview = records[:1]
        effective = preview if args.mode == "trial" else records
        target_table = safe_name(args.target_table or sheet["sheet_name"], "数据")
        import_units.append({
            **sheet,
            "headers": mapped_headers + [f for f in SYSTEM_FIELDS if f not in mapped_headers],
            "field_types": mapped_types | {f: ("datetime" if f == "采集时间" else "text") for f in SYSTEM_FIELDS},
            "unique_fields": unique_fields or (["内容指纹"] if args.content_fingerprint else []),
            "write_mode": args.write_mode,
            "target_table": target_table,
            "trial_preview": preview,
            "records": effective,
            "record_count": len(effective),
            "full_record_count": len(records),
        })
    return {
        "schema_version": 1,
        "collection_mode": "download_file_import",
        "source": {
            "path": str(source),
            "name": source.name,
            "format": source.suffix.casefold().lstrip("."),
            "size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "batch": {"id": batch_id, "mode": args.mode, "created_at": now_iso()},
        "target": {
            "confirmed": args.target_confirmed,
            "base": args.target_base,
            "table_strategy": args.table_strategy,
        },
        "units": import_units,
        "unit_count": len(import_units),
    }


def write_batches(manifest: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "import_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = []
    for index, unit in enumerate(manifest["units"], 1):
        if unit.get("status") != "ready":
            continue
        path = output_dir / f"{index:03d}-{safe_name(unit['sheet_name'], 'sheet')}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in unit["records"]:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        files.append({"sheet_name": unit["sheet_name"], "path": str(path), "records": unit["record_count"]})
    return {"manifest": str(manifest_path), "batch_files": files}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["inspect", "prepare"])
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--mode", choices=["trial", "run"], default="trial")
    p.add_argument("--sheet", action="append", help="Repeat to select sheets; omit for all")
    p.add_argument("--header-rows-json", help='Object such as {"Sheet1":2}; omit for automatic detection')
    p.add_argument("--field-mapping-json", help='Object mapping source headers to target fields')
    p.add_argument("--unique-field", action="append")
    p.add_argument("--content-fingerprint", action="store_true")
    p.add_argument("--write-mode", choices=["新增", "新增或更新"], default="新增或更新")
    p.add_argument("--include-summaries", action="store_true")
    p.add_argument("--task-name", default="文件导入")
    p.add_argument("--account-label", default="")
    p.add_argument("--source-url", default="")
    p.add_argument("--collected-at", default="")
    p.add_argument("--batch-id", default="")
    p.add_argument("--target-confirmed", action="store_true")
    p.add_argument("--target-base", default="")
    p.add_argument("--target-table", default="")
    p.add_argument("--table-strategy", choices=["每个工作表新建数据表", "逐表指定", "兼容工作表合并"], default="每个工作表新建数据表")
    p.add_argument("--output-dir", type=Path)
    return p


def main() -> int:
    args = parser().parse_args()
    if args.action == "prepare" and not args.output_dir:
        raise SystemExit("output_dir_required_for_prepare")
    manifest = build_manifest(args)
    if args.action == "inspect":
        safe = json.loads(json.dumps(manifest, ensure_ascii=False))
        for unit in safe["units"]:
            unit.pop("records", None)
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 0
    result = write_batches(manifest, args.output_dir.expanduser().resolve())
    print(json.dumps({"prepared": True, **result, "unit_count": manifest["unit_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, FileNotFoundError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
