# Search

## 1. 项目简介

Search 是一款 Windows 本地科研资料检索桌面应用。它把本地 PDF、Zotero 标注与笔记、证据检索、PDF 原文定位与高光、证据篮子和 Research Workspace 放在同一套工作流中。

Search 采用本地优先设计：基础检索不要求把论文、数据库或笔记上传到 GitHub，也不要求连接外部大模型。Codex MCP、Zotero 插件和 ChatGPT App 都是可选集成；其中只有 ChatGPT App 需要 HTTPS Tunnel。

仓库只发布源码、配置示例和构建说明，不包含用户的生产数据库、PDF、Zotero snapshot、FTS/LanceDB 索引、模型权重或凭据。

## 2. 功能

当前已实现并有契约测试覆盖的功能：

- 左侧只有一个用户可见的“搜索”入口；旧搜索路由会重定向到统一页面。
- 统一搜索页支持高质量检索和 FTS5/BM25 关键词检索，并提供来源、文档、年份、上下文和去重筛选。
- 支持本地 PDF chunk、Zotero 高光、标注评论、子笔记、灵感笔记和本地笔记的统一结果模型。
- 搜索结果、证据篮子和成熟的 `PdfLocationPreview` 位于同一页面。
- PDF Preview 支持页码定位、exact 文本高光、独立滚动和结果切换。
- `/retrieval → /workspace → /retrieval` 返回后保留查询、模式、筛选、结果、预览页码、高光、证据篮子和滚动状态。
- 证据可按 Markdown、JSON 或 JSONL 导出，并保留来源和稳定 fragment ID。
- Search 桌面端可静默拉起本地 FastAPI `8000` 和 MCP `8787`，自动化测试使用隐藏窗口模式。
- 无生产数据时应用仍可启动，并显示“资料库为空”及导入/配置提示。
- MCP 暴露只读工具 `search`、`fetch`、`export_evidence`。

高质量检索需要本地模型、向量索引和可选模型依赖。缺失时会明确报告不可用，不会静默伪装为同等质量的关键词结果。

## 3. 系统环境

当前实际验证环境：

- 平台：Windows x64，已验证系统版本 `10.0.26200`；Windows 是当前主要验证平台。
- Python：CPython `3.11.15`；SQLite `3.51.2`。
- Conda：不是运行时硬性依赖，但当前验证环境使用非 base Conda 环境；项目内 `.venv` 也可使用。
- Node.js：`24.15.0`；npm `11.4.2`。
- Electron：`37.2.6`；electron-builder：`26.0.12`。
- Vite：lockfile 实际解析为 `7.3.3`。
- React：`19.2.6`；PDF.js：`5.7.284`。
- FastAPI：`0.136.1`；Uvicorn：`0.47.0`；SQLAlchemy：`2.0.49`。
- Zotero 插件 manifest 兼容 `9.0`–`9.9.9`；历史手工验证版本为 `9.0.4`。
- cloudflared：可选；当前本机审计版本为 `2026.7.2`，基础 Search 不需要它。
- Git：只在克隆、开发、提交和可重复构建时需要，预构建版运行不需要 Git。

资源建议不是启动硬门槛：基础关键词检索建议至少 8 GB 内存和 4 GB 可用磁盘；加载本地 embedding/reranker 或 OCR 模型建议至少 16 GB 内存，并为依赖、模型和索引预留 10 GB 以上空间。实际需求随语料和模型而变化。

## 4. 安装方式

### 方式 A：使用预构建 Search

GitHub Release `0.1.4` 发布后：

1. 下载 `Search-0.1.4-windows-x64.zip` 并解压到普通用户可写目录。
2. 安装 Node.js 24.x 和 Python 3.11；预构建包包含应用代码和 MCP bundle，但不包含 Python/Node 可执行文件。
3. 在解压目录根据附带的 `environment.yml` 创建 Python 环境：

```powershell
conda env create -f .\environment.yml
conda activate search
$env:SEARCH_PYTHON = (Get-Command python.exe).Source
$env:SEARCH_NODE = (Get-Command node.exe).Source
```

4. 可选设置独立数据目录，然后启动：

```powershell
$env:SEARCH_DATA_DIR = Join-Path $env:LOCALAPPDATA "Search\data"
.\Search.exe
```

首次启动没有数据库时会显示空资料库状态。预构建版不会附带论文、索引或模型，也不会自动下载它们。

### 方式 B：从源码安装

以下命令以 PowerShell 为例：

```powershell
git clone --branch 0.1.4 --depth 1 https://github.com/WuZheH/NB_ai.git Search
Set-Location .\Search
conda env create -f .\environment.yml
conda activate search
$env:SEARCH_PYTHON = (Get-Command python.exe).Source
$env:SEARCH_NODE = (Get-Command node.exe).Source
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -CheckOnly
```

若需要由项目脚本安装锁定依赖，必须先激活非 base Conda 环境或使用项目内 `.venv`，然后显式执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -Install
```

等价的 Node 恢复命令如下；全部使用已提交的 `package-lock.json`：

```powershell
npm --prefix .\frontend ci --cache .\.codex_tmp\npm-cache --no-audit --no-fund
npm --prefix .\integrations\search_desktop ci --cache .\.codex_tmp\npm-cache --no-audit --no-fund
npm --prefix .\integrations\notebook_ai_chatgpt_app ci --cache .\.codex_tmp\npm-cache --no-audit --no-fund
npm --prefix .\packages\search-design-system ci --cache .\.codex_tmp\npm-cache --no-audit --no-fund
```

构建并启动开发版：

```powershell
npm --prefix .\frontend run build
npm --prefix .\integrations\notebook_ai_chatgpt_app run build
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_dev.ps1
```

脚本只使用当前进程环境，不修改系统 PATH、注册表、防火墙或计划任务。

## 5. 如何使用

1. 启动 `Search.exe` 或运行 `scripts/start_dev.ps1`。
2. 首次使用从左侧“导入 PDF”进入导入预检；确认前不会写入核心数据库。
3. 打开左侧唯一的“搜索”页面。
4. 选择“高质量搜索”或“关键词搜索”；再设置来源、文档、年份、上下文和去重选项。
5. 输入查询并查看统一结果列表；技术 ID 和原始分值默认收纳在折叠详情中。
6. 选择结果后，在同页右侧使用 PDF Preview 查看定位页和 exact 高光。
7. 将需要的结果加入证据篮子，调整顺序和导出选项。
8. 进入 Research Workspace 继续整理证据。
9. 点击“返回搜索”回到 `/retrieval`；查询、筛选、当前结果、Preview 和证据篮子应保持。
10. 从左侧“系统状态”查看 FastAPI、MCP、Zotero 和 ChatGPT Tunnel 的独立状态。

## 6. Zotero 插件

基础 Search 不要求安装 Zotero 插件。插件名为 Search Inspiration，manifest 支持 Zotero `9.0`–`9.9.9`，手工验证基线为 `9.0.4`。

XPI 安装步骤：

1. 将 `zotero-plugin/` 目录内容压缩，保证 `manifest.json` 位于压缩包根目录，并将扩展名改为 `.xpi`。
2. 在 Zotero 中打开“工具 / Plugins”，点击齿轮，选择“Install Plugin From File...”。
3. 选择 XPI；按 Zotero 提示重启。
4. 在 Search 已启动时检查后端同步状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/zotero/inspiration-notes/sync-status
```

当前选区采集仍属于谨慎使用的 MVP：请先在可丢弃测试 PDF 上选择文本，调用插件的 `captureSelectionWithPromptFallback()`，输入测试笔记/标签，并核对 loopback 回执和本地列表。已验证字段包括 item key、attachment key、页码、page label、selected text 和 bbox；annotation key 在部分选区可为空。Reader 内嵌弹窗不可用时会回退到 prompt，未验证的跳转不会伪造成功。

插件只连接 loopback 后端；不会把 Zotero 数据库或凭据提交到 Git。基础 PDF 搜索不依赖插件。

## 7. Codex MCP

Search 启动并显示本地后端就绪后，MCP 地址为：

```text
http://127.0.0.1:8787/mcp
```

在 Codex 的 MCP 设置中添加该 HTTP URL，然后重新检查连接。Search 不会自动打开或修改 Codex。已实现工具：

- `search`：按查询和来源筛选本地证据。
- `fetch`：按稳定 fragment ID 读取完整证据与 provenance。
- `export_evidence`：按 ID 导出 Markdown、JSON 或 JSONL 证据包。

Codex 与 Zotero 都使用本地 8000/8787 链路，不需要 Cloudflare Tunnel。

## 8. ChatGPT App

ChatGPT 无法直接访问 `127.0.0.1`，因此需要以下外部链路：

```text
ChatGPT → HTTPS Tunnel → 127.0.0.1:8787/mcp
```

临时 Quick Tunnel 仅用于开发测试。先检查，再显式启动：

```powershell
$env:SEARCH_CLOUDFLARED = "C:\Tools\cloudflared.exe"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_quick_tunnel.ps1 -Check
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_quick_tunnel.ps1
```

脚本会隐藏启动 cloudflared，并且只有公网健康检查通过后才输出形如 `https://<随机子域>.trycloudflare.com/mcp` 的精确 URL。它不会修改 ChatGPT App，也不会把凭据写入 Git。Quick Tunnel URL 会变化，旧 URL 失效时 `@search` 可能返回 `mcp_network_error`。

在 ChatGPT App 的 MCP 配置中手动粘贴脚本输出的 `/mcp` URL。固定地址需要 Cloudflare 账户、域名、named tunnel、credentials 和公网认证方案；匿名开发 MCP 不应直接作为长期公网服务。credentials、`cert.pem` 和本地配置均被 `.gitignore` 排除。

当前源码发布状态为：

```text
PENDING_CHATGPT_TUNNEL_CONFIGURATION
```

只有在 ChatGPT 中真实执行 `search`、`fetch`、`export_evidence` 三项均成功后，才能改为“ChatGPT App 验证通过”。本地 MCP 通过不能替代这项外部验证。

## 9. 配置

`.env.example` 是变量示例，不会自动加载。可在当前 PowerShell 会话设置：

```powershell
$env:SEARCH_DATA_DIR = ".\data"
$env:SEARCH_PYTHON = (Get-Command python.exe).Source
$env:SEARCH_NODE = (Get-Command node.exe).Source
$env:SEARCH_CLOUDFLARED = ""
$env:SEARCH_TUNNEL_STATE_DIR = ".\.codex_tmp\quick-tunnel"
$env:SEARCH_BACKEND_PORT = "8000"
$env:SEARCH_MCP_PORT = "8787"
$env:SEARCH_BACKEND_URL = "http://127.0.0.1:8000"
$env:SEARCH_FRONTEND_URL = "http://127.0.0.1:5173"
$env:SEARCH_RUNTIME_MODE = "local"
$env:SEARCH_ALLOW_UNAUTHENTICATED_MCP_DEV = ""
$env:SEARCH_BACKEND_BEARER_TOKEN = ""
$env:SEARCH_WIDGET_DOMAIN = ""
$env:SEARCH_MCP_INSPECTOR = ""
$env:SEARCH_SMOKE_SOURCE_TYPES = "zotero_inspiration_note"
$env:SEARCH_RUNTIME_DIR = ""
$env:SEARCH_LOG_DIR = ""
$env:SEARCH_CONFIG_DIR = ""
$env:SEARCH_ZOTERO_DATA_DIR = ""
$env:SEARCH_MODEL_CACHE_DIR = ".\data\models"
$env:SEARCH_EMBEDDING_MODEL = ""
$env:SEARCH_RERANKER_MODEL = ""
$env:SEARCH_MARKER_MODEL_CACHE = ""
```

路径规则：

- 源码模式从脚本/模块位置自动发现项目根。
- 打包模式的代码固定从 `resources/app/runtime-project` 定位，不引用开发 worktree。
- `SEARCH_DATA_DIR` 直接表示数据目录；相对路径基于源码或打包 runtime 根解析。
- 默认运行状态和后端日志位于 `%LOCALAPPDATA%\Search`。
- 默认用户配置位于 `%APPDATA%\Search`。
- `SEARCH_RUNTIME_DIR`、`SEARCH_LOG_DIR`、`SEARCH_CONFIG_DIR` 可分别覆盖上述目录。
- 未配置 Python/Node 时只做安全 PATH 探测；找不到会显示依赖缺失，不会安装软件。
- `SEARCH_ALLOW_UNAUTHENTICATED_MCP_DEV=1` 只允许短时本地 Developer Mode；公网长期部署必须使用认证。
- `SEARCH_BACKEND_BEARER_TOKEN`、`SEARCH_WIDGET_DOMAIN`、`SEARCH_MCP_INSPECTOR` 和 `SEARCH_SMOKE_SOURCE_TYPES` 分别用于可选后端认证扩展、托管 widget、已有 Inspector 路径和 MCP smoke 来源；示例不包含真实值。
- `SEARCH_TUNNEL_STATE_DIR` 只保存 Quick Tunnel 的临时日志；默认位于项目 `.codex_tmp/quick-tunnel`，不存储 Cloudflare credentials。
- `SEARCH_RUNTIME_ROOT` 及 `SEARCH_SCROLL_*`、`SEARCH_STATUS_*`、`SEARCH_PDF_PREVIEW_*` 等名称属于桌面打包器/自动化 probe 的内部进程契约，不是用户支持的配置接口，请勿手工设置。

## 10. 数据与隐私

以下内容不会进入 Git：

- production SQLite DB 及 WAL/SHM；
- PDF、converted Markdown 和用户笔记数据；
- Zotero 原库、snapshot 和同步缓存；
- FTS、LanceDB、vector index 和其他派生索引；
- embedding、reranker、OCR 模型和权重；
- Cloudflare credentials、私钥、证书和本地 Tunnel 配置；
- `.env`、桌面本地配置、日志、缓存、Electron user-data 和测试临时目录。

Search 默认只监听 loopback。执行任何迁移、导入 commit 或索引重建前，应先备份自己的数据；发布构建和自动化测试不会重建生产索引。

## 11. 项目结构

- `frontend/`：React/Vite 桌面 renderer，统一搜索页和 PDF Preview。
- `app/`：FastAPI、检索领域服务、资料库与运行时监督器。
- `integrations/search_desktop/`：Electron 桌面外壳、打包和 packaged smoke。
- `integrations/notebook_ai_chatgpt_app/`：本地 MCP server、Apps widget 和 MCP 测试。
- `zotero-plugin/`：Search Inspiration Zotero 扩展源码。
- `scripts/`：初始化、启动、测试、构建、索引、导入和维护脚本。
- `tests/`：Python 核心、运行时、安全和发布契约测试。
- `data/`：用户本地数据目录；clean clone 中可不存在，且默认不跟踪。
- `config/`：检索别名等不含凭据的确定性配置。

## 12. 开发与测试

先设置解释器：

```powershell
$env:SEARCH_PYTHON = (Get-Command python.exe).Source
$env:SEARCH_NODE = (Get-Command node.exe).Source
```

统一检查和测试：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_all.ps1
```

分项命令：

```powershell
$env:SEARCH_DATA_DIR = ".\.codex_tmp\manual-test-data"
& $env:SEARCH_PYTHON -B -m pytest -q .\tests\core -p no:cacheprovider --basetemp .\.codex_tmp\pytest-temp

$FrontendTests = (Get-ChildItem .\frontend\tests\*.test.mjs).FullName
& $env:SEARCH_NODE --test $FrontendTests

npm --prefix .\integrations\search_desktop test
npm --prefix .\integrations\notebook_ai_chatgpt_app test
npm --prefix .\frontend run build
npm --prefix .\integrations\notebook_ai_chatgpt_app run build
```

构建独立 Windows 候选：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

构建脚本只写入 `integrations/search_desktop/dist-candidates/<唯一候选名>`，不覆盖当前正式包。manifest 会记录 source commit、文件数、总大小、`Search.exe` SHA256、`resources/app` 树 SHA256 和完整树 SHA256。

packaged smoke 必须显式传入候选：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_all.ps1 `
  -IncludePackagedSmoke `
  -PackagedExecutable ".\integrations\search_desktop\dist-candidates\<候选名>\win-unpacked\Search.exe"
```

自动化设置 `SEARCH_ELECTRON_TEST_MODE=1`，不会弹出 Search 桌面窗口。

## 13. 常见问题

### Search 打不开

先运行 `bootstrap_windows.ps1 -CheckOnly`。确认 `SEARCH_PYTHON`、`SEARCH_NODE` 指向存在的文件，并查看 `%APPDATA%\Search\logs\search-startup.log`。预构建包不携带 Python/Node。

### 8000 不可用

打开“系统状态”检查 FastAPI。确认 Python 3.11 环境已安装 `requirements.lock.txt`，端口未被其他程序占用。不要直接结束不明进程；可在当前会话设置配套的 `SEARCH_BACKEND_PORT` 和 `SEARCH_BACKEND_URL` 后重启 Search。

### 8787 不可用

确认 Node 可执行文件和 packaged MCP bundle 存在。源码模式先运行 `npm --prefix .\integrations\notebook_ai_chatgpt_app run build`。健康检查为 `http://127.0.0.1:8787/healthz`。

### 为什么 ChatGPT Tunnel 不可用

本地 MCP 与外部 Tunnel 是两层。若 8000/8787 正常但 Tunnel 失败，系统会显示“本地后端正常，外部 Tunnel 不可达”。本地 Search 仍可使用。

### 为什么 `@search` 返回 `mcp_network_error`

通常是 ChatGPT App 仍指向旧 Quick Tunnel、Tunnel 进程未通过公网健康检查，或 `/mcp` URL 填写错误。重新运行 `start_quick_tunnel.ps1`，只使用脚本已验证并输出的新 URL，然后手动更新 ChatGPT App。

### PDF Preview 不显示

确认结果有有效 document ID、PDF 映射和页码；直接检查 `/api/v1/library/documents/<id>/pdf`。clean clone 不带 PDF，因此空资料库不会显示 Preview。

### Zotero 后端就绪但插件没有反应

确认插件版本与 Zotero 9.x 匹配、插件已重启，并先用可丢弃测试选区验证 prompt fallback。查看 Zotero Debug Output；Reader popup/跳转仍有已知限制。

### 端口被占用

先用系统状态确认占用者是否是 Search 已有实例。Search 使用单实例和外部进程所有权保护；不要结束不属于当前测试/实例的进程。必要时同时修改端口和对应 loopback URL。

### 为什么没有搜索结果

检查是否显示“资料库为空”、FTS manifest 是否缺失、来源/文档筛选是否过窄，以及高质量模型是否已配置。仓库不会附带生产数据或自动重建索引。

### 如何查看日志

运行时日志默认位于 `%LOCALAPPDATA%\Search\logs\runtime.jsonl`；Electron 启动日志默认位于 `%APPDATA%\Search\logs\search-startup.log`。若设置 `SEARCH_LOG_DIR`，后端日志改写到该目录。

## 14. 已知限制

- ChatGPT Quick Tunnel 不是持久连接；当前状态是 `PENDING_CHATGPT_TUNNEL_CONFIGURATION`。
- 首次源码安装需要从软件源下载 Python/npm 依赖；仓库本身不包含这些缓存。
- Windows 预构建包包含应用代码，但仍需要外部 Python 3.11 和 Node.js。
- 发布物不附带用户论文、数据库、Zotero 数据、索引或模型。
- 高质量检索、Marker/Surya OCR 和 GPU 路径需要用户自行准备兼容依赖与模型；当前验证模型栈包括 `marker-pdf 1.10.2`、`sentence-transformers 5.5.0`、`surya-ocr 0.17.1` 和本机 CUDA profile 的 `torch 2.11.0+cu128`，它们不在基础锁中自动安装。
- Zotero Reader 内嵌 popup 与部分跳转仍需更多版本验证；重要笔记应先做备份和小样本验证。
- Windows x64 是当前主要验证平台；macOS/Linux 尚无正式桌面发布包。
- 仓库当前没有 LICENSE；在维护者明确选择许可证前，不应假定拥有复制、修改或再发布授权。

## 15. 发布信息

- 版本：`0.1.4`
- Build ID：`20260717-search-0.1.4-github-release-convergence`
- 正式基线：`9c949e56d16c57124786fa52803ed128f53dcb3a`
- 发布 source commit：以 Git tag `0.1.4` 的目标提交及候选目录旁的 `search-0.1.4-build-manifest.json` 为准；构建脚本从 `git rev-parse HEAD` 自动记录完整值。
- 可重复构建入口：`scripts/build_windows.ps1`
- ChatGPT 状态：`PENDING_CHATGPT_TUNNEL_CONFIGURATION`

正式发布要求分支、clean clone 测试、secret/大文件审计、候选 smoke 和 hash manifest 全部通过。Windows ZIP 应作为 GitHub Release asset 发布，不把大型二进制提交进 Git。
