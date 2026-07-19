# Search 0.1.4 canonical candidate2 构建与验证报告

## 1. 结论

Search 0.1.4 canonical candidate2 已从指定的 clean source commit 通过 Windows PowerShell 5.1 完成全新构建。候选与独立 smoke 副本逐文件一致，packaged Electron、frontend、Python Runtime 源码、MCP server/widget 和 PDF.js 均来自 smoke 副本，不依赖 canonical 源码目录或旧 `notebook_ai` 根。

真实 package smoke 已通过健康检查、统一搜索、高质量检索、MCP 三个只读工具、PDF Preview、三种 viewport、Workspace round-trip、`data-preview-ready=true`、Evidence Basket、单一“搜索”入口、只读 Tunnel 诊断和受控退出。生产数据在构建、smoke、退出和源码回归前后逐字节不变。

Search 在本报告中是同一个产品：供 ChatGPT 使用的 Search 应用及其本地 Runtime/MCP。外部 HTTPS Tunnel 仍是独立配置边界；本候选只验证本地 Runtime/MCP 和 Tunnel 只读诊断，没有修改 ChatGPT App 或 Cloudflare 配置。

状态：

- `PASS_CANONICAL_SEARCH_PACKAGE_CANDIDATE2_BUILT`
- `PASS_CANONICAL_SEARCH_PACKAGE_CANDIDATE2_SELF_CONTAINED`
- `PASS_CANONICAL_SEARCH_PACKAGE_CANDIDATE2_SMOKE`
- `PASS_CANONICAL_SEARCH_PACKAGE_CANDIDATE2_NO_DATA_DRIFT`
- `READY_FOR_FORMAL_SEARCH_PACKAGE_SWITCH_AND_DAILY_USE_VALIDATION`

Candidate1、其 smoke 副本、报告和失败证据均保留，candidate1 的失败结论没有被覆盖或改写。

## 2. 构建基线与命令

构建前满足：

- HEAD：`99dab087c78afd030d96d2b2d4e7e6efcb5c067a`；
- branch：`codex/search-canonical-root-migration`；
- 本地与远端差异：0/0；
- tracked 工作树：clean；
- Search Runtime：0；
- 8000、8787、18080、18787：均未监听；
- production data：191 文件、670,300,309 字节、基线 tree SHA256 一致；
- 旧主仓库：57 modified、1,253 untracked、0 staged；
- candidate1 和 candidate1 smoke 完整保留；
- candidate2 和 candidate2 smoke 目标路径开始时均不存在。

失败的未完成 candidate2 输出在删除前确认：408 文件、316,480,842 字节、无有效 build report、无 reparse point、无 Git 元数据、无生产数据库/PDF/向量数据、无加载进程。只删除了用户批准的精确目录，未使用 `git clean`，也未触及任何其他 candidate 或 `.codex_tmp` 证据。

正式构建命令使用 Windows PowerShell 5.1：

```powershell
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe `
  -NoProfile `
  -ExecutionPolicy Bypass `
  -File D:\LEARNING\Tools\search\scripts\build_windows.ps1 `
  -PythonExe D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe `
  -NodeExe D:\LEARNING\Tools\node.js\node.exe `
  -BuildId 20260719-search-0.1.4-canonical-root-candidate2 `
  -OutputRoot D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c2
```

Source commit 由构建脚本执行 Git 命令自动读取，没有由调用方传入。

| 用途 | 路径 |
| --- | --- |
| Candidate root | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c2` |
| Candidate executable | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c2\win-unpacked\Search.exe` |
| 独立 smoke root | `D:\LEARNING\Tools\SearchPackageSmoke\Search-0.1.4-canonical-c2` |
| Smoke executable | `D:\LEARNING\Tools\SearchPackageSmoke\Search-0.1.4-canonical-c2\win-unpacked\Search.exe` |
| Smoke 状态、日志和缓存 | `D:\LEARNING\Tools\search\.codex_tmp\canonical-package-c2` |
| Runtime data root | `D:\LEARNING\Tools\search\data` |

## 3. Build Identity

| 字段 | 值 |
| --- | --- |
| Product | Search |
| Version | 0.1.4 |
| Build ID | `20260719-search-0.1.4-canonical-root-candidate2` |
| Source commit | `99dab087c78afd030d96d2b2d4e7e6efcb5c067a` |
| Source branch | `codex/search-canonical-root-migration` |
| Build mode | `packaged` |
| Build timestamp UTC | `2026-07-19T10:30:37.019Z` |

身份在 package metadata、machine-readable build report、Electron main/preload、Runtime status 和设置页诊断 payload 中一致。没有使用 fallback、目录名推断、candidate1 Build ID 或历史 Build ID。

`build_timestamp_utc` 通过 Node 的 JSON 字符串读取和原始文本捕获验证：package metadata、build report 嵌套身份和 build report 顶层字段均为 `string`，规范化 UTC 字符串及 UTF-8 字节完全一致。Windows PowerShell 5.1 生成的 build report 带 UTF-8 BOM；验证器只在 JSON 解析前识别 BOM，未对时间戳执行 PowerShell `DateTime` 转换或重新格式化。

## 4. Package 完整性与内容边界

### 4.1 稳定哈希

| 项目 | 结果 |
| --- | --- |
| Candidate root 文件数 | 409 |
| Candidate root 总字节 | 316,482,767 |
| Candidate/smoke stable tree SHA256 | `07EB57990E849C4D43312C17E9466B7A2DB7AF0173102427CAE3C4BEC3F2FC03` |
| `win-unpacked` 文件数 | 407 |
| `win-unpacked` 总字节 | 316,479,830 |
| `win-unpacked` SHA256 | `FA11A0A470BCDF67CFFBCE50D88BCACFD2EFFFDAB2BAC2E716C16D65FF069794` |
| Search.exe SHA256 | `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD` |
| resources/app tree SHA256 | `F93E83ECC6049AE9DE0CF1610C74BD5E9C32CE233A9D0AC6CD68A85A690D4D4F` |
| packaged runtime tree SHA256 | `BC4513DA82B0D8247D4E54664B5784A5A0D18EE54226516D3AD35BEE5395527E` |
| frontend tree SHA256 | `21E847D6446C62814633861E86C1352D0A2F8B8E514E2BFBF2B3024635F8BFA5` |
| metadata SHA256 | `B20973D000D8B07946502ACED5D733CAF6D39AFC101AE7842303996C9046FBEC` |
| MCP server SHA256 | `F49666EA4A3E63280B5C9DF3EC1374F0F1968A2808F4B330985B6EA1C74987A7` |
| MCP widget SHA256 | `A1A102E6318A1562820BD39756DF7CDDA0D3BCB3964AE3BEB3110234A5309C58` |

稳定 tree hash 使用 `search.tree-hash.v1`、`OrdinalIgnoreCase` 加 ordinal tie-break、长度前缀二进制编码，空目录不参与。Candidate 和 smoke 副本各在三个独立 Windows PowerShell 5.1 进程中计算，共六次；结果只有一个唯一值。构建报告中的 `win-unpacked` hash 与退出后重算结果一致。

本构建采用 unpacked `resources/app`，没有 `app.asar`；因此报告以 `resources/app` tree hash 作为等价资源完整性标识。

### 4.2 内容边界

Candidate 和 smoke 副本均确认：

- reparse point：0；
- `.git`、`.codex_tmp`、`node_modules`、历史 `dist-candidates`：0；
- production SQLite、PDF、LanceDB/vector_store、production manifest、notes、exports：0；
- production data bundled：false；
- machine-local config bundled：false；
- candidate 与 smoke 的 409 个文件、总字节和 tree hash 完全一致。

以下正式引用在 candidate 和 smoke 副本中均为 0：

- `D:\LEARNING\Tools\notebook_ai`；
- `notebook_ai_worktrees`；
- `notebook_ai_clean_clones`；
- candidate1 和历史 Build ID；
- `/api/v1/search/database`；
- `TunnelDriverBoundary`；
- `configure-tunnel`；
- `pause_tunnel`；
- `resume_tunnel`；
- `tunnel-doctor`。

## 5. 独立 smoke 与自包含运行证明

唯一正式 smoke 从独立复制后的 `Search.exe` 启动。运行进程证明：

- Electron 主进程和 renderer 来自 smoke 副本；
- supervisor 使用 smoke 副本内 `runtime-project/scripts/runtime/notebook_ai_launcher.py`；
- FastAPI 以 packaged `runtime-project` 为工作代码根；
- MCP 使用 smoke 副本内已构建的 `dist/server/index.js`；
- frontend 来自 smoke 副本 `resources/search-assets/frontend`；
- 旧根依赖：0；
- canonical 源码 `app` 或 `frontend/dist` 依赖：0；
- Python、Node 和 conhost 的可见窗口句柄：0，没有控制台黑框。

解释器通过受支持的 `SEARCH_PYTHON`、`SEARCH_NODE` 配置取得；packaged Python 源码、MCP bundle、frontend 和 Electron resources 不依赖源码根。模型通过外部只读 model-cache 配置读取，没有打入 package，也没有修改系统环境变量。

所有 user-data、Chromium cache、Runtime 状态、日志和临时文件均写入 `D:\LEARNING\Tools\search\.codex_tmp\canonical-package-c2`。未使用 Computer Use；UI 验收通过 Electron DevTools Protocol 的只读 DOM 状态和事件契约完成。

## 6. 健康、搜索、MCP 与 Tunnel

| 项目 | 结果 |
| --- | --- |
| FastAPI `/health` | PASS；`app=Search`、`status=ok` |
| MCP `/healthz` | PASS；`status=ok` |
| Runtime identity | PASS；Build ID、version、commit、branch、data root 全部一致 |
| Canonical retrieval route | PASS |
| `/api/v1/search` | 未注册 |
| `/api/v1/search/database` | 未注册 |
| 关键词搜索 | PASS；12 条合法 production result |
| 高质量搜索 | PASS；embedding/reranker 可用；UI 10 条结果 |
| MCP `search` | PASS；只读 |
| MCP `fetch` | PASS；只读并包含 provenance |
| MCP `export_evidence` | PASS；仅生成返回内容，没有写 production data |
| 用户可见搜索入口 | 只有一个“搜索” |
| 旧双搜索入口 | 0 |
| Tunnel | `tunnel_not_configured`；本地 ready，不作为本地阻塞 |
| 设置页文案 | “Search 仅诊断 Tunnel 状态，不启动、暂停或恢复 Tunnel。” |

Search 没有启动、停止、暂停、恢复或配置 cloudflared；ChatGPT App 配置也未修改。

## 7. PDF Preview、Workspace 与 Evidence Basket

真实 package DOM smoke 验证通过：

- PDF document 和目标页真实加载；
- canvas 非零；
- 页码 7；
- exact highlight；
- 8 个非零且位于 canvas 内的 bbox rectangles；
- 缩放、定位和 PDF 内部滚动通过；
- 1440、1600、1920 viewport 均通过；
- 搜索结果、Preview、PDF 和 Evidence Basket 使用独立滚动区域；
- Evidence Basket 加入、展示和 Workspace 返回恢复通过，smoke 中为 22 项 renderer 内存状态；
- `/retrieval → /workspace → /retrieval` 后搜索词、模式、筛选、结果、页码、chunk、缩放、滚动、高光和 Evidence Basket 恢复；
- Workspace 返回后 `data-preview-ready=true`；
- 新文档加载和错误语义由已提交的 semantic-ready 状态机契约覆盖。

Candidate1 的真实视觉内容恢复但 semantic-ready 持续 false 的阻塞在 candidate2 中不再复现。

## 8. 生产数据无漂移

构建前、smoke 前、受控退出后、源码回归后均运行只读 guard。最终结果：

| 项目 | 结果 |
| --- | --- |
| 文件数 | 191 |
| 目录数 | 92 |
| 总字节 | 670,300,309 |
| 全树 SHA256 | `7de213d494bb5387b21e037248a2da4fcf3c51dbaf148cfcc28acb5240e37c64` |
| SQLite integrity_check | 41/41 `ok` |
| foreign_key_check | 41/41 空 |
| FTS | 11,803 |
| passage vectors | 11,373 |
| object vectors | 35 |
| legacy vectors | 6,114 |
| Zotero note vectors | 161 |
| PDF | 8；hash 未变化 |
| WAL/SHM | 0 |
| manifest | 原始字节未变化 |

没有写 production SQLite、FTS、LanceDB、manifest、PDF、Zotero、notes 或 exports。

## 9. 受控退出

自动化先向 Electron 应用发送 `app.quit()`；Electron 进程正常退出，但该低层调试调用不等价于托盘“完全退出”的完整 Runtime stop 编排，supervisor、FastAPI 和 MCP 仍在。随后使用 smoke 副本内正式 packaged launcher、同一 packaged metadata 和同一 runtime/config 路径执行 `stop`，返回完整 `state=stopped`。

整个退出过程没有使用 `taskkill /F`、`Stop-Process -Force` 或其他强制终止。最终结果：

- Search.exe：0；
- supervisor、FastAPI、MCP：0；
- orphan Search process：0；
- 8000、8787、5173、19222、19223、18080、18787：全部释放；
- cloudflared 和其他应用未受影响。

Desktop 回归测试中的“runtime started by desktop is stopped on fully quit”契约通过；正式切换后的日常验证仍应从托盘执行一次真实“完全退出”，确认用户路径与本次正式 launcher stop 结果一致。

## 10. 源码回归

| 项目 | 结果 |
| --- | --- |
| tests/core | 190 passed、4 skipped、0 failed；共 194 项 |
| tracked Python compile | 397/397 |
| Frontend | 28/28 |
| Desktop | 56/56 |
| MCP | 23/23 |
| Vite production build | PASS；133 modules |
| MCP widget build | PASS |
| MCP server build | PASS |
| packaged source resource contract | 33/33 |

4 个 core skip 是隔离测试 data 中缺少 production DB/FTS/Zotero snapshot 的预期条件；生产数据由独立只读 guard 覆盖。四个 tracked lockfile SHA256 在构建与回归前后完全一致。新增本报告前 tracked 工作树 clean，测试结束后相关端口均无残留。

## 11. 正式切换前剩余检查

1. 由用户单独批准正式 package 切换；
2. 切换后从正式路径重复健康、搜索、PDF、Workspace、Evidence Basket 和身份检查；
3. 从托盘执行一次真实“完全退出”并确认 Runtime/端口无残留；
4. 如需 ChatGPT 远程使用，单独完成 HTTPS Tunnel 配置和实际 ChatGPT 调用验证；本报告没有把本地 MCP 通过冒充为外部 ChatGPT 连接通过。

本报告不授权切换正式包、修改快捷方式、打 tag、创建 GitHub Release、上传 ZIP、修改 production data、操作 cloudflared 或修改 ChatGPT App。
