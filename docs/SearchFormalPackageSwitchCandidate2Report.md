# Search candidate2 正式包切换与日常使用验证报告

## 1. 结论

本次从 canonical Search 根对 candidate2 进行了可回滚的正式部署和真实入口启动验证。Candidate2 的 package 身份、自包含 Runtime、FastAPI、MCP 健康、canonical data root、关键词检索、真实托盘完全退出和生产数据无漂移均得到确认。

正式日常使用验收没有通过。正式入口没有持久的模型目录配置，packaged Runtime 将 embedding/reranker 默认解析到不存在的 `D:\LEARNING\Tools\search\data\models`；实际只读模型位于 package 外部。因此高质量搜索返回 HTTP 500，passage/object 向量搜索报告 `vector_store_stale`，MCP `search` 返回错误。Candidate smoke 曾通过，是因为 smoke 进程显式提供了模型路径；该测试配置不能替代正式入口配置。

发现阻塞后没有执行三轮冷启动，也没有继续 PDF/Workspace/Evidence Basket 的正式日常验收，没有修改系统环境变量或增加临时 launcher fallback。用户通过真实托盘菜单执行“完全退出”后，所有进程和端口正常释放。随后活动自动入口已安全回滚：新 `Search Desktop` 任务保留但禁用，原 `NOTEBOOK_AI Runtime Launcher` 恢复启用。Candidate2 正式部署副本、candidate1/2、smoke 副本、旧正式 package 和全部回滚证据均保留。

本报告的真实状态为：

- `PASS_SEARCH_TRAY_COMPLETE_EXIT`
- `PASS_SEARCH_FORMAL_SWITCH_NO_DATA_DRIFT`
- `PASS_SEARCH_FORMAL_SWITCH_ROLLBACK_READY`
- `FAIL_SEARCH_DAILY_USE_HIGH_QUALITY_MODEL_PATH_CONFIGURATION`
- `FORMAL_SWITCH_ROLLED_BACK`
- `NOT_READY_FOR_CHATGPT_EXTERNAL_CONNECTION_CONFIGURATION`

不得将本次结果表述为 candidate2 已正式切换通过。

## 2. 基线与批准候选

| 项目 | 值 |
| --- | --- |
| 报告基线 HEAD | `05975891a5902f3154ef10ab57fd51ef315c0c09` |
| Branch | `codex/search-canonical-root-migration` |
| Candidate2 source commit | `99dab087c78afd030d96d2b2d4e7e6efcb5c067a` |
| Build ID | `20260719-search-0.1.4-canonical-root-candidate2` |
| Candidate root | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c2` |
| Candidate stable tree SHA256 | `07EB57990E849C4D43312C17E9466B7A2DB7AF0173102427CAE3C4BEC3F2FC03` |
| Candidate Search.exe SHA256 | `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD` |
| Production data root | `D:\LEARNING\Tools\search\data` |

执行前 HEAD、upstream 0/0、tracked clean、候选文件数 409、候选字节 316,482,767、候选哈希、Search.exe 哈希、Runtime 进程和端口、生产数据以及旧仓库 57 modified / 1,253 untracked / 0 staged 均符合批准基线。

## 3. 切换前正式入口审计

| 入口类型 | 名称 | 切换前 target / working directory | 自动启动 | 处置与回滚值 |
| --- | --- | --- | --- | --- |
| 桌面快捷方式 | 无 | 无 | 否 | 未创建第二套快捷方式 |
| 开始菜单快捷方式 | 无 | 无 | 否 | 未修改 |
| Taskbar 固定项 | 未发现可解析 Search 入口 | 无 | 否 | 未修改 |
| Startup 文件夹 | 无 Search 入口 | 无 | 否 | 未修改 |
| Registry Run | 无 Search 入口 | 无 | 否 | 未修改注册表 |
| Scheduled Task | `NOTEBOOK_AI Runtime Launcher` | 旧根 Python launcher；工作目录为旧 `notebook_ai` 根 | 是 | 验证期间禁用；失败后恢复启用 |
| Scheduled Task | `Search Desktop` | 切换前不存在 | 否 | 创建后用于真实入口验证；失败后保留但禁用 |

项目已有明确正式契约：固定 unpacked package 位于 `integrations/search_desktop/dist/win-unpacked`，自动启动由当前用户 `Search Desktop` Scheduled Task 直接运行 packaged `Search.exe`，不使用 Registry Run 或系统服务。因此没有发明新的 wrapper、快捷方式或安装机制。

切换前旧正式 package 位于：

`D:\LEARNING\Tools\notebook_ai\integrations\search_desktop\dist\win-unpacked`

旧 package 仍完整保留：

- 419 文件；
- 316,773,511 字节；
- stable tree SHA256 `6C37B2F90762C249A756D5B197845F850EF5FE97E05BE1312CA0B93FED8E98E5`；
- Search.exe SHA256 `15E02071BD5A688E7A5CA8C0A2E3FD00D45A7405B908EF9A59542C44012A5498`；
- 旧 package 没有新的 build identity resource；
- 旧 machine-local config SHA256 `AE1626A99E14B8E99C485B6D034256B37A00C3E8857CE0BBB0F82C577559994E`。

## 4. 回滚快照

回滚证据位于：

`D:\LEARNING\Tools\search\.codex_tmp\formal-switch-c2\rollback`

其中记录或保存：

- 旧正式 package 路径、tree hash 和 Search.exe hash；
- 旧 machine-local config 的只读副本及 SHA256；
- 旧 Scheduled Task XML；
- 切换前所有入口审计结果；
- 原 data root、user-data、Runtime state 和日志目录；
- 快捷方式、Startup、Run entry 不存在的结果。

没有复制 production data。回滚不需要重建 package、修改数据库或重建索引。

## 5. Candidate2 正式部署

Candidate2 构建目录保持不变。`win-unpacked` 被非覆盖复制到 canonical 固定正式目录：

`D:\LEARNING\Tools\search\integrations\search_desktop\dist\win-unpacked`

复制后、写入 machine-local config 前，正式目录与 candidate2 `win-unpacked` 完全一致：

- 407 文件；
- 316,479,830 字节；
- stable tree SHA256 `FA11A0A470BCDF67CFFBCE50D88BCACFD2EFFFDAB2BAC2E716C16D65FF069794`。

随后只在正式部署副本创建受支持的 `search-desktop.local.json`：

- schema version 3；
- data root 为 canonical `data`；
- Python/Node 指向现有本机解释器；
- cloudflared 留空；
- local config SHA256 `9C01D83B388ED22E86CE6DC4B3CCEAACF3926A5EE7DDCE36632A3966E85AE306`。

加入 local config 后正式目录为 408 文件、316,480,063 字节，stable tree SHA256 为 `82A3E1B19C15ECC71CC16EACF896C32A0914BA971F570A11B43573AEC4E64729`。Search.exe hash 仍与 candidate2 一致。Production data、用户状态和日志没有写入 package 目录。

## 6. 正式入口与运行身份

使用仓库正式 `install-autostart.ps1` 创建当前用户 `Search Desktop` Scheduled Task：

- executable：canonical 固定正式目录的 `Search.exe`；
- working directory：同一正式目录；
- arguments：无；
- logon delay：20 秒；
- run level：Limited；
- multiple instances：IgnoreNew。

真实启动通过 `Start-ScheduledTask -TaskName "Search Desktop"` 触发，没有直接运行 candidate 或 smoke `Search.exe`，没有测试端口、测试 user-data 或测试参数。

启动结果：

- Search 主进程来自 canonical 正式目录；
- 主窗口标题为 `Search`，单一主实例；
- renderer、GPU 和 network utility 均来自同一正式目录；
- supervisor、FastAPI 和 MCP 均来自正式 package 内 `runtime-project`；
- Python 和 Node 使用受支持的 existing local config；
- user-data 为 `%APPDATA%\Search`；
- Runtime state/log 为 `%LOCALAPPDATA%\Search`；
- 8000、8787、5173 的 owner 均为本次正式链路；
- 没有使用旧 `notebook_ai` 代码或 candidate/smoke 资源；
- 没有启动或修改 cloudflared。

Runtime status 与 renderer build identity 一致：

| 字段 | 值 |
| --- | --- |
| product | `Search` |
| version | `0.1.4` |
| build_id | `20260719-search-0.1.4-canonical-root-candidate2` |
| source_commit | `99dab087c78afd030d96d2b2d4e7e6efcb5c067a` |
| source_branch | `codex/search-canonical-root-migration` |
| data_root | `D:\LEARNING\Tools\search\data` |
| FastAPI | `ready` |
| MCP | `ready` |
| Tunnel | `tunnel_not_configured`，只读诊断 |

`/health` 返回 `app=Search`、`status=ok`；MCP `/healthz` 返回 `status=ok`。

## 7. 日常使用验证结果

### 7.1 已通过

- 中文关键词检索：12 条，document_id 和 fragment_id 全部有效；
- 英文关键词检索：12 条，document_id 和 fragment_id 全部有效；
- 明确无结果查询：0 条；
- 所有关键词响应写入标志均为 false；
- MCP server identity 为 `search`；
- MCP tools 为 `search`、`fetch`、`export_evidence`；
- MCP 空查询/错误 limit 返回预期错误；
- MCP 不存在 fragment 返回预期错误；
- production bundle 显示 Search 品牌和唯一一个“搜索”入口，没有旧双搜索入口。

### 7.2 阻塞

正式入口下高质量检索返回 HTTP 500。只读诊断证明：

- `SEARCH_MODEL_CACHE_DIR`、`SEARCH_EMBEDDING_MODEL`、`SEARCH_RERANKER_MODEL` 没有 user/system 持久配置；
- packaged 默认解析 model cache 为 `D:\LEARNING\Tools\search\data\models`；
- 该目录以及两个默认模型目录不存在；
- vector manifest 记录的实际 embedding model path 为外部 `D:\LEARNING\Tools\model_cache\Qwen3-Embedding-0.6B`；
- vector status 为 `vector_store_stale` / `manifest_mismatch`；
- passage/object count 本身仍为 11,373 / 35，但向量搜索返回 0；
- MCP `search` 因同一 high-quality backend 错误返回 `isError=true`。

Candidate2 smoke 中该链路通过，是因为 smoke 启动器显式设置了 process-local model paths；正式 `Search Desktop` Task 没有这些环境，且当前 `search-desktop.local.json` schema 不支持模型目录字段。把 smoke 环境写入系统环境变量、增加 wrapper、复制模型进 production data 或硬编码本机路径都会违背正式产品边界，因此没有采用。

本阻塞会直接影响 Search 在 ChatGPT 中的日常 `search` 调用。MCP server ready 不能替代 MCP tool 成功。

### 7.3 因阻塞未执行

发现正式高质量/MCP search 阻塞后，按停止条件没有继续：

- 三轮完整冷启动循环；
- 正式入口 PDF Preview/Workspace round-trip；
- Evidence Basket 完整交互；
- minimize/restore/close-to-tray/reopen 的完整循环；
- 外部 ChatGPT Tunnel 或 ChatGPT App 验证。

Candidate2 独立 smoke 中上述 PDF/Workspace/Evidence Basket 契约仍保持已通过，但不能替代本次正式入口日常验收。

## 8. 真实托盘完全退出

由于本任务明确不使用 Computer Use，用户从真实托盘菜单执行：

`右键 Search 托盘图标 → 完全退出`

随后只读复核：

- Runtime status 为 `stopped`；
- Search.exe：0；
- supervisor：0；
- FastAPI：0；
- MCP：0；
- orphan Search process：0；
- 8000、8787、5173、18080、18787、19222、19223：全部释放；
- FastAPI/MCP/supervisor component state：全部 `stopped`。

因此真实托盘完全退出路径通过。

## 9. Production data 无漂移

托盘退出释放数据库锁后，使用正式 packaged runtime 的只读数据守卫重新验证：

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
| PDF | 8，aggregate hash 未变化 |
| WAL/SHM | 0 |
| manifest | 原始字节未变化 |

启动、关键词检索、失败的高质量调用和托盘退出均未改变 production SQLite、FTS、LanceDB、manifest、PDF、Zotero、notes 或 exports。

## 10. 回滚结果

因为正式日常验收失败，活动启动入口已回滚：

- `Search Desktop` Scheduled Task：保留，`Disabled`；
- `NOTEBOOK_AI Runtime Launcher` Scheduled Task：恢复为 `Enabled / Ready`；
- candidate2 canonical 正式部署副本：保留；
- candidate1、candidate2 和两份 smoke：保留；
- 旧正式 package：保留；
- production data：未修改。

没有删除 Scheduled Task、package、candidate、smoke、旧目录或用户状态。恢复 candidate2 验证只需在修复 package-local model path contract 并生成新候选后，重新校验并切换 task enabled state；回滚不需要覆盖 package 或修改数据，可在十分钟内完成。

## 11. 后续前置条件

进入下一次正式切换前，应先建立正式 package-local 模型路径契约：

1. machine-local config 显式支持 model cache、embedding model 和 reranker model；
2. Electron launcher 将解析后的路径作为 child-process environment 传给 packaged Runtime；
3. 不修改系统环境变量，不新增 wrapper，不硬编码当前机器路径；
4. 缺失模型时设置页和 Runtime status 返回明确诊断，而不是 generic HTTP 500；
5. 重新构建新 candidate，并从无测试参数的正式入口验证 high-quality、passage、object 和 MCP `search`；
6. 全部通过后再执行三轮冷启动和正式 PDF/Workspace/Evidence Basket 验收。

外部 ChatGPT HTTPS Tunnel 仍未配置。本次没有操作 cloudflared，也没有修改 ChatGPT App；在本地正式 MCP `search` 通过之前，不应进入外部连接配置。
