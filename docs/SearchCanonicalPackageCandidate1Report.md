# Search 0.1.4 canonical candidate1 构建与验证报告

## 1. 结论

Search 0.1.4 canonical candidate1 已从指定 clean source commit 完成构建，候选与独立 smoke 副本逐文件一致，packaged backend、MCP、frontend、PDF.js 与 Electron 资源均来自 package 副本。健康检查、统一搜索、高质量检索、MCP 三个只读工具、真实 PDF 渲染、三种 viewport、Evidence Basket、身份字段、退出清理和生产数据无漂移验证均通过。

本候选**不建议切换为正式包**。真实 `/retrieval → /workspace → /retrieval` 往返后，PDF 画布、页码、缩放、滚动位置、bbox 和 8 个 exact 高光已经恢复，但语义 ready 契约 `data-preview-ready` 持续为 `false`。这意味着视觉内容虽然恢复，自动化与只读诊断仍无法确认 Preview 完成。现有 Desktop 契约测试覆盖文本 Preview 的 Workspace 恢复，没有覆盖真实 PDF remount 后的语义 ready。

此外，package 的逐文件内容保持不变且 candidate/smoke 完全一致，但现有 PowerShell tree-hash helper 使用文化相关的 `Sort-Object FullName`。不同进程对带连字符的文件名可能产生不同排序，导致相同逐文件清单重算出不同聚合 tree hash。正式发布前应改为稳定的 ordinal 相对路径排序。

状态：

- `PASS_CANONICAL_SEARCH_PACKAGE_CANDIDATE_BUILT`
- `PASS_CANONICAL_SEARCH_PACKAGE_SELF_CONTAINED`
- `PASS_CANONICAL_SEARCH_PACKAGE_NO_DATA_DRIFT`
- `FAIL_CANONICAL_SEARCH_PACKAGE_SMOKE_WORKSPACE_PDF_READY_CONTRACT`
- `NOT_READY_FOR_FORMAL_SEARCH_PACKAGE_SWITCH`

## 2. 构建身份

| 字段 | 值 |
| --- | --- |
| Product | Search |
| Version | 0.1.4 |
| Build ID | `20260719-search-0.1.4-canonical-root-candidate1` |
| Source commit | `e84e432816ddba2c145271ebc0d02529dc28566c` |
| Source branch | `codex/search-canonical-root-migration` |
| Build mode | `packaged` |
| Build timestamp UTC | `2026-07-19T06:05:48.099Z` |

构建身份在 package metadata、machine-readable build report、Electron main/preload、Runtime status 和设置页诊断 payload 中一致。没有使用旧 Build ID、目录名推断或运行时 fallback。

构建前满足：

- HEAD 精确等于指定 source commit；
- 本地与远端差异为 0/0；
- tracked 工作树 clean；
- 四个 lockfile hash 与构建前基线一致；
- Search Runtime 和相关监听端口均为 0；
- production data tree hash 与迁移基线一致；
- 旧主仓库仍为 57 modified、1,253 untracked、0 staged。

## 3. 构建命令和路径

正式构建入口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\LEARNING\Tools\search\scripts\build_windows.ps1 `
  -BuildId 20260719-search-0.1.4-canonical-root-candidate1 `
  -OutputRoot D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c1 `
  -PythonExe D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe `
  -NodeExe D:\LEARNING\Tools\node.js\node.exe
```

构建脚本自行读取完整 Git HEAD 和当前 branch；调用方没有传入 source commit。

| 用途 | 路径 |
| --- | --- |
| Candidate root | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c1` |
| Candidate executable | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c1\win-unpacked\Search.exe` |
| 独立 smoke root | `D:\LEARNING\Tools\SearchPackageSmoke\Search-0.1.4-canonical-c1` |
| Smoke executable | `D:\LEARNING\Tools\SearchPackageSmoke\Search-0.1.4-canonical-c1\win-unpacked\Search.exe` |
| Smoke 状态与日志 | `D:\LEARNING\Tools\search\.codex_tmp\canonical-package-c1` |
| Runtime data root | `D:\LEARNING\Tools\search\data` |

## 4. Package 完整性

### 4.1 构建时机器报告

| 项目 | 结果 |
| --- | --- |
| `win-unpacked` 文件数 | 407 |
| `win-unpacked` 总字节 | 316,474,697 |
| `win-unpacked` build-time tree SHA256 | `FC4BA82CA4AA3A39EDFB5468567ED4F605CCEA84751622A3AFABF2B2A9EC251B` |
| 完整 candidate root 文件数 | 409 |
| 完整 candidate root 总字节 | 316,477,470 |
| 完整 candidate root initial audit SHA256 | `EA020B8F7D6E25C26192F053D2A9C93D773ED61CB0ADA4E85C60BE50E31AB767` |
| Search.exe SHA256 | `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD` |
| resources/app tree SHA256 | `7206FA1F70376DB97B7098C7F66BC682343EB0466596C02F5BBC2470798D23E5` |
| packaged runtime tree SHA256 | `C2DBC5C6B14A80582D7610B34E1780D825FAFEC9B850A8AA090BB3B8FFE79953` |
| frontend tree SHA256 | `EFEBBEEED2E51F1280FE3590AA01D33B8AA544B1BD88C059C3E4FC08329809A5` |
| metadata/package.json SHA256 | `18BD94C019983B1DFFF79C593AC65D58E4804DDD6664CB0AF4D679020510BE27` |
| MCP server SHA256 | `F49666EA4A3E63280B5C9DF3EC1374F0F1968A2808F4B330985B6EA1C74987A7` |
| MCP widget SHA256 | `A1A102E6318A1562820BD39756DF7CDDA0D3BCB3964AE3BEB3110234A5309C58` |

Candidate 与 smoke 副本的 409 个相对路径、文件大小和逐文件 SHA256 全部一致，差异数为 0。启动和退出后再次比对也没有文件变化。

聚合 tree hash 的后续重算暴露了排序不稳定问题：逐文件清单差异为 0，但默认文化排序对 `locales/es.pak` 与 `locales/es-419.pak` 的先后顺序发生变化。因此本报告保留构建时和初次审计值，同时把 ordinal 排序修复列为发布阻塞项，不用不稳定的重算值替代逐文件一致性证明。

### 4.2 内容边界

Candidate 和 smoke 副本均通过：

- reparse point：0；
- `node_modules` 目录：0；
- production DB、SQLite、PDF、LanceDB/vector、manifest 数据文件：0；
- `.git`、`.codex_tmp`、历史 `dist-candidates` 目录：0；
- production data bundled：false；
- machine-local config bundled：false。

以下正式引用在 candidate 和 smoke 副本中均为 0：

- 旧 `notebook_ai` 绝对根路径；
- `notebook_ai_worktrees`；
- `notebook_ai_clean_clones`；
- 旧 Build ID；
- `/api/v1/search/database`；
- `TunnelDriverBoundary`；
- `configure-tunnel`；
- `pause_tunnel`；
- `resume_tunnel`；
- `tunnel-doctor`。

## 5. 独立 smoke 运行证明

唯一正式 smoke 从复制后的 `Search.exe` 启动。进程命令行、packaged runtime-project、MCP server/widget、frontend 和 Electron resources 全部来自 smoke 副本；没有调用源码根 Python、源码 `frontend/dist`、源码 `node_modules`、旧 `notebook_ai` 或旧 candidate。

所有用户状态、Chromium cache、Runtime 状态、日志和临时目录均定向到 canonical 根下 `.codex_tmp`。生产 data 通过进程级 `SEARCH_DATA_DIR` 解析为 canonical data root。高质量搜索所需模型通过受支持的进程级外部 model-cache 配置读取；模型没有打入 package，也没有修改系统环境变量。

未使用 Computer Use。DOM 验收通过 Electron DevTools Protocol 的只读页面状态和事件契约完成；自动化窗口保持隐藏，没有出现控制台黑框。

## 6. 健康、身份与只读功能

| 项目 | 结果 |
| --- | --- |
| FastAPI `/health` | PASS；`app=Search`、`status=ok` |
| MCP `/healthz` | PASS；`status=ok` |
| Runtime status identity | PASS；Build ID、version、commit、branch、data root 全部一致 |
| Canonical retrieval route | PASS |
| `/api/v1/search` | 未注册 |
| `/api/v1/search/database` | 未注册 |
| Tunnel | 只读 degraded/not configured；Search 未启动、暂停或恢复 Tunnel |
| 统一关键词搜索 | PASS；返回真实 production result |
| 高质量搜索 | PASS；embedding 和 reranker 可用 |
| MCP `search` | PASS；只读 |
| MCP `fetch` | PASS；只读 |
| MCP `export_evidence` | PASS；只在内存生成返回内容，没有写 production data |
| 搜索导航 | PASS；用户可见入口只有一个“搜索” |
| 旧双入口 | 不存在 |
| 设置页 Tunnel 文案 | PASS；显示“Search 仅诊断 Tunnel 状态，不启动、暂停或恢复 Tunnel。” |

## 7. PDF Preview、Workspace 与 Evidence Basket

### 7.1 通过项

真实 production PDF Preview 验证通过：

- PDF document 和目标页真实加载；
- canvas 非零尺寸；
- exact highlight；
- 8 个 bbox rectangles；
- 页码、缩放、定位；
- 1440、1600、1920 viewport；
- PDF 内部滚动、结果列表滚动和 Evidence Basket 滚动彼此独立；
- Evidence Basket 加入、展示和恢复通过，状态仅在 renderer 内存中变化；
- 搜索词、搜索模式、来源筛选、文档筛选、结果、目标 fragment/chunk/page、Evidence Basket 和滚动位置在 Workspace 往返后恢复。

### 7.2 阻塞项

Workspace 返回后的真实状态快照：

- render status：`ready`；
- canvas：612 × 792；
- scale：1；
- page：7；
- chunk：66；
- actual exact highlight rectangles：8；
- 高光 bbox 非零且位于 canvas 内；
- PDF scroller 已恢复到目标位置；
- `data-preview-ready=false`；
- 语义 `data-highlight-strategy` 为空；
- 语义 `data-highlight-count=0`。

这不是固定 sleep 或超时不足：等待的是明确 semantic ready 条件，实际 canvas/highlight 已提交后该条件仍不成立。不能通过降低断言、扩大 timeout 或用 mock 代替真实 PDF 来掩盖。

需要在新 source commit 中：

1. 修复 `PdfLocationPreview` remount 后 auto-fit/focus completion 与实际 overlay commit 的一致性；
2. 新增真实 PDF 的 `/retrieval → /workspace → /retrieval` semantic-ready 回归测试；
3. 保持现有 canvas、页码、exact highlight、bbox、缩放、定位和独立滚动断言不变。

## 8. 生产数据无漂移

启动前、运行后、受控退出后和源码回归后均执行只读检查。最终结果：

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
| PDF 文件 | 8；hash 未变化 |
| WAL/SHM | 0 |
| manifest | 原始字节未变化 |

没有写 production SQLite、FTS、LanceDB、manifest、PDF、Zotero、notes 或 exports。

## 9. 受控退出

Packaged Runtime 先通过正式 launcher stop 关闭 supervisor、FastAPI 和 MCP；Electron 再通过应用自身 `app.quit()` 生命周期退出。没有使用 `taskkill /F`、`Stop-Process` 或其他强制终止。

退出后：

- Search.exe：0；
- supervisor/FastAPI/MCP 残留：0；
- orphan process：0；
- 8000、8787、5173、5191、18080、18787、19222、19223 监听：0；
- cloudflared 未被本任务启动、停止或修改；
- C 盘 Search 用户状态目录未删除、未用于本次 smoke。

## 10. 源码回归

| 项目 | 结果 |
| --- | --- |
| tests/core | 190 passed、4 skipped、0 failed；共 194 项 |
| tracked Python 内存 compile | 397/397 |
| Frontend | 25/25 |
| Desktop | 55/55 |
| MCP | 23/23 |
| Vite production build | PASS；132 modules |
| MCP widget build | PASS |
| MCP server build | PASS |
| packaged source resource contract | 33/33 |

4 个 core skip 是隔离测试 data 中没有 production DB/FTS/Zotero snapshot 的预期条件；生产数据完整性已由独立只读 validator 覆盖。任务输入中的 `194 passed` 和 `395/395` 是旧计数：当前实际为 190 passed + 4 skipped，以及 Build Identity 提交新增文件后的 397/397。所有测试均为 0 failed。

四个 tracked lockfile SHA256 在构建和回归前后完全一致，tracked 工作树在新增本报告前保持 clean。

## 11. 正式切换前剩余工作

1. 修复 Workspace 返回后 PDF semantic-ready 状态不提交的问题，并新增真实 PDF round-trip 覆盖；
2. 将 package tree hash 的路径排序改为稳定 ordinal 排序，确保跨进程可重复；
3. 以修复后的新 commit 重新构建新候选并完整重跑独立 smoke；
4. 新候选全部通过后，才能请求正式 Search.exe 切换授权。

本报告不授权修改 candidate1 的 source commit，也不授权切换正式包、打 tag、创建 Release 或上传 ZIP。
