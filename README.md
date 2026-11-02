# web-data-to-base

跨平台网页数据采集与飞书多维表格同步的 Agent Skill：把「打开网页 → 登录 → 采集/导出数据 → 写入多维表格」整条链路，做成可配置、可断点续跑、可回放、可持续学习的自动化管线。

## 核心特性

- **双模式登录**：账号密码目标（凭据只进系统安全存储，绝不进入模型上下文、文件或日志）与扫码目标（运行时交接）。
- **数字快捷导航**：1 配置网页目标与账号 → 2 配置采集任务与保存位置 → 3 试运行（每目标只采 1 条）→ 4 正式运行。
- **两种采集模式**：页面采集；下载文件导入（XLSX / XLS / CSV，多工作表路由、表头自动识别、汇总行排除、前导零格式保留、字段类型推断）。
- **三面架构**：控制面（统一多维表格「网页数据采集结果」）/ 秘密面（系统钥匙串、Windows DPAPI、Linux Secret Service）/ 经验面（本机非敏感配方与异常经验库）。
- **统一执行计划**：跨网站 × 任务展开为连续队列，阶段状态机 login → navigate → filter → download → parse_file → write → verify → logout，支持 block / resume 断点续跑。
- **语义配方回放**：只记录非敏感语义动作（可访问名称、角色、动态来源标记），不录坐标、不存 DOM、不存任何凭据；单步失效只重学该步。
- **异常经验库**：iframe 自愈等坑与解法按指纹归档，回放时按成功率排序优先执行历史验证过的解法。
- **持续学习**：三层记忆（凭据 / 操作路线 / 任务需求），操作配方 + 学习日志 + 预置文件沉淀，越用越省事。

## 内置电商四平台预置配方

| 平台 | 登录 | 拉取内容 | 频率 |
|---|---|---|---|
| 海豚智投 | 账号密码（可全自动登录） | 直播场次报表 | 每日 |
| 微信小店电商罗盘 | 微信扫码 | 多店巡检（店铺明细 + 商品明细） | 每日 |
| 腾讯广告 ADQ 客户工作台 | 微信扫码 | 多账户自定义列报表 | 每日 |
| 微信小店单店主后台 | 微信扫码 | 财务三块（资金 / 订单 / 涉税） | 每月 |

预置含罗盘「全选要点两次」、ADQ 自定义列口径、微信小店主账号与多店 API 边界等一线实操结论，详见 references/ecommerce-platforms-preset.md。

## 目录结构

```
├── SKILL.md                          # 主文档：架构、导航、安全边界、运行流程
├── config/                           # 非敏感配置模板（账号清单 / Base 布局 / 任务）
├── references/
│   ├── base_schema.md                # 多维表格字段蓝图
│   ├── ecommerce-platforms-preset.md # 电商四平台预置配方
│   └── spreadsheet_import.md         # 下载表格导入规范
└── scripts/                          # 20 个可执行脚本
```

| 脚本 | 作用 |
|---|---|
| initialize_config.py | 初始化配置缓存并播种电商四平台 |
| navigation.py | 数字导航与任务保存位置展示 |
| configure_account.py | 一次性 localhost 批量配置页（秘密直达系统密钥库） |
| secure_credentials.py | 跨平台凭据库（Keychain / DPAPI / Secret Service） |
| account_sync.py | 多维表格双向同步与回读校验 |
| update_account_catalog.py | 从 JSON 刷新本地账号缓存 |
| validate_config.py | 配置与凭据引用校验 |
| base_layout.py | Base 布局管理与按主机分表路由 |
| upsert_task.py | 任务创建/更新与保存位置规范化 |
| spreadsheet_import.py | XLSX/XLS/CSV 安全解析与导入批次生成 |
| seed_ecommerce.py | 电商四平台目标与任务种子（幂等） |
| batch_state.py | 统一执行计划与断点游标 |
| operation_recipe.py | 语义操作配方与异常经验库 |
| learnings.py | 学习日志 |
| update_runtime.py | 更新试运行状态 |
| state_io.py | 原子 JSON 读写 |
| setup_linux_keyring.sh（及 _gui 版） | Linux 密钥库引导 |
| test_account_sync.py / test_spreadsheet_import.py | 自检脚本 |

## 安全设计

- 密码、验证码、Cookie、Token、会话数据绝不进入模型上下文、普通文件、命令行参数、工具回显、日志、截图或回复。
- 登录动作以 fill_account / fill_secret_from_store 的动态来源标记记录：配方里只有「从系统密钥库读取」，没有值。
- 多维表格中的「账号密码本」是非敏感控制表（密钥引用 ID + 密码状态），代码强制禁止创建密码 / 验证码 / Cookie 等字段。
- 状态文件同目录临时文件 + 原子替换，权限收紧为仅当前用户可读写。

## 依赖与运行

- Python 3.10+。
- 表格解析依赖 openpyxl / xlrd / xlwt（pip 安装即可；私有部署随包携带 vendor 目录，本公开仓库不含）。
- 本 Skill 运行于 Agent 宿主：浏览器操作依赖宿主的浏览器自动化能力，多维表格操作依赖 lark-base 能力；Linux 密钥库引导见 scripts/setup_linux_keyring.sh。
- 快速自检：python3 scripts/test_spreadsheet_import.py 与 python3 scripts/test_account_sync.py。
