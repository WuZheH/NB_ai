# Search legacy `notebook_ai` 根删除报告

## 1. 最终结论

旧根 `D:\LEARNING\Tools\notebook_ai` 的内容清退、空目录壳删除、Candidate8 删除后受控重启、API/MCP/UI/PDF 验证和 production data 最终守卫均已完成。

最终状态：

- 旧根完全不存在；
- `Search Desktop` 为 Enabled / Running，任务无参数，唯一 executable 为 Candidate8 formal package；
- `NOTEBOOK_AI Runtime Launcher` 任务定义仍保留且为 Disabled；
- Candidate8 local ready，FastAPI、MCP、renderer 均 healthy；
- production data 前后稳定树哈希相同，41/41 SQLite 完整性通过，无 WAL/SHM 或写漂移；
- Candidate1–8、smoke、旧正式包、migration safety archive、cache 和两个独立 worktree 全部保留；
- 未修改 ACL、未夺取所有权、未强制结束 Search 或 Runtime 进程、未重启系统、未执行 `git clean`、未安装或下载软件。

结论：`SEARCH_CANONICAL_ROOT_MIGRATION_CLOSED`。

## 2. Repository 与基线

| 项目 | 值 |
| --- | --- |
| Repository | `WuZheH/NB_ai` |
| Branch | `codex/search-canonical-root-migration` |
| 本轮开始 HEAD | `f28e98255e7d11df00791107ac0256b8d8045365` |
| Candidate8 source commit | `db92824c2551156df6c441db8233ae96386cb851` |
| Candidate8 build ID | `20260722-search-0.1.4-canonical-root-candidate8` |

本轮开始时本地与 `origin/codex/search-canonical-root-migration` ahead/behind 为 `0/0`，tracked worktree clean。

## 3. 删除前完整统计与唯一数据审计

第一次删除前的最终稳定清单为：

| 项目 | 值 |
| --- | ---: |
| 文件 | 26,888 |
| 子目录 | 2,889 |
| 目录（含根） | 2,890 |
| 总字节 | 4,416,335,449 |
| reparse point | 0 |
| `legacy.notebook-ai.tree-hash.v1` | `246E24461AF7F7413DB58209323231286076B2A3269C84794FBF2EF2F8898A33` |

所有 26,888 个文件均获得 disposition：

| 分类 | 文件数 |
| --- | ---: |
| exact duplicate in GitHub | 46 |
| exact duplicate in canonical production data | 191 |
| generated build artifact | 2,024 |
| dependency cache | 5,655 |
| runtime log or status | 559 |
| obsolete test artifact | 52 |
| safely regenerable | 18,360 |
| unique but obsolete and safe to delete | 1 |
| unique requires user decision | 0 |

其中 191 个 legacy `data\` 文件按相对路径、字节数和 SHA256 与 canonical production data 逐项相同。57 个 modified tracked、1,253 个 untracked 和 38 个 handwritten untracked 文件均由 migration safety archive 覆盖并验证；legacy HEAD 在 GitHub，local-only commit 和 stash 均为 0。因此没有仍需用户裁决的唯一数据，结论为：

`PASS_SEARCH_LEGACY_ROOT_UNIQUE_DATA_AUDIT`

## 4. Reparse point 与删除边界

最终清单和删除前 live scan 均为 0 junction、0 symbolic link、0 mount point、0 其他 reparse point；遍历没有跨出删除目标。旧根与 canonical root、production data、model cache、marker cache、migration archive、Candidate/formal/smoke 资产及两个 worktree 均为不同路径。

结论：

`PASS_SEARCH_LEGACY_ROOT_REPARSE_POINT_AUDIT`

## 5. 两次删除结果

第一次删除发生在用户切换 workspace/cwd 之前。它已清空 26,888 个文件和 2,889 个子目录，但删除最后的空根目录壳时返回 `System.IO.IOException`：目录正被另一进程使用。失败后的只读残留证明为 0 文件、0 子目录、0 字节、仅 1 个空根目录；当时立即停止，没有第二次尝试，也没有终止锁进程。

用户随后关闭所有已知以旧根作为 cwd/workspace 的窗口和终端，并将 Codex/终端 workspace 切换到 `D:\LEARNING\Tools\search`。

第二次删除前的重新审计确认：

- 当前 cwd/workspace 为 canonical Search 根；
- 当前进程链和全局进程的 ExecutablePath/CommandLine 对旧根引用数均为 0；
- 环境/workspace 变量旧根引用数为 0；
- 旧根仍为 0 文件、0 子目录、0 字节、0 hidden/system item、0 reparse point；
- 旧根内不存在 production data；
- 4 个 Search 进程全部来自 Candidate8 formal executable；
- supervisor 和 MCP 命令行直接来自 formal runtime-project，FastAPI 是该 formal supervisor 的直接子进程；
- `NOTEBOOK_AI Runtime Launcher` 仍为 Disabled；
- production data 仍为 191 文件、670,300,309 字节和预期稳定树哈希。

2026-07-22 19:19:19（Asia/Shanghai）只执行了一次第二轮删除调用：`Remove-Item -LiteralPath` 精确目标；没有通配符、`-Recurse` 或 `-Force`，没有删除父目录。调用成功，紧随其后的 `Test-Path -LiteralPath` 为 false。后续所有资产检查和最终系统审计也持续证明旧根不存在。

结论：

`PASS_SEARCH_LEGACY_EMPTY_ROOT_REMOVAL`

`LEGACY_NOTEBOOK_AI_ROOT_REMOVED`

## 6. 保留资产

删除后逐项确认仍存在：

- `dist-candidates` 下 Candidate1–8；
- Candidate1/2 canonical package smoke、Candidate3/4/5/7/8 smoke 与正式验收证据；
- 旧 formal packages `Search-0.1.4-45f89f2e`、`Search-0.1.4-604e9e85`、`Search-0.1.4-83c6356d`；
- Candidate8 formal package `Search-0.1.4-db92824c`；
- `D:\LEARNING\Archives\SearchMigrationSafety_20260718`；
- `D:\LEARNING\Tools\search\data`；
- `D:\LEARNING\Tools\model_cache`；
- `D:\LEARNING\Tools\marker_cache`；
- 两个已转换为独立仓库的 worktree。

两个 worktree 各自仍有独立 `.git` 目录、无 object alternates、tracked clean：

| Worktree | HEAD |
| --- | --- |
| `search-0.1.4-github-release-convergence` | `047f6385493d72898c5f16020d1368faf5ec02a9` |
| `unified-local-backend-bootstrap-0.1.3` | `9c949e56d16c57124786fa52803ed128f53dcb3a` |

旧 `NOTEBOOK_AI Runtime Launcher` 任务定义和 XML 备份均保留。任务 action 仍记录旧路径作为回滚历史，但任务为 Disabled，既未删除也未修改。

`LEGACY_RUNTIME_TASK_RETAINED_DISABLED`

## 7. Candidate8 删除后受控重启

旧根删除成功后，通过真实 Search 系统托盘的“完全退出”执行正常关闭。退出结果：

- Search 进程：0；
- 原 supervisor、FastAPI、MCP PID：0；
- 5173、8000、8787、19224 listener：0；
- `Search Desktop` task state：Ready。

没有使用 `taskkill`、`Stop-Process -Force` 或 Runtime 强制终止。一个已知会挂起的 disposable 外部 Edge PDF 探针调用被停止等待；该临时浏览器随后通过 CDP `Browser.close` 正常关闭，未影响 Search/Runtime，19224 最终为 0。

随后从真实 `Search Desktop` Scheduled Task 启动，没有给任务添加任何调试参数。10.614 秒后 local ready：

| 项目 | 删除后最终状态 |
| --- | --- |
| `Search Desktop` | Enabled / Running |
| Executable | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-db92824c\win-unpacked\Search.exe` |
| Arguments | none |
| Search processes | 4，全部来自 Candidate8 formal package |
| Build ID | `20260722-search-0.1.4-canonical-root-candidate8` |
| Source commit | `db92824c2551156df6c441db8233ae96386cb851` |
| Runtime owner | `managed-by-search` |
| Runtime state | `local_ready_tunnel_missing` |
| Data root | `D:\LEARNING\Tools\search\data` |
| desktop-runtime config | `desktop_runtime_ready` / `user_data` |
| machine-config | `model_ready` |
| 5173 / 8000 / 8787 | listening / healthy |
| 19224 | not listening |

`desktop-runtime.json` SHA256 仍为 `3FD0B64B20FEAE365A8B6A0CE2C34EE23E1DD73B0F15A259E1ADB722D946826B`，`machine-config.json` SHA256 仍为 `5A6FEBDEF711F81998D8CBB6DD371601358CC9C1CC6F328689187DA8E33B092A`。当前进程和这两个配置的旧根 fallback 引用数均为 0。

`PASS_SEARCH_POST_REMOVAL_CANDIDATE8_RESTART`

## 8. 删除后 API、MCP、PDF、Workspace 与 Evidence 验证

删除后重新执行的 live 验证结果：

- FastAPI `/health`：`Search` / `ok`；
- MCP `/healthz`：`ok`；
- 中文关键词“运动”：12 条；
- 英文关键词“evidence”：12 条；
- 中文高质量“避免脚步滑动”：3 条，embedding/reranker 均存在；
- 英文高质量“human motion generation”：3 条，embedding/reranker 均存在；
- UI 高质量搜索：10 条；
- UI 关键词搜索：12 条；
- MCP tools 精确为 `search`、`fetch`、`export_evidence`；
- MCP fetch 含 provenance；
- MCP export 为 Markdown，content length 1,641；
- Evidence Basket：高质量结果 10 条加入、清空成功，关键词结果 12 条加入；
- Workspace 路径 `/workspace`，返回后 query、mode、source、document ID 和 12 条 basket 状态完整恢复。

当前 PDF API 再次返回 document 1 / page 7 / chunk 66、`exact_text_location`、`exact_search`、8 个非零 bbox，PDF Range 请求返回 206 和正确 `application/pdf`。由于用户要求最终正式任务绝不添加调试参数，没有向删除后真实 Electron 注入 CDP。`data-preview-ready=true` 的等价证明由三项共同构成：

1. 删除后当前 exact locator 仍为 page 7 / chunk 66 / 8 bbox；
2. formal frontend 当前 `search.tree-hash.v1` 仍为 `3F8F169BDEADBC62F2FC6AF3F7B5EF69E44F77A592C3E814DFB6204AC58E8875`，`Search.exe` SHA256 仍为 `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD`；
3. 保留的同一 Candidate8 real-Electron 验收证明 `data-preview-ready=true`、document 1 / page 7 / chunk 66、exact、8 个 bbox 且全部位于 canvas 内。

因此删除空目录没有改变被执行的 renderer 字节、locator 数据或 Runtime；同时删除后重新执行的 UI 搜索、Workspace 和 Evidence 状态路径均通过。

`PASS_SEARCH_POST_REMOVAL_API_MCP_PDF`

## 9. Production data 前后守卫

| 守卫 | 删除前 | 删除后最终 |
| --- | ---: | ---: |
| 文件数 | 191 | 191 |
| 总字节 | 670,300,309 | 670,300,309 |
| `search.tree-hash.v1` | `0FC6E59C6A0B54469AD80D71F0E219F0C99E7BC8B3A623D1B3E020C86BDEBE20` | `0FC6E59C6A0B54469AD80D71F0E219F0C99E7BC8B3A623D1B3E020C86BDEBE20` |

最终只读审计还确认：

- SQLite：41/41 `integrity_check=ok`；
- `foreign_key_check`：41/41 为空；
- 41/41 SQLite 在只读检查前后 SHA256 不变；
- WAL/SHM：0；
- data reparse point：0；
- production write flags：全部 false。

`PASS_SEARCH_POST_REMOVAL_NO_DATA_DRIFT`

## 10. 操作边界与最终标记

本轮没有修改 ACL、没有夺取所有权、没有删除父目录、没有删除旧 Runtime task、没有强制结束 Search/Runtime、没有重启系统、没有执行 `git clean`，也没有安装或下载软件。Candidate8 最终保持 Enabled / Running，供用户继续使用；当前没有仍需用户手动处理的文件系统锁或迁移步骤。

- `PASS_SEARCH_LEGACY_ROOT_UNIQUE_DATA_AUDIT`
- `PASS_SEARCH_LEGACY_ROOT_REPARSE_POINT_AUDIT`
- `PASS_SEARCH_LEGACY_EMPTY_ROOT_REMOVAL`
- `PASS_SEARCH_POST_REMOVAL_CANDIDATE8_RESTART`
- `PASS_SEARCH_POST_REMOVAL_API_MCP_PDF`
- `PASS_SEARCH_POST_REMOVAL_NO_DATA_DRIFT`
- `LEGACY_NOTEBOOK_AI_ROOT_REMOVED`
- `LEGACY_RUNTIME_TASK_RETAINED_DISABLED`
- `SEARCH_CANONICAL_ROOT_MIGRATION_CLOSED`
