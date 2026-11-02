#!/usr/bin/env python3
"""Seed the built-in e-commerce platform targets and collection tasks.

一次性内置海豚智投 / 微信小店电商罗盘 / 腾讯广告 ADQ / 微信小店单店主后台
四个平台的网页目标、网址和采集任务。幂等：只补齐缺失的种子，不覆盖用户已修改的
启用状态、执行顺序、保存位置等；账号密码类目标的密码只进系统安全存储，本脚本不写、
不读、不打印密码。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from state_io import atomic_write_json, load_json
from configure_account import make_target_ref
import secure_credentials as sc

DEFAULT_DIR = Path.home() / ".config" / "web-data-to-base"
DEFAULT_CATALOG = DEFAULT_DIR / "account_catalog.local.json"
DEFAULT_TASKS = DEFAULT_DIR / "tasks.local.json"

# 账号固定标识；扫码平台在本技能里按“无需密码目标”登记（密码不进密钥库），登录时走微信扫码交接。
HAITUN_REF = "ieh5.com_legacy-account"  # 兼容早期手工写入的海豚密码引用（公开版占位）

SEED_ACCOUNTS = [
    {
        "site_name": "海豚智投",
        "login_url": "https://www.ieh5.com/home/login",
        "username": "your-haitun-account",
        "login_mode": "account_password",
        "enabled": True,
    },
    {
        "site_name": "微信小店·电商罗盘",
        "login_url": "https://store.weixin.qq.com/compass/patrol",
        "username": "",
        "login_mode": "none",
        "enabled": True,
    },
    {
        "site_name": "腾讯广告ADQ客户工作台",
        "login_url": "https://e.qq.com/",
        "username": "",
        "login_mode": "none",
        "enabled": True,
    },
    {
        "site_name": "微信小店·单店主后台",
        "login_url": "https://store.weixin.qq.com/",
        "username": "",
        "login_mode": "none",
        "enabled": True,
    },
]

SEED_TASKS = [
    {
        "任务名称": "海豚智投-直播场次",
        "网页": "https://www.ieh5.com/home/live/launch-data/launch-data",
        "采集模式": "下载文件导入",
        "写入方式": "新增或更新",
        "是否启用": True,
        "执行顺序": 1,
        "操作需求": (
            "每天拉取前一天的「直播场次」报表。步骤：1) 用账号密码自动登录（账号为种子预置占位账号，"
            "密码从系统密钥库读取）；2) 直播间→直播投流→投放数据→切到顶部「直播场次」标签；"
            "3) 日期选择器点「昨天」；4) 点「导出」，弹窗内日期默认是今天，必须单独点弹窗日历改选「昨天」再确定；"
            "5) 点蓝色「导出」仅生成导出任务，不会直接下载；6) 切到弹窗顶部「导出记录」标签，点最新一条"
            "「生成成功」的「下载」；7) 得到「直播场次_YYYYMMDD.xlsx」，每场1行约39列。下载后解析并保留"
            "消耗、成交金额、整体ROI、成交订单数、利润、净利润等关键字段。"
        ),
    },
    {
        "任务名称": "微信小店电商罗盘-多店巡检",
        "网页": "https://store.weixin.qq.com/compass/patrol",
        "采集模式": "下载文件导入",
        "写入方式": "新增或更新",
        "是否启用": True,
        "执行顺序": 2,
        "操作需求": (
            "每天拉取前一天的多店巡检数据，导出「店铺明细」和「商品明细」两个文件。微信扫码登录（失效则请客户扫码）。"
            "步骤：1) 打开店铺选择器（默认可能只选1家带「+N」），点「全选」——注意第一次点会变成全不选、再点一次才全选；"
            "2) 时间默认「近7天」，改「自定义」并把起止都点成昨天单日；3) 分别在店铺明细、商品明细点「筛选指标」，"
            "只保留 成交金额、成交订单数、成交退款金额、退款金额 这4项（商品明细取消成交人数/成交件数；"
            "店铺明细取消成交人数/下单金额/下单订单数/下单人数/客单价）；4) 商品明细点「下载数据」（5列：商品信息+4指标），"
            "店铺明细点「下载数据」；5) 店铺明细固定附带4个体验分列（店铺/商品/物流/服务体验分，筛选不掉），清洗时删除。"
            "用官方「下载数据」全选后一次导出，不逐店抓取。"
        ),
    },
    {
        "任务名称": "腾讯广告ADQ-客户工作台",
        "网页": "https://e.qq.com/",
        "采集模式": "下载文件导入",
        "写入方式": "新增或更新",
        "是否启用": True,
        "执行顺序": 3,
        "操作需求": (
            "每天拉取前一天全部广告账户的数据。微信扫码登录客户工作台（入口 e.qq.com 右上角「登录→我是客户」，失效则请客户扫码）。"
            "步骤：1) 全选账户（单次最多50个）；2) 顶部时间设为昨天单日；3) 点「自定义列」勾选14个字段并确定，"
            "字段为账户5项：账户(名称)、账户健康度、账户状态、账户运营方、账户是否启用；指标9项：备注、标签、余额、花费、"
            "目标转化成本、曝光次数、点击次数、下单金额、下单ROI；4) 点列表页自带的「导出」下载。"
            "口径：状态类（健康度/状态/运营方/是否启用/备注/标签/余额）取当前值；效果类（花费/目标转化成本/曝光/点击/下单金额/下单ROI）按昨天统计。"
        ),
    },
    {
        "任务名称": "微信小店-财务三块(资金/订单/涉税)",
        "网页": "https://store.weixin.qq.com/",
        "采集模式": "下载文件导入",
        "写入方式": "新增或更新",
        "是否启用": True,
        "执行顺序": 4,
        "操作需求": (
            "每月初读取上个月整月（非每日），建议每月第3-5个工作日。微信扫码登录店铺主后台 shop/home。"
            "进入资金结算→流水与账单，分别导出「资金流水」「订单流水」「涉税账单」，动账时间设为上月1日至上月末。"
            "资金流水有T+1延迟（次日16点后才有今日数据）。多店铺用右上角店铺切换器逐个切换、数据按店铺隔离，"
            "空店跳过；财务导出强制微信扫码确认，无法自动跳过。"
        ),
    },
    {
        "任务名称": "微信小店-订单管理",
        "网页": "https://store.weixin.qq.com/",
        "采集模式": "下载文件导入",
        "写入方式": "新增或更新",
        "是否启用": True,
        "执行顺序": 5,
        "操作需求": (
            "每天拉取前一天的订单。微信扫码登录店铺主后台，进入订单/配送→订单管理（全部订单），"
            "下单时间设为昨天，用「全部导出」导出订单流水后下载解析。"
        ),
    },
    {
        "任务名称": "微信小店-小店投放直播投放",
        "网页": "https://store.weixin.qq.com/",
        "采集模式": "下载文件导入",
        "写入方式": "新增或更新",
        "是否启用": True,
        "执行顺序": 6,
        "操作需求": (
            "每天拉取前一天的小店投放·直播投放数据。微信扫码登录店铺主后台，进入营销中心→小店推广→小店投放→直播投放，"
            "数据概览/投放时间设为昨天，导出或读取 花费、成交金额、实际成交ROI、成交订单数、直播间观看人数 等指标。"
        ),
    },
]


def secret_status(seed: dict, ref: str) -> str:
    if seed["login_mode"] == "none":
        return "无需密码"
    # 兼容标准引用与早期手工引用
    for candidate in (ref, HAITUN_REF):
        try:
            if sc.credential_exists(candidate):
                return "已安全保存"
        except Exception:
            continue
    return "缺失"


def seed_catalog() -> dict:
    DEFAULT_CATALOG.parent.mkdir(parents=True, exist_ok=True)
    if DEFAULT_CATALOG.exists():
        data = load_json(DEFAULT_CATALOG)
    else:
        data = {
            "账号清单多维表格": {"名称": "网页数据采集结果", "网址": "", "数据表": "账号密码本"},
            "账号列表": [],
        }
    rows = data.setdefault("账号列表", [])
    by_ref = {str(r.get("密钥引用ID")): r for r in rows if isinstance(r, dict)}
    added, refreshed = [], []
    for index, seed in enumerate(SEED_ACCOUNTS, start=1):
        ref = make_target_ref(seed["site_name"], seed["login_url"], seed["username"], seed["login_mode"])
        status = secret_status(seed, ref)
        if ref in by_ref:
            # 只刷新密码状态，不覆盖用户已改的启用/顺序等
            if by_ref[ref].get("密码状态") != status:
                by_ref[ref]["密码状态"] = status
                refreshed.append(seed["site_name"])
            continue
        rows.append({
            "网站名称": seed["site_name"],
            "登录网址": seed["login_url"],
            "账号标识": seed["username"] if seed["login_mode"] == "account_password" else "无需登录",
            "登录方式": seed["login_mode"],
            "密钥引用ID": ref,
            "密码状态": status,
            "是否启用": seed["enabled"],
            "执行顺序": index,
        })
        added.append(seed["site_name"])
    rows.sort(key=lambda r: int(r.get("执行顺序", 999999)))
    atomic_write_json(DEFAULT_CATALOG, data)
    return {"added": added, "refreshed": refreshed, "total": len(rows)}


def upsert_tasks() -> dict:
    import subprocess
    upsert = Path(__file__).resolve().parent / "upsert_task.py"
    created, updated, failed = [], [], []
    for task in SEED_TASKS:
        proc = subprocess.run(
            [sys.executable, str(upsert), "--json", json.dumps(task, ensure_ascii=False)],
            capture_output=True, text=True,
        )
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out = {}
        if out.get("updated"):
            (created if out.get("action") == "created" else updated).append(task["任务名称"])
        else:
            failed.append({"任务名称": task["任务名称"], "error": out.get("errors", proc.stdout or proc.stderr)})
    return {"created": created, "updated": updated, "failed": failed}


def main() -> int:
    catalog_result = seed_catalog()
    tasks_result = upsert_tasks()
    # 海豚密码提示（不打印密码）
    haitun = next(s for s in SEED_ACCOUNTS if s["login_mode"] == "account_password")
    haitun_ref = make_target_ref(haitun["site_name"], haitun["login_url"], haitun["username"], "account_password")
    haitun_ready = secret_status(haitun, haitun_ref) == "已安全保存"
    result = {
        "seeded": True,
        "账号目标": catalog_result,
        "采集任务": tasks_result,
        "海豚密码就绪": haitun_ready,
        "提示": (
            "海豚密码已在本机安全存储" if haitun_ready
            else "海豚账号已内置但密码尚未在本机配置；首次运行海豚前请在配置页输入一次密码（仅存系统密钥库，不写入任何文件）"
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not tasks_result["failed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
