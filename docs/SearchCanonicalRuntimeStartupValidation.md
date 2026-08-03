# Search canonical Runtime 启动验证

## 1. 验证范围

本报告记录 Search 从 canonical Git 根和迁移后的正式数据启动、只读运行、受控退出及回归测试的结果。

- 验证日期：2026-07-18
- canonical 根：D:\LEARNING\Tools\search
- 分支：codex/search-canonical-root-migration
- 验证源码基线：2b899552a9f1313f34b74363a24a9a3b9fba7987
- 正式数据根：D:\LEARNING\Tools\search\data
- 详细日志：.codex_tmp\canonical-runtime-validation（Git 忽略）

本次未启动正式 Search.exe，未切换正式包或快捷方式，未修改 Tunnel、ChatGPT App、系统环境变量、PATH 或注册表。

## 2. 启动边界

源码 Runtime 使用 canonical launcher 的绝对路径启动和停止：

~~~powershell
D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe -B D:\LEARNING\Tools\search\scripts\runtime\notebook_ai_launcher.py start
D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe -B D:\LEARNING\Tools\search\scripts\runtime\notebook_ai_launcher.py stop
~~~

每次启动均显式设置：

- SEARCH_DATA_DIR 指向 canonical data；
- runtime、log、config、LOCALAPPDATA、APPDATA 和 temp 指向 canonical 根内独立验证目录；
- 明确的 FastAPI 端口、MCP 端口和 backend URL；
- NOTEBOOK_AI_VECTOR_STORE_WORKER_ENABLED=0；
- NOTEBOOK_AI_VECTOR_STORE_AUTO_SYNC_ENABLED=0；
- HF_HUB_OFFLINE=1 与 TRANSFORMERS_OFFLINE=1；
- 清空进程级 PYTHONPATH、NODE_PATH 和旧项目根变量。

Python conda 环境名中的 NOTEBOOK_AI 是保留的内部技术标识，不是旧项目路径依赖。

### 外置模型缓存

正式 vector manifest 绑定可配置的外置模型缓存：

- D:\LEARNING\Tools\model_cache\Qwen3-Embedding-0.6B；
- D:\LEARNING\Tools\model_cache\Qwen3-Reranker-0.6B。

两个目录均存在、不是 reparse point，也不位于旧 notebook_ai 项目根。验证通过进程级 SEARCH_MODEL_CACHE_DIR、SEARCH_EMBEDDING_MODEL 和 SEARCH_RERANKER_MODEL 使用它们，并强制离线模式。验证前后文件数和总字节保持 25 / 1,207,490,335 与 29 / 1,207,489,846，不存在下载或新增模型文件。

## 3. 启动前冻结检查

启动前确认：

- 8000、8787、18080、18787 均未监听；
- 旧、新 Search Runtime 进程均为 0；
- canonical Git 工作树 clean；
- 旧主仓库保持 57 modified、1,253 untracked、0 staged；
- production data 为 191 个文件、92 个目录、670,300,309 字节；
- 完整树 SHA256 为 7de213d494bb5387b21e037248a2da4fcf3c51dbaf148cfcc28acb5240e37c64；
- 未发现 SQLite WAL/SHM；
- 未发现相关 Python、Node、Electron 或 SQLite 进程持有旧或新 data；
- 未启动、停止或修改 cloudflared。

## 4. 隔离端口验证

隔离端口为 FastAPI 18080、MCP 18787。正式模型查询前，Runtime 使用新的状态目录并在强制离线模式下重新启动；前一次进程已先通过正式 stop 完整退出，数据 hash 未变化。

### 4.1 进程与端口

| 组件 | PID | Parent PID | 工作目录 | 端口 |
| --- | ---: | ---: | --- | ---: |
| supervisor | 3352 | 44892 | D:\LEARNING\Tools\search | - |
| FastAPI | 11840 | 3352 | D:\LEARNING\Tools\search | 18080 |
| MCP | 40720 | 3352 | D:\LEARNING\Tools\search\integrations\notebook_ai_chatgpt_app | 18787 |

三个组件均为 launcher owned process。FastAPI 和 MCP 的监听 PID 与 Runtime status 一致。命令行、cwd、模块和 MCP bundle 均来自 canonical 根；旧主仓库、worktree、clean clone、candidate、win-unpacked 和 dist-candidates 命中均为 0。

### 4.2 健康、状态和路由

- FastAPI GET /health：status=ok、app=Search、所有写标志为 false；
- launcher status：local_ready_tunnel_missing；
- FastAPI 和 MCP component：ready；
- MCP GET /healthz：status=ok；
- Tunnel：type=none、tunnel_not_configured，不阻止本地就绪；
- 设置页消费的状态包含 Tunnel 类型、状态和检查时间；对应只读诊断文案契约通过；
- 没有调用任何 Tunnel 启动、配置、暂停或恢复能力；
- canonical POST /api/v1/retrieval/search 实际请求成功；
- /api/v1/search 返回 404；
- /api/v1/search/database 返回 404；
- app.main 共枚举 90 个唯一路由。

## 5. 正式数据只读验证

所有结果只记录计数、状态和标识有效性，不记录 PDF 正文、笔记正文、Zotero 正文或搜索结果内容。

| 数据集 | 结果 |
| --- | --- |
| documents | 10 |
| knowledge_chunks | 11,380 |
| FTS | ready；11,803 fragments；integrity ok |
| LanceDB passage vectors | 11,373 |
| LanceDB object vectors | 35 |
| legacy vector index | 6,114；manifest 与 JSONL 一致 |
| Zotero note vectors | ready；161；1,024 维 |
| vector freshness | available=true；stale=false；complete=true |
| vector worker | disabled；not running；write=false |
| Zotero readiness | available、schema ready、integrity、read availability 全部通过；write=false |

Runtime 运行期间和退出后均未出现 WAL/SHM。

## 6. 真实只读功能验证

### 6.1 统一搜索和高质量搜索

- 使用通用、非私人技术词执行 keyword/precision 搜索，返回 3 个 pdf_chunk；
- FTS 状态为 ready；
- 每个结果的 document_id 合法且 fragment ID 唯一；
- fragment fetch 的 document_id、chunk_id 均为正整数；
- 没有返回已删除旧 API 格式；
- 在离线模型模式下执行真实 notebook-search，返回 3 个 PDF chunk；
- embedding 为 Qwen3-Embedding-0.6B，reranker 为 Qwen3-Reranker-0.6B；
- 高质量结果的 document_id、chunk_id 全部合法；
- db_write_performed=false、vector_write_performed=false、llm_called=false。

### 6.2 PDF Preview、Workspace 与 Evidence Basket

Desktop production renderer probe 使用真实 Electron、正式 Vite build、PDF.js、canvas、text/highlight overlay 和动态 fixture PDF，不使用 production DB 或 production PDF。

- PDF Preview 的 canvas、页码、exact highlight、bbox、缩放、定位和独立滚动通过；
- 请求的 1440、1600、1920 三档桌面尺寸全部通过；1920 档受当前显示器可用工作区限制，Electron 实际窗口为 1707×1019，测试仍完整保留布局、canvas 与高光断言；
- /retrieval → /workspace → /retrieval round-trip 通过；
- 统一 searchSession 恢复 query、模式、筛选、上下文、结果、Preview 和滚动状态；
- Evidence Basket 的加入、展示、12 项状态和独立滚动通过；
- 以上测试均为内存/fixture 状态，没有向 production DB 写测试证据。

### 6.3 Zotero 与 MCP

- Zotero 仅调用 inspiration notes 的只读 sync-status；没有 refresh、sync、upsert 或测试 note；
- MCP tools/list 精确列出 search、fetch、export_evidence；
- 本地 search → fetch → export_evidence 实际调用成功，返回 3 个 Zotero inspiration note 结果；
- evidence export 仅在内存中生成，没有写文件；
- 全过程不需要公网 Tunnel。

## 7. 标准端口验证

隔离阶段退出且数据 hash 通过后，使用新的状态目录短暂启动源码 Runtime：

- FastAPI 8000；
- MCP 8787；
- supervisor PID 44028；
- FastAPI PID 29972；
- MCP PID 46448。

验证结果：

- 端口所有者、父子关系、命令行和 cwd 全部来自 canonical 根；
- /health 返回 Search/ok；
- /healthz 返回 ok；
- launcher status 中 FastAPI/MCP ready，Tunnel 未配置但不阻塞；
- FTS path 和 manifest path 均位于 canonical data；
- 一次只读 PDF keyword 搜索成功；
- 写标志为 false；
- 旧根路径命中为 0。

随后用正式 launcher stop 受控退出，约 4.8 秒完成。8000、8787、18080、18787 全部释放，三个 PID 均退出，无 orphan process。

## 8. 数据漂移与完整性

| 检查点 | 文件 | 目录 | 字节 | 完整树 SHA256 | WAL/SHM |
| --- | ---: | ---: | ---: | --- | ---: |
| 启动前 | 191 | 92 | 670,300,309 | 7de213d494bb5387b21e037248a2da4fcf3c51dbaf148cfcc28acb5240e37c64 | 0 |
| 隔离阶段退出后 | 191 | 92 | 670,300,309 | 相同 | 0 |
| 标准阶段退出后 | 191 | 92 | 670,300,309 | 相同 | 0 |
| 回归与最终校验后 | 191 | 92 | 670,300,309 | 相同 | 0 |

其他稳定证明：

- research_memory.db SHA256：6452bebc924f63f500fb9edd149aafd680f4dc4d5f1a97d86e323b0614d69c09；
- PDF tree SHA256：df3de042b41c252dbc8be87bac2da32ff54ac7a4ab32b603108229e5b4f4a24f；
- vector manifest tree SHA256：76b5d4feffd91fab41c0e9407396922e9356b475583b0ba759c29418a50a087a；
- 41/41 个非空 SQLite 数据库 integrity_check=ok；
- 41/41 个数据库 foreign_key_check 为空；
- 所有数据库前后 hash 不变；
- FTS、LanceDB、legacy vector、Zotero note vector 和 5 份 JSON manifest 均通过只读一致性校验；
- vector source ID 无重复，document/chunk 映射合法；
- 没有 migration、checkpoint、VACUUM、FTS/vector rebuild、embedding sync 或 manifest 更新。

## 9. 回归、构建与 packaged source 契约

| 项目 | 结果 |
| --- | --- |
| tracked Python 内存 compile | 395/395 |
| app.main import/route enumeration | PASS |
| tests/core | 185 passed、4 skipped、0 failed |
| Frontend | 25/25 |
| Desktop | 53/53 |
| MCP | 23/23 |
| Vite production build | PASS；132 modules |
| MCP widget build | PASS |
| MCP server build | PASS |
| packaged source resource contract | ready；31/31 |

4 个 core skip 来自测试隔离 data 中不存在 production DB 的预期条件；正式数据库完整性已由独立 immutable read-only validator 覆盖。

Electron 自动化使用隐藏窗口并禁用 crash reporting；没有用户可见测试窗口或控制台黑框。未启动 packaged Search.exe。

## 10. 旧路径和环境边界

- formal Python/JavaScript/PowerShell/配置源码中的旧绝对路径命中：0；
- 新 frontend/MCP build bundle 中的旧绝对路径命中：0；
- tracked 全仓存在 70 条旧路径文本，全部位于目录盘点、保护和数据迁移历史审计文档，不属于运行时、配置、测试入口或 build 输入；
- Runtime 进程命令行/cwd 中旧路径命中：0；
- 退出后 canonical Runtime 进程为 0；
- cloudflared 进程未由本任务启动、停止或修改；
- 旧主仓库最终仍为 57 modified、1,253 untracked、0 staged。

## 11. 结论与下一阶段

结论：

- PASS_CANONICAL_SEARCH_RUNTIME_STARTED_FROM_NEW_ROOT
- PASS_CANONICAL_SEARCH_PRODUCTION_DATA_READABLE
- PASS_CANONICAL_SEARCH_RUNTIME_NO_DATA_DRIFT
- PASS_CANONICAL_SEARCH_RUNTIME_GRACEFUL_SHUTDOWN
- READY_FOR_CANONICAL_SEARCH_PACKAGE_BUILD

下一阶段可以从 canonical source 构建独立候选，但仍应满足：

1. 不覆盖或切换当前正式 Search.exe；
2. 候选的 code/resources 必须全部由本分支正式构建脚本生成；
3. production data 继续作为 sidecar/配置数据，不打入 resources/app；
4. 将外置模型缓存作为明确配置前置条件，不复制进源码或默认安装包；
5. 候选完成 packaged smoke、单一搜索页、PDF Preview、Workspace、Evidence Basket、FastAPI/MCP、无黑框、无旧路径和零数据漂移后，再请求正式切换授权。
