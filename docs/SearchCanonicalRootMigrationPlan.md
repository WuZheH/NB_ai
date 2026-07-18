# Search canonical 根目录迁移计划

## 1. 目标状态

唯一正式活动项目根目录：

```text
D:\LEARNING\Tools\search
```

产品显示名称：`Search`。

目标根用于开发、运行、测试、构建、打包、发布和管理正式数据引用。它必须是独立 Git 工作副本，不能依赖 `notebook_ai_worktrees`、`notebook_ai_clean_clones`、candidate 或旧主仓库的 `.git` common directory。

目标边界为：

```text
D:\LEARNING\Tools\search\                    canonical source/Git root
├── .git\                                     independent Git directory
├── app\, frontend\, integrations\, ...      tracked source
├── data\                                     ignored production data
└── .codex_tmp\                               ignored D 盘测试/构建临时区

%LOCALAPPDATA%\Search\                        用户 runtime/log（项目根之外）
%APPDATA%\Search\                             用户 config（项目根之外）
```

本计划只描述迁移，不执行任何目录建立、clone、复制、移动、删除、进程停止或正式包切换。

## 2. canonical 来源

### 2.1 Git 来源

canonical 代码基线只能来自：

```text
origin/codex/search-0.1.4-github-release-convergence
a562d7c267da70f7864f77ad45075747788b75a0
```

理由：该分支已包含 R6 自包含运行时、单一 Search session、PDF Probe 语义 ready、旧 database search 删除、Tunnel 收敛和 Search health 品牌修复；三个 clean clone 均只是它的旧祖先。

按书删除 design-only 提交：

```text
origin/codex/search-book-delete
070f7c571b5d47b4ebf2b874151d1f74104718e9
```

该提交在目录迁移完成后通过 Git cherry-pick/merge 迁入新的 canonical 工作历史。本阶段不实现删除 API、数据库删除、FTS 删除或向量删除。

本审计提交完成并推送后也应由远端 `codex/search-folder-consolidation-audit` 保留。迁移执行分支应从该远端审计分支创建，不能从当前 linked worktree 复制 `.git`。

### 2.2 数据来源

canonical 数据唯一来源是：

```text
D:\LEARNING\Tools\notebook_ai\data
```

不得从旧 R6 `data` junction、packaged `resources/app`、candidate、clean clone 或测试 user-data 取数据。精确数据集和 SHA256 见 `SearchProtectedDataMigrationMatrix.md`。

## 3. 迁移前硬阻塞

以下条件全部解决前不得建立正式切换：

1. `D:\LEARNING\Tools\notebook_ai` 有 57 个 modified 和 1,253 个 untracked；其中 16 个源码/文档文件在 stable 中不存在，17 个 untracked 同路径内容不同，32 个 modified tracked 内容不同。
2. 旧根正在运行 supervisor、FastAPI 8000 和 MCP 8787。
3. `research_memory.db` 正被进程占用；当前 hash 是在线共享读结果。
4. 当前正式 unpacked local config 仍把数据项目指向旧根。
5. C 盘 AppData 按本任务边界未扫描，尚未确认是否存在需要迁移的旧 `NOTEBOOK_AI` runtime config。
6. 用户尚未授权实际 clone、数据复制、停止进程、修改 local config 或切换正式包。

任一阻塞未解除都不能通过“复制整个目录”绕过。

## 4. 旧路径与技术标识审计

### 4.1 tracked 绝对路径

在 `a562d7c` 的全部 tracked 文本文件中：

| 模式 | 匹配行 | 匹配文件 | 结论 |
| --- | ---: | ---: | --- |
| `D:\LEARNING\Tools\notebook_ai` | 0 | 0 | canonical 源码没有该硬编码绝对路径 |
| `D:/LEARNING/Tools/notebook_ai` | 0 | 0 | 同上 |
| `notebook_ai_worktrees` | 0 | 0 | 源码不依赖 worktree 容器 |
| `notebook_ai_clean_clones` | 0 | 0 | 源码不依赖 clean clone 容器 |
| `NOTEBOOK_AI`（不区分大小写） | 280 | 75 | 内部技术标识、兼容配置、测试和少量过时用户文案 |
| `notebook-ai` | 43 | 26 | npm/MCP service/package 名、source type、测试和 manifest |

因此无需、也禁止对 tracked 源码执行全局字符串替换。真正需要修改的是本地配置和用户可见文案，而不是内部 Python module/package/schema 名。

### 4.2 分类

| 分类 | 对象 | 迁移动作 |
| --- | --- | --- |
| 1. 必须切换到新根 | 当前活动 launcher/MCP 命令行；24 份旧 candidate/package `search-desktop.local.json` 中的 `projectRoot`/`dataProjectRoot`；未来正式构建的 local config | 不修改旧 candidate；在新根重新生成正式 local config，正式切换时确认活动进程路径只含 `D:\LEARNING\Tools\search` |
| 2. 内部技术标识，可保留 | `notebook_ai.runtime.v1` schema、`notebook-ai-mcp` service、`integrations/notebook_ai_chatgpt_app`、npm package、`notebook_ai_launcher.py` 文件名、内部 source id | 不因目录改名而重命名；避免扩大成大规模 API/package 迁移 |
| 3. 历史文档 | 本次没有 tracked 旧 D 盘绝对路径或 worktree/clone 路径命中 | 不做无依据修改；审计文档保留精确路径用于迁移证据 |
| 4. 测试 fixture | legacy AppData `NOTEBOOK_AI`、负向品牌契约、旧环境变量兼容、packaging fixture | 保留能证明向后兼容和“正式品牌为 Search”的测试；不得删测试掩盖旧路径 |
| 5. 用户配置，需要策略 | `NOTEBOOK_AI_*` 环境变量兼容、`%LOCALAPPDATA%\NOTEBOOK_AI\config\runtime.json` fallback、D 盘 package local config | canonical 写入只使用 `SEARCH_*` 与 `%APPDATA%\Search`；旧字段只读兼容，禁止改写/删除旧用户配置 |

### 4.3 需要后续修正的用户可见文本

以下不是绝对路径依赖，但仍显示旧品牌，应在迁移实施的独立、小型代码提交中审查：

- `docs/ARCHITECTURE.md` 的产品开头；
- `docs/RUNNING.md` 标题和环境说明；
- `scripts/runtime/check_notebook_ai_dev_status.py` 的控制台文案；
- `config/environment.example.txt` 只列旧 `NOTEBOOK_AI_*` 变量，而根 `.env.example` 已使用 canonical `SEARCH_*`。

内部文件名、module path、schema version 和 MCP service identity 不属于用户显示品牌，默认保留。所有修改必须逐条带测试，不能全局替换。

## 5. 阶段 A：冻结与快照

### A1. Git 冻结

1. 再次确认以下 origin refs 可访问并记录 SHA：stable、book-delete、folder-consolidation-audit、R6 和 PDF Preview 历史分支。
2. 确认 `070f7c…` 和本审计提交均已推送。
3. 对主仓库 57 modified 和 38 个源码型 untracked 逐文件复审：相同、stable 已替代、仍有独有价值。
4. 对有价值内容创建专用保护分支并只提交源码/文档；不得提交 dist、`.codex_tmp`、node_modules、数据库、PDF 或模型。
5. 推送保护分支后，再用 `git rev-list --all --not --remotes=origin` 证明没有 local-only commit。
6. 主仓库无需为了迁移被强制 clean；但所有有价值差异必须有远端 commit 或经用户确认的 D 盘只读归档清单。

不得使用 stash 作为唯一保护，因为 stash 仍依赖旧 common Git directory。不得 reset、clean、prune、gc 或 force push。

### A2. 运行与数据冻结

1. 另行取得用户授权后，优雅停止旧根 Search supervisor；禁止 `taskkill`/`Stop-Process` 强杀。
2. 确认 8000、8787 和所有 launcher 子进程退出；记录 PID 仅在迁移日志中，不写入 tracked 文档。
3. 证明旧根 DB、FTS、LanceDB 和 Zotero snapshot 没有写句柄。
4. 重新计算两次受保护数据 hash；两次结果必须相同。
5. 只读执行 SQLite `integrity_check`/`foreign_key_check`、FTS manifest 和 LanceDB tree/manifest 检查。
6. 保存冻结清单；不 checkpoint、不 vacuum、不重建索引、不写生产 DB。

若不能安全停止或 hash 不稳定，迁移阻塞，恢复旧服务，不继续复制。

## 6. 阶段 B：建立 `D:\LEARNING\Tools\search`

1. 断言目标路径不存在；若存在任何文件，立即阻塞，不覆盖。
2. 在获得 clone/联网授权后，从 origin 直接 clone 到精确目标路径。不得复制旧 `.git`，不得移动 linked worktree。
3. checkout 已推送的 `codex/search-folder-consolidation-audit`，验证其祖先包含 `a562d7c`。
4. 验证：
   - `git rev-parse --show-toplevel` 为 `D:/LEARNING/Tools/search`；
   - `git rev-parse --git-common-dir` 指向新根自己的 `.git`；
   - remote 与 origin 一致；
   - 初始 working tree clean；
   - 没有 junction/symlink 指回旧目录。
5. 不从现有 clean clone 复制 node_modules 或 build；依赖在新根按 lockfile 恢复。

本阶段只建立源码根，不导入生产数据，不启动正式端口。

## 7. 阶段 C：迁移 Git 历史和分支

1. fetch origin 全部分支，不做历史重写。
2. 从已推送 audit 分支创建获批的实施分支，例如 `codex/search-folder-consolidation`。
3. 将 `070f7c571b5d47b4ebf2b874151d1f74104718e9` 作为 design-only Git 提交迁入；验证只新增两份按书删除设计文档，不带实现代码。
4. 根据阶段 A 的人工决定，逐个合入确有价值的旧主仓库保护提交；每批单独测试，不批量覆盖 stable。
5. `git log --all` 必须仍可访问所有关键 SHA；local-only branch `9d46aa2` 可由远端 fix3 祖先关系恢复，无需复制 branch 文件。
6. 推送实施分支。不得在此阶段创建 tag 或 GitHub Release。

## 8. 阶段 D：迁移正式数据

1. 在新根建立空 staging data 目录；目标同名文件存在即失败。
2. 以 `SearchProtectedDataMigrationMatrix.md` 为白名单，按数据集复制：DB、PDF、converted_md、notes、FTS、vector_index、vector_store、Zotero、exports、reports、seeds 和 layout。
3. 不跟随 R6 `data` junction；不从 cold-data 复制。
4. 不复制空历史占位 DB、tmp、uploads、marker_tmp 内容；由应用按需创建空目录。
5. SQLite/FTS 在停止写进程后作为普通冻结文件复制，不合并；LanceDB 和 manifest 作为同一原子数据集复制。
6. 每个目标数据集验证文件数、总大小和 tree SHA256；PDF 再逐文件验证。
7. 完成前保持旧数据只读原状。任何差异都删除“新 staging 的失败副本”也需要另行精确授权；本计划不预授权删除。
8. 验证后把 staging 作为 `D:\LEARNING\Tools\search\data` 使用；不修改或删除旧树。

禁止 `Copy-Item` 全目录覆盖、`robocopy /MIR`、`Move-Item` 覆盖、SQLite 合并和 LanceDB 局部覆盖。

## 9. 阶段 E：更新必要路径

### E1. 源码路径

canonical 代码已经通过 `Path(__file__)`、`import.meta.url` 和 `resourcesPath` 自动定位项目/packaged runtime，tracked 旧 D 盘绝对路径为 0。迁移后只需验证，不应硬编码 `D:\LEARNING\Tools\search` 到通用源码。

### E2. 正式本地配置

1. 从新根构建脚本重新生成 package local config；使用 `dataDir`/`SEARCH_DATA_DIR` 指向新数据根。
2. Python、Node、cloudflared 仍通过 `SEARCH_PYTHON`、`SEARCH_NODE`、`SEARCH_CLOUDFLARED` 或 local config 注入；不修改 PATH。
3. 不复制 24 份旧 candidate local config。
4. 旧 `NOTEBOOK_AI_*` 仅作为只读兼容 alias；canonical 文档和新配置只写 `SEARCH_*`。
5. C 盘 AppData 只有在用户另行授权只读检查后才处理。默认策略是不删除旧 `%LOCALAPPDATA%\NOTEBOOK_AI`，在 `%APPDATA%\Search` 生成新 config。

### E3. 用户可见品牌

以独立提交修正第 4.3 节的过时文本，并运行品牌契约。不得修改内部 Python module、数据库名、schema version、MCP service identity 或历史 Git 分支名。

## 10. 阶段 F：完整验证与数据一致性

### F1. 测试隔离

所有测试临时目录必须在：

```text
D:\LEARNING\Tools\search\.codex_tmp
```

测试设置独立 `SEARCH_DATA_DIR`、TEMP/TMP 和 Electron user-data，禁止指向生产 `data`，禁止写 C 盘测试缓存。Python 使用明确的 `SEARCH_PYTHON`/指定 conda executable，不使用裸 `python`。依赖恢复或联网安装必须按全局规章另行获得用户确认。

### F2. 源码与构建验证

必须依次通过：

1. canonical source `git status` clean（`data` 和 `.codex_tmp` 被 ignore）；
2. tracked Python compile/import；
3. `tests/core` 全绿；
4. Frontend tests；
5. Desktop 53/53；
6. MCP 23/23；
7. Vite production build；
8. MCP build；
9. 独立 candidate packaged smoke；
10. FastAPI/MCP 隔离端口冷启动；
11. PDF Preview、exact highlight、Workspace round-trip、Evidence Basket；
12. 自动化不弹桌面窗口、无黑框、退出无 PID/端口残留。

### F3. 生产数据只读验证

1. 新旧数据 tree hash、文件数和总字节一致；
2. `research_memory.db` 的 `integrity_check=ok`，`foreign_key_check` 无行；
3. FTS DB 与 manifest 一致，查询结果计数/抽样一致；
4. LanceDB tree、manifest、表/版本集合一致；
5. Zotero snapshot hash 与 readiness 一致；
6. PDF 逐文件 SHA256 一致；
7. Search 从新根读取真实数据时不触发重建、同步或写入；
8. 运行前后生产数据 hash 不变（运行态日志和用户 config 不计入生产数据树）。

### F4. 旧路径为零

必须同时证明：

- tracked source 中旧绝对路径、worktree/clean clone 容器名为 0；
- production frontend/MCP/desktop bundle 中旧绝对路径为 0；
- 活动 launcher、FastAPI、MCP 和 Electron 命令行不含旧根；
- package local config 不含旧根；
- 新根不含指回旧根的 junction/symlink；
- 正式数据引用只解析为 `D:\LEARNING\Tools\search\data`。

所有验证通过后仍只生成迁移报告；正式包切换需要用户再次确认。

## 11. 阶段 G：旧目录归档/删除候选

阶段 G 只生成精确路径清单，不自动删除。

### 第一组：低风险候选

- `D:\LEARNING\Tools\notebook_ai_clean_clones\_tmp`；
- `D:\LEARNING\Tools\notebook_ai_clean_clones\search-0.1.4-446fdd4-retry1`；
- 各根中已证明可重建的 node_modules、普通测试 `.codex_tmp` 和 dist-candidates。

### 第二组：需归档决定

- clean clone 767/candidate1；
- clean clone 1263/candidate2；
- R6 worktree 的历史 candidate、packaged smoke 和旧正式二进制。

### 第三组：最后处理的高风险根

- `D:\LEARNING\Tools\notebook_ai_worktrees\unified-local-backend-bootstrap-0.1.3`；
- `D:\LEARNING\Tools\notebook_ai_worktrees\search-0.1.4-github-release-convergence`；
- `D:\LEARNING\Tools\notebook_ai`。

linked worktree 必须先通过 Git 正常注销，不能直接删除目录。主仓库是 common Git directory，必须最后处理，并且只有在所有分支、未提交源码、生产数据、正式运行和构建验证均转移后才可进入人工确认。每个删除批次必须重新列出路径、用途、风险和可恢复性，等待用户精确授权。

## 12. 回滚方案

1. 新根迁移全过程不修改旧源码或旧数据；旧正式包保持冻结。
2. 正式切换前只使用隔离端口和独立 user-data 验证新根。
3. 任一源码、测试、hash、SQLite、FTS、LanceDB、PDF 或 UI 检查失败，停止新根进程并恢复旧根原运行方式；不覆盖旧配置。
4. 在新根验证完成前不删除旧 root、worktree、clone、candidate 或数据。
5. 不通过重建生产索引、修改 DB 或重新导入 PDF来“修复”迁移差异。

## 13. 迁移批准门槛

进入实际迁移前，用户至少需要单独批准：

1. 对主仓库未提交源码的保护策略；
2. 优雅停止旧根 Search 运行链路；
3. 在 D 盘创建并 clone `D:\LEARNING\Tools\search`；
4. 复制冻结的生产数据到新根；
5. 是否只读检查 C 盘 Search/NOTEBOOK_AI AppData 配置；
6. 恢复依赖所需的 `npm ci`/联网行为；
7. 最终正式包和运行根切换。

删除旧目录、tag、GitHub Release、ZIP 上传仍是更晚的独立决策，不包含在迁移批准中。
