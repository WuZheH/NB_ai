# Search Candidate3 长运行、正式切换失败与回滚完成报告

## 1. 最终结论

Search 0.1.4 Candidate3 已解决 Candidate2 正式入口缺少持久模型目录配置的问题。machine-local config 的契约、真实写入、模型角色校验、显式 Electron/launcher/supervisor 传递链和路径脱敏均通过；不需要重做 machine-local config，也不需要重新审计模型路径。

Candidate3 的中英文高质量搜索、MCP `search` / `fetch` / `export_evidence`、关键词搜索，以及独立 package 的无 machine-config / 有效 machine-config 双模式 smoke 均通过。Candidate3 本身不是失败点。

正式入口的第一轮验证在 renderer PDF `first_preview` 语义条件等待 60 秒后超时，错误为 `wait_timeout:first_preview`。因此正式切换判定失败，没有继续第二、第三轮冷启动，也没有把 API/MCP 成功冒充为完整正式入口验收通过。活动入口随后安全回滚到原 Candidate2 正式副本。

用户已通过 Search 托盘菜单执行“完全退出”，Candidate3 主进程、三个 Electron 子进程及 Candidate3 自己启动的 Runtime 均正常退出。回滚并恢复旧 `NOTEBOOK_AI Runtime Launcher` 后，旧 NOTEBOOK_AI Runtime 在后续登录启动中重新运行；它属于旧启动入口，不是 Candidate3 残留。最终收尾通过旧 supervisor 的正式 `stop.request` 控制协议将该旧 Runtime 关闭，调用方没有执行 `taskkill /F`、`Stop-Process -Force` 或超时强制兜底。全部目标进程和端口最终归零。

production data 在 Candidate3 构建、双模式 smoke、正式第一轮、回滚、托盘退出、旧 Runtime 受控退出及最终只读复核后均无漂移。尚未进入 ChatGPT 外部连接配置，也没有操作 cloudflared。

最终状态：

- `PASS_SEARCH_MODEL_LOCATION_AUDIT`
- `PASS_SEARCH_MACHINE_LOCAL_MODEL_CONFIG_CONTRACT`
- `PASS_SEARCH_MACHINE_CONFIG_REAL_WRITE`
- `PASS_CANONICAL_SEARCH_PACKAGE_CANDIDATE3_BUILT`
- `PASS_CANONICAL_SEARCH_PACKAGE_CANDIDATE3_SMOKE`
- `FAIL_SEARCH_CANDIDATE3_FORMAL_PACKAGE_SWITCH`
- `PASS_SEARCH_TRAY_COMPLETE_EXIT`
- `FORMAL_SWITCH_ROLLED_BACK`
- `PASS_SEARCH_CANDIDATE3_NO_DATA_DRIFT`
- `NOT_READY_FOR_CHATGPT_EXTERNAL_CONNECTION_CONFIGURATION`

不得将本次结果表述为 Candidate3 正式切换成功，也不得据此删除旧 `D:\LEARNING\Tools\notebook_ai` 根。下一步只需修复正式入口 PDF `first_preview` 语义超时，再重新进行正式入口验收。

## 2. Source、构建与配置身份

| 项目 | 值 |
| --- | --- |
| Branch | `codex/search-canonical-root-migration` |
| Candidate3 source commit | `604e9e85fc5ed8da8f1770259a4aa43b5775a842` |
| Source commit message | `feat(search): add persistent machine-local model configuration` |
| Product / version | `Search` / `0.1.4` |
| Build ID | `20260719-search-0.1.4-canonical-root-candidate3` |
| Build timestamp UTC | `2026-07-19T15:09:50.256Z` |
| Candidate root | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c3` |
| Independent smoke root | `D:\LEARNING\Tools\SearchPackageSmoke\Search-0.1.4-canonical-c3` |
| Failed formal deployment copy | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-604e9e85` |
| Production data root | `D:\LEARNING\Tools\search\data` |
| Machine-local config | `%APPDATA%\Search\machine-config.json` |

Candidate3 构建报告为 `status=ready`：410 文件、316,500,705 字节，完整树 SHA256 为 `5CE2DEC1A17872F68A293534FEA941955F7B8A0A8163094D053F92936C9E0FD4`，`Search.exe` SHA256 为 `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD`。构建报告同时确认 `production_data_bundled=false`、`machine_local_config_bundled=false`、`current_formal_package_untouched=true`。

真实 machine-local config 保留在 `%APPDATA%\Search\machine-config.json`：schema version 1，embedding 为 `Qwen3-Embedding-0.6B`，reranker 为 `Qwen3-Reranker-0.6B`；文件 227 字节，SHA256 为 `5A6FEBDEF711F81998D8CBB6DD371601358CC9C1CC6F328689187DA8E33B092A`。两个模型仍从既有只读 `D:\LEARNING\Tools\model_cache` 读取，没有下载、复制或移动模型，没有设置系统/用户环境变量。

## 3. 模型配置问题已解决

Candidate2 正式入口失败的根因是 smoke 显式提供模型路径，而正式桌面入口没有等价持久配置。Candidate3 source commit 增加了版本无关的 `%APPDATA%\Search\machine-config.json` 契约，并将同一已验证配置从 Electron main 显式传给 launcher、supervisor、FastAPI 和 MCP。

验证结果：

- machine config 真实文件存在且为 `model_ready`；
- embedding 和 reranker 角色、结构、绝对路径和可加载状态均通过；
- health、Runtime status 和 UI 只公开状态、模型 basename 与路径摘要，不暴露绝对路径；
- 正式第一轮 Runtime identity 显示 `embedding_model_ready=true`、`reranker_model_ready=true`；
- 正式第一轮高质量搜索和 MCP 均通过，Candidate2 的模型路径失败不再复现。

因此后续修复不需要重新写 machine config，不需要增加环境变量 fallback，也不需要重新审计模型目录。

## 4. Candidate3 独立双模式 smoke

Candidate3 从独立 smoke 副本分别运行两种模式。

### 4.1 无 machine-config 模式

- package identity、Runtime identity、关键词搜索、PDF Preview、三种 viewport、独立滚动区、Workspace round-trip 和 Evidence Basket 均通过；
- 关键词搜索返回 12 条；
- 高质量搜索按契约跳过并报告 `skipped_without_machine_config`；
- 没有 cwd、旧根或 ambient environment 模型路径 fallback；
- package 仍可启动，缺配置不会伪装成空结果或触发无限重启。

### 4.2 有效 machine-config 模式

- API/MCP smoke：`status=ok`；
- 中文关键词搜索返回 12 条；
- 中文高质量检索返回 3 条，模式为 `high_quality_notebook_search_v1`；
- renderer 高质量搜索显示 10 条，backend 为 `lancedbQwen3-Reranker-0.6B`；
- MCP tools 精确为 `search`、`fetch`、`export_evidence`；
- MCP `search` 返回 3 条，`fetch` 包含 provenance，`export_evidence` 生成 Markdown 内容；
- PDF first preview 为 ready、页码 7、exact highlight、8 个合法高亮框；
- 1440 / 1600 / 1920 viewport、缩放、独立滚动、Workspace 返回恢复和 Evidence Basket 均通过。

这证明 Candidate3 独立双模式 smoke 通过，并且 machine-local config 的存在与缺失都遵循设计契约。

## 5. 正式入口第一轮与失败点

正式入口第一轮从 Scheduled Task 启动 Candidate3 正式部署副本，而不是 candidate 或 smoke 路径。API/MCP 层首先通过：

- `/health`：`app=Search`、`status=ok`；
- MCP `/healthz`：`status=ok`；
- canonical retrieval/notebook routes 存在，旧 `/api/v1/search` routes 不存在；
- 中英文关键词搜索通过；
- 中文高质量搜索通过；
- 英文高质量查询 `physical plausibility and foot sliding` 通过；
- 无结果边界返回 0 条；
- MCP `search` / `fetch` / `export_evidence` 全部通过。

随后 renderer 验收在点击正式搜索结果“预览”后等待：

```text
document.querySelector('[data-testid="pdf-location-preview"]')
  ?.dataset.previewReady === 'true'
```

该 `first_preview` 语义条件在 60 秒内未成立，失败为 `wait_timeout:first_preview`。这不是模型路径、FastAPI、MCP、搜索结果或 PDF 文件缺失问题；同一 Candidate3 独立 smoke 的 PDF Preview 已通过。失败边界被精确限定为正式入口 renderer 的 PDF first-preview semantic-ready 超时。

因此：

- `FAIL_SEARCH_CANDIDATE3_FORMAL_PACKAGE_SWITCH`；
- 没有继续第二、第三轮真实冷启动；
- 没有继续把 Workspace、Evidence Basket 等后续正式 renderer 步骤标记为通过；
- 没有重新切换正式入口，没有构建 Candidate4。

## 6. 托盘完全退出、旧 Runtime 与最终进程状态

用户从 Search 托盘菜单执行“完全退出”。Candidate3 主进程 PID 32624、三个 Electron 子进程、Candidate3 supervisor/FastAPI/MCP 和 Candidate3 Search Runtime 均退出；`%LOCALAPPDATA%\Search\runtime\status.json` 记录 Candidate3 `state=stopped`。

活动入口回滚后，旧 `NOTEBOOK_AI Runtime Launcher` 恢复启用。后续登录启动于 2026-07-20 11:00（Asia/Shanghai）重新启动了旧 Runtime：

| 组件 | PID | Executable / command | cwd / port |
| --- | ---: | --- | --- |
| 旧 supervisor | 28572 | 既有 NOTEBOOK_AI Python；旧根 `notebook_ai_launcher.py supervise` | `D:\LEARNING\Tools\notebook_ai` |
| 旧 FastAPI | 15272 | 既有 NOTEBOOK_AI Python；`-m uvicorn app.main:app` | `D:\LEARNING\Tools\notebook_ai` / 8000 |
| 旧 MCP | 12608 | 既有 Node；旧根 MCP `dist/server/index.js` | 旧根 MCP integration / 8787 |

FastAPI 和 MCP 都以 28572 为父进程，进程路径、cwd、父子关系和端口归属一致。它们由恢复后的旧启动入口产生，不得描述为 Candidate3 残留。

2026-07-20T05:44:03.757573Z，只向 `%LOCALAPPDATA%\NOTEBOOK_AI\runtime\stop.request` 写入一次正式 `{action: stop}` 请求。约 2 秒后旧 supervisor、FastAPI 和 MCP 均退出；调用方只轮询 60 秒上限，没有调用 `RuntimeController.stop()` 的强制兜底，没有使用 `taskkill /F` 或 `Stop-Process -Force`。

最终只读复核：

| 项目 | 最终值 |
| --- | ---: |
| Candidate3 主进程 PID 32624 | 0 |
| 旧 supervisor PID 28572 | 0 |
| 旧 FastAPI PID 15272 | 0 |
| 旧 MCP PID 12608 | 0 |
| Search.exe | 0 |
| Electron | 0 |
| supervisor | 0 |
| FastAPI | 0 |
| MCP | 0 |
| orphan Search/Electron/NOTEBOOK_AI Runtime | 0 |
| 5173 / 8000 / 8787 / 18080 / 18787 / 19222 / 19223 listeners | 0 |

## 7. 活动入口回滚复核

| 项目 | 最终状态 |
| --- | --- |
| `Search Desktop` Scheduled Task | 保留且禁用 |
| `Search Desktop` executable | 原 Candidate2 正式副本 `D:\LEARNING\Tools\search\integrations\search_desktop\dist\win-unpacked\Search.exe` |
| `Search Desktop` working directory | 原 Candidate2 正式副本目录 |
| `NOTEBOOK_AI Runtime Launcher` | 恢复启用，当前无运行进程 |
| Candidate3 | 不是活动正式入口 |
| Registry Run / Startup / shortcuts | 本轮未修改 |

Candidate3 正式部署副本保留在 `dist\formal\Search-0.1.4-604e9e85`，但计划任务不指向它。Candidate1、Candidate2、Candidate3、三个独立 smoke、旧正式包、Candidate3 正式部署副本和失败证据全部保留。

本次失败证据位于 `D:\LEARNING\Tools\search\.codex_tmp\candidate3-longrun`，其中包括双模式 smoke、正式第一轮 API/MCP 结果、renderer probe、回滚脚本、任务快照和两次 data guard。没有删除任何 candidate、smoke、旧正式包、machine-config 或失败证据。

## 8. Production data 最终只读守卫

Candidate3 smoke 后、正式回滚后以及旧 Runtime graceful-stop 后的最终逐文件扫描完全一致。最终扫描将当前 191 个文件逐一与既有 guard 的相对路径、字节数和 SHA256 比对：0 缺失、0 新增、0 变化。

| 项目 | 最终值 |
| --- | ---: |
| 文件 | 191 |
| 目录（含根） | 92 |
| 总字节 | 670,300,309 |
| content tree SHA256 | `2cfa22f884782ce3910f6884a6db72f51f794a388a704b15f7168a68f8d01bc7` |
| structure tree SHA256 | `3699ed3ec6332569aadf4a0e903b07d881bd792701ecc47e352491139d1dac67` |
| full tree SHA256 | `7de213d494bb5387b21e037248a2da4fcf3c51dbaf148cfcc28acb5240e37c64` |
| SQLite `integrity_check` | 41/41 `ok` |
| SQLite `foreign_key_check` | 41/41 `[]` |
| FTS | 11,803 |
| passage vectors | 11,373 |
| object vectors | 35 |
| legacy vectors | 6,114 |
| Zotero note vectors | 161 |
| WAL/SHM | 0 |
| missing / extra / changed files | 0 / 0 / 0 |

SQLite 复核使用 `mode=ro&immutable=1` 和 `PRAGMA query_only=ON`；检查后文件数、总字节和 WAL/SHM 再次相同。没有写 production SQLite、FTS、LanceDB、legacy vectors、Zotero notes、PDF、Markdown、manifest、exports 或 backups。

## 9. Cloudflared、ChatGPT 与本机变更边界

- 尚未进入 ChatGPT 外部连接配置；
- 没有启动、停止、配置或修改 cloudflared；
- 没有修改 ChatGPT App 外部配置；
- 没有安装或下载软件、依赖或模型；
- 没有修改永久环境变量、PATH 或注册表；
- C 盘写入仅包括先前批准的 `%APPDATA%\Search\machine-config.json`、Search 用户状态，以及本次批准的 `%LOCALAPPDATA%\NOTEBOOK_AI` stop request / runtime 状态更新；production data 位于 D 盘且无变化。

由于正式入口未通过，不满足旧 `D:\LEARNING\Tools\notebook_ai` 根安全清退的前置条件。旧 Runtime 启动任务目前仍引用该旧根，严禁删除旧根。

## 10. 保留资产与下一步

保留资产：

- Candidate1、Candidate2、Candidate3；
- Candidate1/2/3 独立 smoke；
- 当前原 Candidate2 正式副本；
- Candidate3 正式部署失败副本；
- 旧正式包和回滚任务记录；
- `%APPDATA%\Search\machine-config.json`；
- Candidate3 source commit 和本报告；
- `candidate3-longrun` 下的全部 smoke、正式失败和 data guard 证据；
- production data 原始内容。

下一步范围严格限定为：修复正式入口 renderer 的 PDF `first_preview` semantic-ready 超时，然后从正式入口重新执行三轮真实冷启动、完整 renderer 验收、托盘完全退出和无漂移检查。

下一步不需要：

- 重做 machine-local config；
- 重新审计 embedding/reranker 模型路径；
- 构建 Candidate4（除非修复后另行明确批准）；
- 修改 production data；
- 操作 cloudflared；
- 修改 ChatGPT 外部配置；
- 删除任何 candidate、smoke、旧正式包、machine-config 或旧根。
