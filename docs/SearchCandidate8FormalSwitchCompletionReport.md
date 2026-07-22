# Search Candidate8 正式切换完成报告

## 1. 最终结论

Search 0.1.4 Candidate8 已完成构建、四场景 packaged smoke、真实 API/MCP 验收、真实 Electron renderer 验收、正式目录部署、三轮计划任务冷启动、三轮托盘完全退出、最终无调试参数启动和 production data 守卫。

最终活动状态：

- `Search Desktop` 已启用并运行，唯一入口为 Candidate8 正式副本；
- `NOTEBOOK_AI Runtime Launcher` 已停用，但任务与旧根均保留用于回滚；
- FastAPI 8000、MCP 8787、renderer 5173 均 ready；
- 最终任务无命令行参数，验收调试端口 19224 不存在；
- Search Runtime build ID、source commit、branch 与 production data root 全部正确；
- production data 文件数、字节数和 `search.tree-hash.v1` SHA256 与既有基线完全一致；
- Candidate6/7、旧正式包、旧根和所有失败证据均未删除；
- 未创建 GitHub Release，未删除旧 `D:\LEARNING\Tools\notebook_ai` 根。

结论：`PASS_SEARCH_CANDIDATE8_FORMAL_PACKAGE_SWITCH`。

## 2. Source identity 与提交

| 项目 | 值 |
| --- | --- |
| Repository | `WuZheH/NB_ai` |
| Branch | `codex/search-canonical-root-migration` |
| Candidate8 source commit | `db92824c2551156df6c441db8233ae96386cb851` |
| PDF render retry commit | `84db3de` — `fix(search): retry stalled PDF renders` |
| Build contract test commit | `db92824` — `test(build): allow scoped failed artifact invalidation` |
| Build ID | `20260722-search-0.1.4-canonical-root-candidate8` |
| Build timestamp UTC | `2026-07-22T07:50:53.935Z` |
| Product / version | `Search` / `0.1.4` |

Candidate8 构建前 tracked worktree clean；source commit 已推送，构建时本地 HEAD 与远端 branch 一致。

## 3. Candidate6 与 Candidate7 收敛过程

Candidate6 在打包完成后的 identity 校验阶段暴露 PowerShell 7 `ConvertFrom-Json` 会把 ISO timestamp 解析为 `DateTime` 的问题。构建脚本随后：

- 用 invariant UTC ISO 字符串统一比较 build timestamp；
- 对任何晚期构建失败只失效该 candidate 内误导性的 `Search.exe`；
- 保留失败目录和证据，Candidate6 的 executable 可由重建恢复；
- 不触碰当前正式包或其他 candidate。

Candidate7 构建及四场景 smoke 通过，正式切换后 API/MCP 通过，但外部无头 Edge 的 PDF `page.render(...).promise` 可永久挂起。第一次正式切换按门槛回滚，旧 Runtime 恢复健康。进一步阶段日志证明：PDF worker、5.76 MB PDF、目标页面与 canvas 尺寸均已成功加载，挂点精确位于 `page.render`。

Candidate8 增加了有上限的渲染恢复：单次 render 15 秒未收敛时取消当前任务并重建一次；第二次仍失败则显示结构化 render error，不无限重试、不假 ready。真实 Electron renderer 中首次 preview 均由 attempt 2 恢复并完成 semantic-ready。

最终正式 renderer 验收改为直接连接带临时本机 CDP 端口的 Electron renderer，不再用外部无头 Edge 代替产品 renderer。调试参数只用于三轮验收，最终任务已恢复为无参数状态。

## 4. 全量测试

完整回归证据：

`D:\LEARNING\Tools\search\.codex_tmp\test-all\20260722-154949-0f4b3500`

| 套件 | 结果 |
| --- | ---: |
| Core | 207/207 PASS |
| Frontend | 32/32 PASS |
| Desktop | 74/74 PASS |
| MCP | 25/25 PASS |
| Vite production build | PASS，133 modules |

相关定向测试还覆盖：

- PDF render timeout、最多一次 retry、retry stage 与 epoch 重建契约；
- build timestamp 类型归一化；
- 晚期失败只移除 candidate 内 `win-unpacked/Search.exe`；
- packaged source、machine config、data-root、single-instance、tray、runtime ownership 与 tree hash 契约。

## 5. Candidate8 构建身份

| 项目 | 值 |
| --- | --- |
| Candidate root | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c8` |
| Formal deployment | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-db92824c\win-unpacked` |
| File count | 411 |
| Total bytes | 316,528,547 |
| `Search.exe` SHA256 | `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD` |
| `resources/app` SHA256 | `23DAE08F6D5DDC7455162C30190B04F1F102D7A20F207226DC9C502D726C96DF` |
| Frontend tree SHA256 | `3F8F169BDEADBC62F2FC6AF3F7B5EF69E44F77A592C3E814DFB6204AC58E8875` |
| Complete tree SHA256 | `6589A0E3B988DB5FDB94240193EFAEE2FB9D98DA18322A570F4CBB3C2EA5886D` |
| Tree hash schema | `search.tree-hash.v1` |

Candidate 与 formal copy 均为 411 文件、316,528,547 字节，完整树 SHA256 相同。构建报告确认：

- `production_data_bundled=false`；
- `machine_local_config_bundled=false`；
- `desktop_runtime_config_bundled=false`；
- `current_formal_package_untouched=true`。

## 6. 四场景 packaged smoke

证据根：

`D:\LEARNING\Tools\search\.codex_tmp\candidate8-provisioning\packaged-smoke`

| 场景 | 结果 | Runtime 行为 |
| --- | --- | --- |
| missing | structured unavailable | `desktop_runtime_config_missing`，不 spawn |
| invalid | structured unavailable | `desktop_runtime_config_invalid_json`，不 spawn |
| valid | ready | Electron 启动并拥有 Runtime |
| legacy-migration | ready | 迁移到 userData config，保留 legacy backup |

四个场景均验证：

- duplicate instance 被复用；
- 通过真实托盘“完全退出”；
- 无可见 console；
- 未启动 cloudflared；
- 退出后 Runtime residual 为 0；
- userData config 优先级与备份契约正确。

## 7. API、MCP 与 renderer 功能验收

API/MCP 证据：

`D:\LEARNING\Tools\search\.codex_tmp\candidate8-functional`

真实 Electron renderer 证据：

`D:\LEARNING\Tools\search\.codex_tmp\candidate8-electron-functional`

通过项：

- `/health` 返回 `app=Search`、`status=ok`；
- MCP `/healthz` 返回 ready；
- canonical retrieval/notebook routes 存在，legacy routes 不存在；
- 中文关键词“运动”返回 12 条；
- 英文关键词返回 5 条，无结果边界返回 0；
- 高质量 API 返回 3 条，embedding/reranker 均存在；
- renderer 高质量搜索显示 10 条；
- MCP tools 精确为 `search`、`fetch`、`export_evidence`；
- MCP fetch 含 provenance，export 生成 Markdown；
- PDF preview 为 document 1、page 7、chunk 66、exact、8 个高亮；
- 1440 / 1600 / 1920 viewport、缩放、bbox containment 均通过；
- results、PDF 与 evidence basket 为独立滚动区；
- Workspace 往返后 query、filters、preview 和 basket 恢复；
- 高质量结果加入篮子并清空、关键词篮子 12 条均通过。

## 8. 三轮正式冷启动

正式证据根：

`D:\LEARNING\Tools\search\.codex_tmp\candidate8-formal`

| 轮次 | Ready 时间 | API/MCP | Electron renderer | 托盘完全退出 | 残留端口 |
| --- | ---: | --- | --- | --- | ---: |
| 1 | 10.047 s | PASS | PASS | PASS | 0 |
| 2 | 9.512 s | PASS | PASS | PASS | 0 |
| 3 | 9.489 s | PASS | PASS | PASS | 0 |

每轮都从真实 `Search Desktop` Scheduled Task 冷启动，并验证：

- executable 为 Candidate8 formal copy；
- build ID 为 Candidate8；
- source commit 为 `db92824c...`；
- data root 为 `D:\LEARNING\Tools\search\data`；
- FastAPI、MCP、supervisor ready；
- tunnel 未配置只产生预期 degraded 状态，不阻塞 local ready；
- 进程命令行不引用旧 `D:\LEARNING\Tools\notebook_ai`；
- 退出后 Search 进程、5173、8000、8787、19224 全部归零。

三轮结束后已移除 `--remote-debugging-port=19224`。最终又以无参数任务进行第 4 次运行启动，9.423 秒 ready，并保持运行供用户使用。

## 9. 最终任务、配置与运行状态

| 项目 | 最终状态 |
| --- | --- |
| `Search Desktop` | Enabled / Running |
| Executable | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-db92824c\win-unpacked\Search.exe` |
| Arguments | none |
| `NOTEBOOK_AI Runtime Launcher` | Disabled |
| Search processes | 4，全部来自 Candidate8 formal executable |
| 5173 / 8000 / 8787 | listening / healthy |
| 19224 | not listening |
| Runtime state | `local_ready_tunnel_missing` |
| Embedding / reranker | ready / ready |

Machine-local files：

| 文件 | SHA256 |
| --- | --- |
| `%APPDATA%\Search\desktop-runtime.json` | `3FD0B64B20FEAE365A8B6A0CE2C34EE23E1DD73B0F15A259E1ADB722D946826B` |
| `%APPDATA%\Search\machine-config.json` | `5A6FEBDEF711F81998D8CBB6DD371601358CC9C1CC6F328689187DA8E33B092A` |

最终状态快照：

`D:\LEARNING\Tools\search\.codex_tmp\candidate8-formal\final-state.json`

## 10. Production data 守卫

最终只读 tree hash：

| 项目 | 值 |
| --- | ---: |
| File count | 191 |
| Total bytes | 670,300,309 |
| `search.tree-hash.v1` SHA256 | `0FC6E59C6A0B54469AD80D71F0E219F0C99E7BC8B3A623D1B3E020C86BDEBE20` |

该结果与 Candidate7 前、Candidate7 后、Candidate8 前的既有基线完全一致。没有写 production SQLite、FTS、LanceDB、legacy vectors、Zotero notes、PDF、Markdown 或 manifest；health 也持续报告所有 production write flags 为 false。

## 11. 保留资产与删除边界

本轮未删除：

- `D:\LEARNING\Tools\notebook_ai` 旧根及其 dirty worktree；
- `NOTEBOOK_AI Runtime Launcher` 任务定义；
- 旧 Search 正式包；
- Candidate6 失败目录；
- Candidate7 candidate 与 formal copy；
- Candidate8 candidate、formal copy 与全部验收证据；
- migration safety archive。

Candidate6 晚期失败产生的误导性 `Search.exe` 已由构建脚本在 Candidate6 自身目录内失效；该文件可通过重新构建恢复，其他文件与报告未删除。

正式切换已成功，因此后续可以单独进行旧根清退审计；但删除旧根、旧任务或其他回滚资产属于独立破坏性步骤，本轮没有获得该授权，也没有执行。

## 12. 最终状态标记

- `PASS_SEARCH_PDF_RENDER_RETRY_CONTRACT`
- `PASS_SEARCH_CANDIDATE8_FULL_REGRESSION`
- `PASS_SEARCH_CANDIDATE8_BUILD`
- `PASS_SEARCH_CANDIDATE8_FOUR_PROVISIONING_SCENARIOS`
- `PASS_SEARCH_CANDIDATE8_API_MCP_ACCEPTANCE`
- `PASS_SEARCH_CANDIDATE8_REAL_ELECTRON_RENDERER_ACCEPTANCE`
- `PASS_SEARCH_CANDIDATE8_THREE_FORMAL_COLD_STARTS`
- `PASS_SEARCH_CANDIDATE8_FORMAL_PACKAGE_SWITCH`
- `PASS_SEARCH_CANDIDATE8_NO_DATA_DRIFT`
- `OLD_ROOT_RETAINED_PENDING_SEPARATE_DESTRUCTIVE_APPROVAL`
