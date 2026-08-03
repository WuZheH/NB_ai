# Search Candidate4 正式切换与旧根清退报告

## 1. 最终结论

本轮完成了 PDF `first_preview` 的真实根因复现、事件驱动修复、行为级测试、全量回归、GitHub 推送、Candidate4 构建及独立 packaged smoke。Candidate4 独立环境中的正式 renderer 条件连续 10/10 通过。

正式切换第 1 轮未通过：从真实 `Search Desktop` 计划任务启动 Candidate4 后，Electron window、renderer 和托盘均创建成功，但 FastAPI 8000 与 MCP 8787 在 180 秒内未就绪。按照既定安全规则，本轮立即终止后续冷启动，通过托盘“完全退出”和 packaged launcher `stop` 优雅收敛进程，自动恢复原计划任务定义与启用状态。

因此最终状态为：

- Candidate4 source、candidate、独立 smoke、正式部署副本和失败证据全部保留；
- Candidate4 **未成为正式入口**；
- 三轮冷启动未执行，只有第 1 轮启动并失败；
- 旧 `D:\LEARNING\Tools\notebook_ai` 根未进入清退审计、未删除；
- production data 无漂移；
- 未使用 `taskkill /F`、`Stop-Process -Force` 或其他强制终止手段。

## 2. 仓库与 source identity

| 项目 | 值 |
| --- | --- |
| GitHub repository | `WuZheH/NB_ai` |
| Branch | `codex/search-canonical-root-migration` |
| Candidate4 source commit | `83c6356dfd8f7822f1902d0ab676b8551c933080` |
| Source commit URL | `https://github.com/WuZheH/NB_ai/commit/83c6356dfd8f7822f1902d0ab676b8551c933080` |
| Build ID | `20260720-search-0.1.4-canonical-root-candidate4` |
| Canonical root | `D:\LEARNING\Tools\search` |

修复提交推送后为 ahead/behind `0/0`，tracked worktree clean。该提交是 Candidate4 唯一 source commit。

## 3. `first_preview` 根因与最终修复

### 3.1 Candidate3 原始失败

Candidate3 正式入口的 PDF 页面、canvas 和 exact overlay 可以实际完成，但 `data-preview-ready` 与 `first_preview` 未收敛。真实失败不是 PDF 下载或 PDF.js document load 的一般性失败，而是 focus completion 没有在正式首次布局时完成。

### 3.2 850ee 初步修复为何不足

提交 `850ee192f2fbfba97a51c26aba31bd9335e20339` 尝试在 `calculateHighlightScroll()` 返回 `null` 时写入 `preview_focus_degraded` 并完成 focus。行为审计证明该分支不能覆盖真实失败：

- `focusToHighlightUnion()` 在 scroller 为 `0 x 0` 时先返回 `null`，此时 `pendingFocusRef.current` 尚未建立；
- 已有 pending focus 且 `isRenderReadyForFocus()` 为 true 时，`calculateHighlightScroll()` 所需 page/render 尺寸均已有效；
- 容器不可滚动不会返回 `null`，而是返回 `{ left: 0, top: 0 }`；
- 850ee 新增的源码字符串 presence test 没有执行组件行为。

因此最终修复删除了无效的 `preview_focus_degraded` 路径和弱字符串测试，没有保留死代码来维持测试通过。

### 3.3 真实根因

真实复现链路为：首次正式 renderer mount 时 scroller 的 `clientWidth/clientHeight` 暂时为 0，`focusToHighlightUnion()` 无法生成 focus；随后布局恢复为正尺寸，但容器尺寸不是 React 可观察状态，相关 effect 不会仅因 DOM 尺寸变化再次执行。结果是 pending focus 不建立、completed focus 不完成，`data-preview-ready` 永久保持 false。

Candidate3 基线 package 在强制 `0 x 0 -> 正尺寸` 的真实 production renderer 探针中约 16 秒后失败；相同探针使用最终源码构建的 frontend 后通过。

### 3.4 修复内容

`83c6356d` 实现了统一的事件驱动 semantic-ready 契约：

- 用 `ResizeObserver` 观察 PDF scroller，并将有效 viewport 尺寸写入组件状态；
- viewport 从 0 恢复后重新执行 auto-fit 与 focus 计算；
- observer 在 effect cleanup 时 disconnect；
- 新文档切换时重置旧 selection/focus completion；
- ready 同时要求 PDF、page、canvas、overlay、有效 viewport 和 focus completion；
- PDF load error、render error、page mismatch、无效 overlay 或无效 viewport 均不得假 ready；
- 普通打开、startup restore、Workspace 返回和 Evidence 返回共用相同的产品语义状态；
- 未添加固定延迟、无限轮询、canvas-only ready、URL/page-only ready 或测试专用 selector。

修复提交实际变更：

- 修改 `docs/SearchFirstPreviewFormalEntryRootCause.md`；
- 修改 `frontend/src/PdfLocationPreview.jsx`；
- 修改 `frontend/src/utils/pdfPreviewReady.js`；
- 新增 `frontend/tests/pdfFocus.test.mjs`；
- 修改 `frontend/tests/pdfPreviewReady.test.mjs`；
- 修改 `integrations/search_desktop/tests/fixtures/productionPdfPreviewProbe.mjs`；
- 修改 `integrations/search_desktop/tests/productionPdfPreview.test.mjs`。

提交 diff 为 7 个文件、280 insertions、45 deletions。没有测试专用 bypass。

## 4. 行为级测试与全量回归

行为测试覆盖：

- 不可滚动容器返回 `left=0, top=0`；
- 必需输入缺失时 `calculateHighlightScroll()` 才返回 `null`；
- `0 x 0` 容器不能生成 focus，尺寸恢复后能够生成；
- 初始 0 尺寸、`ResizeObserver` 触发、focus 重算、pending 建立、completed 完成和 `data-preview-ready=true`；
- PDF load error、render error、page mismatch、无效 overlay 均保持 not-ready；
- 新文档重置旧 ready；
- Candidate3 frontend 真实失败、最终 frontend 在同一 production Electron fixture 中通过；
- 正式 source renderer 条件连续 10 次通过。

完整回归结果：

| 套件 | 结果 |
| --- | ---: |
| Core | 206/206 PASS |
| Frontend | 31/31 PASS |
| Desktop | 59/59 PASS |
| MCP | 25/25 PASS |
| Python compile | 400/400 PASS |
| Vite build | PASS，133 modules |
| MCP widget build | PASS |
| MCP server build | PASS |
| packaged source contract | PASS |
| machine-config contract | PASS |
| launcher/supervisor | PASS |
| PDF Preview / Workspace / Evidence Basket | PASS |

四个 lockfile 未修改，最终 SHA256 为：

| Lockfile | SHA256 |
| --- | --- |
| `frontend/package-lock.json` | `F2E8845F8A6586BCF39538BBCEC029DD77A1F3C0A7CBE630483187B9D71C9E84` |
| `integrations/notebook_ai_chatgpt_app/package-lock.json` | `28054A1678A1129944B73DDE6CA1CB3ECE909F7220D5AED9ABBE9EF895549905` |
| `integrations/search_desktop/package-lock.json` | `A01BAF8DFB33E949A1005C1A2A830F804F51BEE022F77D22E5C5D4110980BEA6` |
| `packages/search-design-system/package-lock.json` | `22282103B4C522935C23E8F7B29A5ECE8267F7B7A72C5C7BC2FB2C057E884EA0` |

## 5. Candidate4 构建身份

构建使用 Windows PowerShell 5.1 和正式 `scripts/build_windows.ps1`，在 clean worktree 上自动读取真实 HEAD，未创建 ZIP，未覆盖 Candidate1、Candidate2、Candidate3 或旧 smoke。

| 项目 | 值 |
| --- | --- |
| Candidate | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c4` |
| Independent smoke | `D:\LEARNING\Tools\SearchPackageSmoke\Search-0.1.4-canonical-c4` |
| Formal deployment copy | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-83c6356d` |
| Build report file count | 410 |
| Build report bytes | 316,502,449 |
| `Search.exe` SHA256 | `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD` |
| `resources/app` SHA256 | `DAFF1A9609C37EA8F9DC7795A994F672049B37E075179405B01A8CF3C403D16C` |
| Build complete tree SHA256 | `2B3EB4A235D02FDEC4D2541A3BA71722F13EDC9B8DBF2ED3B7D69531819D2CEF` |
| Frontend tree SHA256 | `0F6B6B7C733E844FC25C41B747AB5951C3CE1FDD7DD7B1B73C31B55B532150C2` |
| Production data bundled | false |
| Machine config bundled | false |

candidate、smoke 和 formal copy 在复制完成后的完整目录身份一致；额外目录级复核包含 build report 本身时均为 412 文件、316,505,386 字节，tree SHA256 `F1F5F1AD4492059C8A221C7534179B39B42BCE41D93625F8A0FC75573D8902A5`。

## 6. Candidate4 独立 packaged smoke

无 machine-config 模式按契约启动：health 正常，关键词搜索可用，高质量搜索与 MCP 返回结构化 `config_missing`，没有泄露本机模型路径。

有效 machine-config 模式通过：

- FastAPI 与 MCP ready；
- 关键词“运动”返回 12 条；
- 中英文高质量搜索成功，embedding/reranker 实际加载；
- MCP 工具集合精确为 `search`、`fetch`、`export_evidence`，fetch 含 provenance，export 返回 Markdown；
- PDF 首次预览定位 document 1 / chunk 66 / page 7；
- `data-preview-ready=true`，exact strategy，8 个有效 bbox；
- page、scale、location、1440/1600/1920 viewport 均通过；
- Workspace 返回恢复成功，Evidence Basket 为 22；
- single-instance 第二实例正常以 exit code 0 退出；
- Tunnel 只读，未启动 cloudflared；
- graceful shutdown 后 Search/runtime orphan 为 0，相关端口全部释放。

正式 packaged renderer 的 `first_preview` 条件连续 10/10 通过；每轮均为 page 7、chunk 66、ready、exact、8 highlights、Workspace restored。没有偶发失败。

## 7. 正式切换失败证据

切换前创建了完整任务回滚快照：

`D:\LEARNING\Tools\search\.codex_tmp\candidate4-first-preview-v3\formal-switch-backup`

其中保留 `Search Desktop`、`NOTEBOOK_AI Runtime Launcher` XML 和切换前状态 JSON。machine-config 只验证未覆盖，路径为 `%APPDATA%\Search\machine-config.json`，SHA256 始终为 `5A6FEBDEF711F81998D8CBB6DD371601358CC9C1CC6F328689187DA8E33B092A`。

第 1 轮从真实 `Search Desktop` 计划任务启动。启动日志证明：

- Candidate4 build ID 与 source commit 正确；
- `config_resolved`、`design_tokens_loaded`、`renderer_started` 完成；
- BrowserWindow 与 tray 创建完成，应用进入 ready；
- `runtime_checked` 在 `2026-07-20T10:51:57.405Z` 开始并于 `10:51:57.406Z` 完成；
- 180 秒内 8000 与 8787 均未监听；
- 启动时 runtime status 仍是 Candidate3 stopped 快照，未出现 Candidate4 supervisor/FastAPI/MCP 进程。

正式部署与独立 smoke 的已确认差异是：

- Candidate4 candidate、smoke 与正式部署副本都没有 `search-desktop.local.json`；
- Candidate2 当前正式目录与 Candidate3 正式副本均有 schema 3 sidecar，明确指定 canonical production data、Python 和 Node；
- 独立 smoke 启动脚本显式注入了等价的隔离环境；真实计划任务没有这些 process-local 注入；
- 缺少 sidecar 后，正式包会退回 PATH 探测 Python/Node，并把 data 默认解析到不存在的 `%LOCALAPPDATA%\Search\data`。

这是已证实的正式部署契约缺口。1 ms 内结束的 `runtime_checked` 与 `runtime_prerequisites_missing` 的同步拒绝路径一致，但当前 Electron catch 只更新内存状态，startup log 未记录具体 error code；因此不能事后声称究竟是 Python、Node 或另一个 prerequisite 缺失。旧 Candidate3 stopped status 是观察到的启动前状态，不作为已证明根因。

由于正式第 1 轮失败，PDF、搜索、MCP、Workspace、Evidence Basket 等后续正式轮次步骤没有伪报为通过；第 2、3 轮未启动。

## 8. 优雅退出与自动回滚

失败后执行顺序：

1. Windows UI Automation 打开隐藏图标区，精确定位 tooltip 为 `Search` 的托盘图标；
2. 右键打开真实应用菜单并执行末项“完全退出”，走产品 `onFullyQuit()`；
3. Candidate4 的 4 个 Electron 进程全部正常退出；
4. 使用 Candidate4 packaged runtime metadata 和正式 machine-config 执行 launcher `stop`；
5. launcher 返回 Candidate4 `state=stopped`，FastAPI/MCP/supervisor pid 均为 null；
6. 执行回滚脚本，恢复原计划任务。

回滚后：

| 项目 | 最终状态 |
| --- | --- |
| `Search Desktop` | Disabled |
| `Search Desktop` executable | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\win-unpacked\Search.exe` |
| `Search Desktop` working directory | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\win-unpacked` |
| `NOTEBOOK_AI Runtime Launcher` | Enabled / Ready |
| 旧 runtime task executable | `D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe` |
| 旧 runtime task working directory | `D:\LEARNING\Tools\notebook_ai` |
| 相关进程 | 0 |
| 相关监听端口 | 0 |

旧计划任务没有删除，快捷方式没有修改，Registry Run 没有修改。

## 9. EPERM 处理

先前临时目录 `D:\LEARNING\Tools\search\.codex_tmp\candidate4-first-preview` 曾出现 EPERM。没有确认到可安全终止的独立占用者，也没有修改权限或安全配置。该目录原样保留，本轮改用唯一新目录：

`D:\LEARNING\Tools\search\.codex_tmp\candidate4-first-preview-v3`

新目录完成了复现、测试、smoke 与正式失败证据采集。最终只读复核时，旧目录仍存在，除执行检查命令自身外没有进程命令行引用它。没有强制删除、系统重启或越界清理。

## 10. Production data 最终守卫

最终扫描将当前 tree 与 Candidate3 回滚后已验证的 191 文件基线逐路径、字节数和 SHA256 比较：0 missing、0 extra、0 changed；目录集合也是 0 missing、0 extra。

| 项目 | 最终值 |
| --- | ---: |
| 文件 | 191 |
| 目录（含根） | 92 |
| 总字节 | 670,300,309 |
| 既有 full tree SHA256 | `7de213d494bb5387b21e037248a2da4fcf3c51dbaf148cfcc28acb5240e37c64` |
| 当前 `search.tree-hash.v1` SHA256 | `0FC6E59C6A0B54469AD80D71F0E219F0C99E7BC8B3A623D1B3E020C86BDEBE20` |
| SQLite 非空数据库 | 41 |
| SQLite `integrity_check` | 41/41 `ok` |
| SQLite `foreign_key_check` | 41/41 `[]` |
| FTS | 11,803 |
| passage vectors | 11,373 |
| object vectors | 35 |
| legacy vectors | 6,114 |
| Zotero note vectors | 161 |
| WAL/SHM | 0 |

两个 tree hash 来自不同版本的规范化算法，不能直接互比；无漂移结论来自当前 191 个文件与既有 guard 清单的逐文件 exact match。SQLite 使用 `mode=ro&immutable=1`、`PRAGMA query_only=ON` 复核。FTS/向量计数由完全相同的受保护文件清单和本轮构建前只读回归共同证明。

## 11. 旧根清退状态

旧根没有删除。正式切换与三轮冷启动未通过，因此严格未进入 `D:\LEARNING\Tools\notebook_ai` 的 57 modified / 1,253 untracked、唯一数据、reparse point、worktree、launcher、计划任务与快捷方式清退审计。

当前 `NOTEBOOK_AI Runtime Launcher` 已恢复启用并仍明确引用旧根。这一状态本身禁止删除旧根。没有删除或不可逆修改系统计划任务，也未触发“请求用户批准删除计划任务”的阶段。

## 12. 本机变更边界与后续

- C 盘写入限于正式 `%APPDATA%\Search` Electron user-data/startup log、`%LOCALAPPDATA%\Search\runtime\status.json` 与 Search logs，以及 Windows Task Scheduler 的临时切换和回滚记录；
- machine-config 内容和 hash 未变化；
- 没有安装或下载软件、依赖、模型；
- 没有启动或修改 cloudflared；
- 没有修改 production data、永久环境变量、PATH、注册表、Candidate1/2/3、旧 smoke 或回滚包；
- Candidate4 与全部证据保留，可用于后续修复正式 sidecar/provisioning 契约后重新构建候选或重新执行正式验证。

当前不需要用户执行恢复操作，系统已回到切换前安全状态。若继续推进，需要先把正式 `search-desktop.local.json` 的创建/继承纳入可审计的部署流程，并增加“真实计划任务环境不依赖 smoke 注入”的回归；在此之前不得再次宣称 Candidate4 正式切换成功，也不得删除旧根。
