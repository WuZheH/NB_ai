# Search 项目目录清点

## 1. 审计结论

审计时间：2026-07-18 18:44:25 +08:00。

审计范围严格限定为 `D:\LEARNING\Tools`。本次没有扫描 C 盘，没有移动、复制、重命名或删除目录，也没有打开 SQLite、FTS 或 LanceDB 进行写操作。

推荐的唯一 canonical Git 历史是：

- 代码基线：`origin/codex/search-0.1.4-github-release-convergence`，当前为 `a562d7c267da70f7864f77ad45075747788b75a0`；
- 按书删除设计成果：`origin/codex/search-book-delete`，当前为 `070f7c571b5d47b4ebf2b874151d1f74104718e9`；
- 目标根目录：`D:\LEARNING\Tools\search`，审计时尚不存在。

不能把现有 `D:\LEARNING\Tools\notebook_ai` 直接改名为目标根。它同时承担 Git common directory、活动运行时和完整生产数据根，并含大量未提交源码。目标根必须从已推送 Git 历史建立为新的独立工作副本，再按数据集迁移受保护数据。

## 2. 扫描口径与数量

只读目录遍历共检查 97,908 个目录，无枚举错误。目录名称命中 `notebook_ai`、`notebook-ai`、`NOTEBOOK_AI`、`Notebook AI` 或 `search` 的原始结果为 684 个。

原始命中不能直接等同于项目副本。684 个结果中包含依赖包内的 `search` 模块、源码组件名、测试临时 AppData、构建包内 runtime-project 和 PDF Preview 测试目录。按 Git 边界、reparse point、构建入口和数据根进一步归并后，得到：

| 类别 | 数量 | 说明 |
| --- | ---: | --- |
| 顶层相关容器 | 3 | `notebook_ai`、`notebook_ai_worktrees`、`notebook_ai_clean_clones` |
| Git 工作根 | 6 | 1 个主仓库、2 个 linked worktree、3 个 independent clone |
| 空临时容器 | 1 | `notebook_ai_clean_clones\_tmp` |
| `.codex_tmp` 根 | 8 | 构建、测试和隔离运行态 |
| `dist-candidates` 容器 | 5 | 共 23 个直接候选子目录 |
| `win-unpacked*` 目录 | 28 | 25 个标准目录及 3 个命名变体 |
| junction/symlink | 2 | 均位于旧 R6 linked worktree；未发现 symlink |
| canonical 目标根 | 0 | `D:\LEARNING\Tools\search` 不存在 |

以下其他工具中的名称命中不属于 Search 项目：Anaconda/Jupyter/Sphinx、Git/Vim、VS Code、npm、TeX Live、PriceAI 和 always-on-top 插件中的普通搜索模块。它们没有 Search 仓库或 Search 数据标记，不进入迁移范围。

## 3. 项目根目录清单

大小和文件数使用“不跟随 reparse point”的只读遍历结果。linked worktree 的 `.git` 是指针文件，其 common Git 数据计入主仓库而不计入 linked worktree。

| 绝对路径 | 类型 | 文件数 | 大小 | 根最后修改时间 | Git/链接 | 风险 | 建议处置 |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
| `D:\LEARNING\Tools\notebook_ai` | 主仓库、Git common directory、活动运行根、完整数据根 | 26,853 | 4,414,785,723 B（4.112 GiB） | 2026-07-16 15:16:11 +08:00 | 独立 `.git` | 极高 | 必须保留；先保护未提交源码、停止旧根运行时并迁移数据，最终验证通过前不可删除或改名 |
| `D:\LEARNING\Tools\notebook_ai_worktrees\search-0.1.4-github-release-convergence` | linked worktree；当前审计工作根 | 25,954 | 1,614,105,802 B（1.503 GiB） | 2026-07-17 17:44:14 +08:00 | common dir 指向主仓库 `.git` | 高 | 用于完成并推送本审计；不得直接移动为 canonical 根 |
| `D:\LEARNING\Tools\notebook_ai_worktrees\unified-local-backend-bootstrap-0.1.3` | linked worktree；R6 源码与历史候选 | 23,678 | 6,580,424,228 B（6.128 GiB） | 2026-07-16 21:10:17 +08:00 | common dir 指向主仓库 `.git`；2 个 junction 被跳过 | 高 | 暂时保留；数据 junction 和历史候选完成比对后才可作为归档/删除候选 |
| `D:\LEARNING\Tools\notebook_ai_clean_clones\search-0.1.4-1263b57` | independent clean clone；candidate2 与 clean-clone 验证证据 | 17,987 | 1,837,855,589 B（1.712 GiB） | 2026-07-17 18:50:11 +08:00 | 独立 `.git` | 中 | 按用户要求暂时保留；canonical 验证完成后再决定归档或删除 |
| `D:\LEARNING\Tools\notebook_ai_clean_clones\search-0.1.4-446fdd4-retry1` | independent clean clone；早期源码重试 | 726 | 7,830,330 B（0.007 GiB） | 2026-07-17 18:36:26 +08:00 | 独立 `.git` | 低 | 无唯一提交、数据或构建；最终复审后可列入安全删除候选 |
| `D:\LEARNING\Tools\notebook_ai_clean_clones\search-0.1.4-767dfc8` | independent clean clone；candidate1 与测试证据 | 17,535 | 1,387,319,603 B（1.292 GiB） | 2026-07-17 18:38:10 +08:00 | 独立 `.git` | 中 | canonical 验证完成前保留；之后可归档或删除 |
| `D:\LEARNING\Tools\notebook_ai_clean_clones\_tmp` | 空容器 | 0 | 0 B | 2026-07-17 18:35:55 +08:00 | 非 Git | 低 | 本阶段保留；迁移验收后可作为精确删除候选 |
| `D:\LEARNING\Tools\search` | 目标 canonical 根 | 0 | 0 B | 不存在 | 不存在 | — | 仅在获批迁移阶段从远端 Git 历史建立 |

三个顶层容器的逻辑占用为：主仓库 4.112 GiB、worktree 容器 7.631 GiB、clean clone 容器 3.011 GiB。不能据此直接释放空间，因为其中包含活动数据、Git common directory 和未提交源码。

### 3.1 每个工作根的数据与产物分类

| 工作根 | SQLite | PDF | FTS/vector | Zotero | 模型权重 | 不可重建/独有内容 | 仅构建或测试产物 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 主仓库 | 43 个 `.db/.sqlite`（含 0 字节占位、历史备份和 snapshot） | 8 个，47,466,300 B | 有正式 FTS、legacy vector、LanceDB 与 manifest | 有当前 snapshot 和 15 个备份 | 0 | 完整生产数据；57 modified、38 个源码型 untracked 中有未被 stable 等价覆盖的内容 | 同时有 `.codex_tmp`、3 个 dist candidate 和多份 unpacked |
| 收敛 worktree | 26 个，均为 0 字节测试占位 | 0 | 无正式数据；仅源码/测试 fixture | 无生产 snapshot | 0 | 当前 audit 分支提交（推送后可远端恢复） | `.codex_tmp`、2 个 health-brand candidate、node_modules |
| R6 worktree | 3 个非空 DB，通过 junction 指向 cold-data | 0 | 有与主树相同的 FTS/vector 副本 | 有与主树相同的当前 snapshot | 0 | 没有发现独有生产数据；R6 Git 提交已推送 | 18 个 dist candidate、大量 smoke/test 运行态 |
| clean clone 1263 | 4 个 0 字节测试占位 | 0 | 无生产数据 | 无生产数据 | 0 | 无 unique commit/data；candidate2 是用户要求暂留的验证证据 | candidate2、node_modules、测试输出 |
| clean clone 446 | 0 | 0 | 无 | 无 | 0 | 无 unique commit/data | 几乎只有 clean source |
| clean clone 767 | 6 个 0 字节测试占位 | 0 | 无生产数据 | 无生产数据 | 0 | 无 unique commit/data | candidate1、node_modules、测试输出 |

`app/models` 是 tracked Python package，不是模型权重。相关项目根中没有发现 `.safetensors`、`.pt`、`.pth`、`.onnx`、`.ckpt` 或 `.model` 文件。

## 4. Git 仓库与 worktree 拓扑

```text
D:\LEARNING\Tools\notebook_ai\.git                 common Git directory
├── D:\LEARNING\Tools\notebook_ai                 codex/search-ui-scroll-0.1.2
├── D:\LEARNING\Tools\notebook_ai_worktrees\
│   ├── search-0.1.4-github-release-convergence     codex/search-folder-consolidation-audit
│   └── unified-local-backend-bootstrap-0.1.3       codex/unified-local-backend-bootstrap-0.1.3
└── local refs for all historical Search branches

D:\LEARNING\Tools\notebook_ai_clean_clones\
├── search-0.1.4-1263b57\.git                       independent clone
├── search-0.1.4-446fdd4-retry1\.git                independent clone
└── search-0.1.4-767dfc8\.git                       independent clone
```

没有发现 detached HEAD。六个工作根的 stash 均为空。所有 remote 都是 `https://github.com/WuZheH/NB_ai.git`，没有发现不同 remote 或不相关 Git 历史。

### 4.1 每个 Git 工作根的状态

| 工作根 | Branch | HEAD | Upstream | Ahead/behind（本地 tracking ref） | staged | modified | untracked | clean |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| 主仓库 | `codex/search-ui-scroll-0.1.2` | `e21aa0f9c5af7fabe9bf88fae16b0a1bc1748e57` | 同名 origin | 0/0 | 0 | 57 | 1,253 | 否 |
| 收敛 worktree | `codex/search-folder-consolidation-audit` | `a562d7c267da70f7864f77ad45075747788b75a0` | `origin/codex/search-0.1.4-github-release-convergence` | 0/0 | 0 | 0 | 0 | 是 |
| R6 worktree | `codex/unified-local-backend-bootstrap-0.1.3` | `9c949e56d16c57124786fa52803ed128f53dcb3a` | 同名 origin | 0/0 | 0 | 0 | 0 | 是 |
| clean clone 1263 | `codex/search-0.1.4-github-release-convergence` | `1263b57388ea1bda951108d9522a45f93c03debb` | 本地旧 tracking ref | 0/0 | 0 | 0 | 0 | 是 |
| clean clone 446 | `codex/search-0.1.4-github-release-convergence` | `446fdd47309c1f6917853db58b923aa862ea0b22` | 本地旧 tracking ref | 0/0 | 0 | 0 | 0 | 是 |
| clean clone 767 | `codex/search-0.1.4-github-release-convergence` | `767dfc8d4b99795ce28108f8438a6331b94d698d` | 本地旧 tracking ref | 0/0 | 0 | 0 | 0 | 是 |

三个 clean clone 的本地 tracking ref 没有在审计中 fetch，因此表内显示 0/0；直接与当前远端稳定提交比较，`1263b57`、`767dfc8`、`446fdd4` 分别落后 14、15、16 个提交，且三者都严格是 `a562d7c` 的祖先，不包含独有提交。

### 4.2 远端保护状态

以下关键分支已存在于 origin：

| 分支 | 远端 HEAD | 状态 |
| --- | --- | --- |
| `codex/search-0.1.4-github-release-convergence` | `a562d7c267da70f7864f77ad45075747788b75a0` | canonical 代码基线 |
| `codex/search-book-delete` | `070f7c571b5d47b4ebf2b874151d1f74104718e9` | design-only 成果已保护 |
| `codex/unified-local-backend-bootstrap-0.1.3` | `9c949e56d16c57124786fa52803ed128f53dcb3a` | R6 历史基线 |
| `codex/search-pdf-preview-0.1.3-fix3` | `d993722be04eab1689acf714b9318322a8bf13e5` | 已验收 Preview 历史 |
| 其他 0.1.2/0.1.3 历史分支 | 与本地同名 ref 相同 | 可从 origin 恢复 |

本地 `codex/search-pdf-preview-0.1.3-fix2` 没有同名远端分支，但其提交 `9d46aa2` 是远端 `codex/search-pdf-preview-0.1.3-fix3` 的直接祖先，因此不是远端不可恢复的 unique commit。

在创建本审计提交之前，`git rev-list --all --not --remotes=origin` 为 0：没有只存在本地提交对象而不被任何 origin ref 包含。审计分支在提交、推送完成后也必须再次满足“同名远端分支包含审计提交”。

### 4.3 必须保留的成果链

| 成果 | 提交 | 是否在远端 stable 中 |
| --- | --- | --- |
| R6 自包含运行时 | `9c949e56d16c57124786fa52803ed128f53dcb3a` | 是 |
| GitHub 源码收敛 | `446fdd47309c1f6917853db58b923aa862ea0b22` | 是 |
| clean clone 文档顺序修复 | `767dfc8d4b99795ce28108f8438a6331b94d698d` | 是 |
| packaged smoke renderer 隔离 | `1263b57388ea1bda951108d9522a45f93c03debb` | 是 |
| 旧 database search 删除 | `b551bbdd1297cfe85945062c13e913236f812720` | 是 |
| PDF Probe 语义 ready 修复 | `ed76db6` | 是 |
| Tunnel 控制 facade 删除 | `0be3f340ad0ef0eea87289c63042589b1069eec5` | 是 |
| managed Tunnel subsystem 删除 | `c4403fa7b10240586b90816cc335771ad2b304d2` | 是 |
| FastAPI health 品牌修复 | `a562d7c267da70f7864f77ad45075747788b75a0` | 是 |
| 按书删除 design-only 审计 | `070f7c571b5d47b4ebf2b874151d1f74104718e9` | 否；由已推送 book-delete 分支单独保护 |

`070f7c…` 是 `a562d7c…` 的直接后续设计提交。迁移时应在新的 canonical 工作根通过 Git 提交迁移，而不是从旧目录复制两份 Markdown。

## 5. 主仓库未提交成果

主仓库的 1,253 个 untracked 中大多数是 `dist-candidates`、packaged runtime、`.codex_tmp` 和构建二进制；但不能因此整体清理。筛选 Python、JavaScript/TypeScript、PowerShell、JSON、Markdown、CSS 和 HTML 后，有 38 个 untracked 源码/文档文件：

- 5 个与 `a562d7c` 完全相同，可由 stable 恢复；
- 17 个在 stable 中存在但内容不同；
- 16 个在 stable 中完全不存在。

stable 中不存在的 16 个路径为：

```text
docs/LOCAL_RUNTIME.md
schemas/mechanism_draft_candidate.schema.json
scripts/runtime/install_autostart.ps1
scripts/runtime/status_autostart.ps1
scripts/runtime/status.ps1
scripts/runtime/uninstall_autostart.ps1
tests/core/test_fragment_locator_api.py
tests/core/test_runtime_desktop_tunnel_control.py
tests/core/test_search_design_system_contract.py
tests/core/test_vector_store_source_drift.py
tests/node/test_zotero_plugin_contract.mjs
tests/node/zotero_locator_highlight.test.mjs
tests/node/zotero_runtime_client.test.mjs
zotero-plugin/src/locatorHighlight.js
zotero-plugin/src/runtimeClient.js
zotero-plugin/src/searchLocator.js
```

与 stable 同路径但内容不同的 17 个 untracked 文件为：

```text
app/domains/retrieval/locator_contracts.py
app/runtime/__init__.py
app/runtime/autostart.py
app/runtime/cli.py
app/runtime/config.py
app/runtime/contracts.py
app/runtime/health.py
app/runtime/pid_identity.py
app/runtime/process_manager.py
app/runtime/supervisor.py
app/runtime/tunnel.py
frontend/src/pages/DesktopSettingsPage.jsx
scripts/runtime/notebook_ai_launcher.py
tests/core/test_runtime_autostart_contract.py
tests/core/test_runtime_launcher_contract.py
tests/core/test_runtime_tunnel_contract.py
tests/core/test_search_desktop_frontend_contract.py
```

57 个 modified tracked 文件中，25 个工作内容与 `a562d7c` 相同，32 个不同。不同内容横跨 retrieval、统一 UI、MCP widget、Zotero 插件和旧 runtime。迁移前必须为这些差异生成独立 patch/保护分支并由用户判断，不允许直接覆盖，也不能因为 stable 更晚就自动判定它们无价值。

## 6. 构建、测试和候选产物

| 根 | 文件数 | 大小 | 性质 | 处置 |
| --- | ---: | ---: | --- | --- |
| 主仓库 `.codex_tmp` | 2,619 | 0.449 GiB | 测试/验收运行态 | 可重建；当前不删除 |
| 主仓库 desktop `dist-candidates` | 1,260 | 0.885 GiB | r6/r6f/r6g 候选 | 历史产物；正式复审前保留 |
| 主仓库 desktop `dist` | 754 | 1.167 GiB | 当前正式及历史 unpacked | 正式包冻结；不可切换或删除 |
| 收敛 worktree `.codex_tmp` | 9,507 | 0.167 GiB | 收敛测试证据 | 可重建；审计后仍保留 |
| 收敛 worktree desktop `dist-candidates` | 815 | 0.589 GiB | health-brand smoke 候选 | 可重建；发布冻结期间保留 |
| R6 worktree `.codex_tmp` | 8,298 | 0.454 GiB | R3-R6 隔离运行态 | 可重建；含数据副本 junction target，不能整体删除 |
| R6 worktree desktop `dist-candidates` | 4,243 | 4.675 GiB | 18 个历史候选 | 二进制可重建；完成数据和 Git 验证后才可列入删除清单 |
| R6 worktree desktop `dist` | 113 | 0.289 GiB | 旧正式 unpacked | 历史运行产物 |
| clean clone 1263 `.codex_tmp` + candidate2 | 3,378 | 0.967 GiB | clean clone、candidate2 和测试证据 | 用户明确要求保留 |
| clean clone 767 `.codex_tmp` + candidate1 | 2,926 | 0.548 GiB | 早期 clean 验证证据 | 最终验证后可归档/删除 |

三个 clone/worktree 中的 `frontend/node_modules`、MCP `node_modules` 和 desktop `node_modules` 均可由 lockfile 恢复，但本阶段不删除。源码仓库迁移不能复制这些目录；应在 canonical 根执行 `npm ci` 恢复。

## 7. Reparse point 审计

只发现两个 junction：

| 路径 | 指向 | 影响 |
| --- | --- | --- |
| `D:\LEARNING\Tools\notebook_ai_worktrees\unified-local-backend-bootstrap-0.1.3\data` | 同一 worktree 的 `.codex_tmp\cold-data\data` | 旧 R6 运行数据副本；直接移动 worktree 会使链接失效，递归复制可能双计或覆盖 |
| `D:\LEARNING\Tools\notebook_ai_worktrees\unified-local-backend-bootstrap-0.1.3\integrations\notebook_ai_chatgpt_app\node_modules` | 主仓库 MCP `node_modules` | 依赖复用；不是可移植依赖，canonical 根应重新 `npm ci` |

未发现指向 C 盘的项目 junction/symlink。

## 8. 当前活动引用

审计时存在一条从旧根启动的实际运行链路：

```text
D:\LEARNING\Tools\notebook_ai\scripts\runtime\notebook_ai_launcher.py supervise
├── Python / uvicorn app.main:app --port 8000
└── integrations\notebook_ai_chatgpt_app\dist\server\index.js
```

因此 `D:\LEARNING\Tools\notebook_ai` 仍是活动运行根，不能在迁移时边运行边复制数据库或向量树。当前 shell 未设置 `SEARCH_*` 或 `NOTEBOOK_AI_*` 环境变量；运行时默认数据路径由旧根解析到 `D:\LEARNING\Tools\notebook_ai\data`。

在 D 盘项目树中发现 24 份 `search-desktop.local.json`，位于旧 build/candidate 内。当前正式 `dist\win-unpacked\search-desktop.local.json` 的数据项目字段指向 `D:\LEARNING\Tools\notebook_ai`；其他历史候选分别指向主仓库或旧 worktree。另有 57 份 `runtime.json`，均位于 `.codex_tmp` 测试路径，没有发现 D 盘 `.env`。

这些 local config 不能复制到 canonical 源码，也不能作为产品默认值。未来正式构建必须从新根重新生成 local config；AppData 中的用户配置因“不得扫描 C 盘”未纳入本审计，属于迁移执行前需明确授权的独立检查项。

## 9. 处置分级

### 必须暂时保留

- 主仓库：Git common directory、活动运行时、完整生产数据、未提交源码；
- 当前收敛 worktree：本审计分支在此提交与推送；
- R6 worktree：仍承载旧正式历史、数据 junction 和大量候选；
- clean clone 1263/candidate2：用户已明确要求保留；
- clean clone 767/candidate1：作为早期 clean 验证证据保留到新根复验结束。

### 未来可归档

- R6 大量 candidate 和 `.codex_tmp` 验收证据；
- clean clone 1263 与 767；
- 历史正式 unpacked 目录。

归档应是显式用户决定，不是迁移的前置条件。

### 未来可安全删除的候选

- 空的 `notebook_ai_clean_clones\_tmp`；
- clean clone 446；
- 经 hash/测试证明可重建的 node_modules、`.codex_tmp`、dist-candidates；
- 已从 common Git directory 正常注销、且无活动进程/独有数据的 linked worktree。

上述均只是候选。本审计没有授权删除；必须在 canonical 根完整验证并再次取得精确路径确认后处理。

## 10. 阻塞项

1. 主仓库 57 modified + 1,253 untracked 尚未保护；这是迁移前最高优先级阻塞。
2. 旧根 supervisor、FastAPI 和 MCP 正在运行；冻结阶段需要用户单独授权优雅停止。
3. 生产数据库处于进程占用状态；本审计哈希为共享读取的在线哈希，不能替代停机后的最终快照哈希。
4. C 盘 AppData 按边界未扫描；正式切换前需要用户授权只读检查或明确采用新配置重新生成策略。
5. 正式包和旧 candidate 的 local config 指向旧根；不能复制到新构建。
6. 迁移后仍需把 `070f7c…` 通过 Git 迁入 canonical 历史，而不是复制文档。
