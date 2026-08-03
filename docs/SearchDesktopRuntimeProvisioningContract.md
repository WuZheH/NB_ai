# Search Desktop Runtime 持久配置与启动契约

日期：2026-07-20
适用分支：`codex/search-canonical-root-migration`

## 1. Candidate4 正式失败与 smoke 遮蔽

Candidate4 的 PDF `first_preview` 修复已经通过真实 packaged renderer 连续 10/10 验证；正式切换失败点不在 PDF。真实 `Search Desktop` 计划任务启动后，Electron window、renderer 与 tray 正常创建，但 FastAPI 8000 和 MCP 8787 在 180 秒内均未就绪。

已推送源码与正式失败证据共同证明了部署契约缺口：Candidate4 package 和正式副本都没有 adjacent `search-desktop.local.json`，旧实现因此回退到 PATH 和 `%LOCALAPPDATA%\Search\data`；计划任务没有独立 smoke 所设置的 `SEARCH_DATA_DIR`、`SEARCH_PYTHON`、`SEARCH_NODE` 与旧数据项目变量。旧 smoke 还在 Electron 之前预启动 Runtime，因而没有覆盖 `Electron -> launcher -> supervisor -> FastAPI/MCP` 的正式所有权链。旧 `ensureReady()` catch 只更新内存状态并把 `runtime_checked` 记为普通完成，startup log 没有稳定 prerequisite 错误码。

本契约不重新修改 PDF 状态机。它消除 smoke 与正式入口之间的 Runtime provisioning 差异。

## 2. machine-local 文件职责

两个文件均位于 Electron `app.getPath("userData")`，但职责严格分离：

- `machine-config.json`：embedding/reranker 模型位置；
- `desktop-runtime.json`：production data、Python、Node 等 Desktop Runtime prerequisite。

`desktop-runtime.json` schema 1：

```json
{
  "schemaVersion": 1,
  "dataDir": "D:\\LEARNING\\Tools\\search\\data",
  "pythonExe": "D:\\LEARNING\\Tools\\ANACONDA\\envs\\NOTEBOOK_AI\\python.exe",
  "nodeExe": "D:\\LEARNING\\Tools\\node.js\\node.exe"
}
```

该文件禁止模型路径、密钥、token、Tunnel credential、Candidate/smoke/package 路径、旧 `notebook_ai` source root 和 production data 内容。正式文件只能在 Candidate5 独立 smoke 通过后通过官方工具原子写入 `%APPDATA%\Search\desktop-runtime.json`。

## 3. packaged 解析优先级

packaged production 的唯一正常事实源是：

1. `app.getPath("userData")\desktop-runtime.json`；
2. Search.exe adjacent、经 schema 2/3 和真实路径验证的 `search-desktop.local.json`，仅作迁移兼容，并显式报告 `legacy_sidecar_used`；
3. 两者都缺失时返回 `desktop_runtime_config_missing`。

userData 文件一旦存在，无论有效与否都不会回退 legacy sidecar。packaged 模式不读取 ambient `SEARCH_DATA_DIR`、`SEARCH_PYTHON`、`SEARCH_NODE`、旧数据项目变量或 PATH 来补全 prerequisite，不创建 `%LOCALAPPDATA%\Search\data`。development 模式仍可使用明确的开发环境设置。

Candidate package 不包含 `desktop-runtime.json` 或真实本机绝对路径。legacy sidecar 不再是 Candidate5 正式事实源。

## 4. 验证状态与失败行为

配置模块提供以下稳定状态：

- `desktop_runtime_config_missing`
- `desktop_runtime_config_invalid_json`
- `desktop_runtime_schema_unsupported`
- `desktop_runtime_required_field_missing`
- `desktop_runtime_path_not_absolute`
- `desktop_runtime_data_dir_missing`
- `desktop_runtime_python_missing`
- `desktop_runtime_node_missing`
- `desktop_runtime_ready`

路径必须绝对；dataDir 必须是既存目录，且不得位于旧 `notebook_ai`、Candidate、smoke 或 packaged output；Python/Node 必须是既存文件，并分别通过真实 `--version` 探测。无效配置不 spawn launcher，不读取错误数据根，不无限重试。窗口、renderer 与 tray 仍完成启动，Runtime 状态通过 IPC 明确显示为 unavailable。

有效配置由 Electron 显式传入 launcher 子进程；launcher 再启动 supervisor、FastAPI 与 MCP。由本次 Electron 启动的 Runtime 标记为 `managed-by-search`，托盘“完全退出”调用 `stopIfOwned()`，不会停止复用的外部 Runtime。

## 5. 官方配置工具

入口：

- `scripts/configure_search_desktop_runtime.ps1`
- `scripts/configure_search_desktop_runtime.mjs`

支持 `inspect`、`validate`、`set`、`backup` 和 `migrate-legacy`。写入使用同目录临时文件、flush 和 rename；覆盖有效配置前生成 `.bak`。未知 schema 或其他无效既有文件不会被覆盖。legacy migration 在 userData 目录生成 `desktop-runtime.legacy-sidecar.bak.json`，不修改 package 中的原 sidecar。

工具不修改 PATH、环境变量、注册表或 production data，不下载或移动 Python、Node、模型及其他依赖。输出只包含状态、basename、path hash 和配置文件 hash。

## 6. startup log 可观测性与隐私

`runtime_checked` 记录开始、config source/schema、实际 `runtime_available`、`data_available`、缺失 prerequisite、是否 spawn launcher、是否由 Desktop 启动 Runtime和 Runtime owner。失败使用：

```text
event=stage_failed
stage=runtime_checked
result=failed
error_code=<stable code>
```

失败不再伪装成 `stage_completed`。日志不写完整 Python、Node、data、userData、resources 或模型路径，不写配置内容、用户名、密钥或 token；只允许 basename、redacted identity 与 SHA-256 path hash。startup fatal error 同样只记录稳定 error code，不记录 stack。

## 7. packaged smoke 契约

正式等价 smoke 创建隔离 user-data，并用官方工具写入其中的 `desktop-runtime.json`。它清除所有 ambient Search/NOTEBOOK_AI prerequisite 设置，不预启动 Runtime，直接从 package 工作目录启动 Search.exe。Electron 必须实际 spawn launcher，最终得到 FastAPI 8000、MCP 8787、`runtime_owner=managed-by-search`、`config_source=user_data` 和 `desktop_runtime_status=desktop_runtime_ready`。

退出必须通过真实托盘“完全退出”，然后证明 package Runtime 的 supervisor/Python/Node、FastAPI/MCP 和端口全部归零。成功路径禁止 `taskkill`、`Stop-Process` 或其他强制清理。

另有 packaged 行为验证覆盖无配置、invalid config、valid config 和 legacy migration。无配置与 invalid config 必须保留 UI/tray、记录稳定失败且不 spawn；valid config 必须覆盖完整搜索、MCP、PDF、Workspace、Evidence Basket；migration 必须保留备份并让 userData 配置优先。

## 8. 回归证据

源码行为测试覆盖：所有状态分类、中文和空格路径、绝对路径、目录/可执行文件验证、禁止数据根、userData 优先级、ambient/PATH/LOCALAPPDATA fallback 禁止、legacy schema 3、原子写入、备份、unknown schema 防覆盖、结构化 coordinator 状态、launcher spawn、managed ownership、owned Runtime graceful stop、startup stable error code、路径脱敏和 package 不携带本机配置。

Candidate5 source 提交前还必须完成 Core、Frontend、Desktop、MCP、Python compile、Vite/MCP build、packaged source、machine-config、desktop-runtime、launcher/supervisor、PDF/Workspace/Evidence、lockfile、旧根引用、test-only bypass 和 production data 全量守卫。实际结果在 source commit 与最终切换报告中记录。

## 9. production data 边界

本修复只读取 dataDir 元数据并把已验证路径传给 Runtime，不创建、复制、移动或改写 production data。受保护基线为 191 文件、670,300,309 字节，逐文件 full-tree exact match；41 个 SQLite 数据库必须全部 `integrity_check=ok`、`foreign_key_check=[]`，FTS 11,803、passage 11,373、object 35、legacy 6,114、Zotero 161，WAL/SHM 0。
