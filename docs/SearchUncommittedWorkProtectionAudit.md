# Search 未提交成果保护审计

## 1. 审计范围与结论

本次审计仅保护旧活动仓库 `D:\LEARNING\Tools\notebook_ai` 中尚未提交的成果，不创建 canonical Search 根目录、不停止现有运行时，也不复制或写入生产数据。

保护快照的旧仓库状态为：

- HEAD：`e21aa0f9c5af7fabe9bf88fae16b0a1bc1748e57`
- 分支：`codex/search-ui-scroll-0.1.2`
- staged：0
- modified：57
- untracked：1,253
- stash：0

所有安全材料位于独立、非活动源码目录：

```text
D:\LEARNING\Archives\SearchMigrationSafety_20260718
```

该目录没有放入未来 canonical 根目录，也没有复制 `data`、PDF、数据库或向量库。

## 2. 可恢复材料

| 材料 | 位置 | 验证 |
| --- | --- | --- |
| 全 refs Git bundle | `git/search-all-refs-20260718.bundle` | `git bundle verify` 通过；complete history；35 refs |
| tracked binary patch | `tracked-patches/notebook_ai-tracked-working-tree-e21aa0f.patch` | 非空；57 个路径；`git apply --numstat --binary` 可解析；反向 apply check 通过 |
| untracked 手写成果 | `untracked-source/` | 38 个文件逐文件非覆盖复制；源/归档 SHA256 全部一致 |
| tracked 分类矩阵 | `manifests/tracked-modified-files.csv` | 57/57 文件均记录状态、大小、SHA256、差异摘要和语义分类 |
| untracked 总清单 | `manifests/untracked-files.csv` | 1,253/1,253 文件均记录分类、大小、复制/排除决定和原因 |
| 生产数据保护清单 | `manifests/protected-production-data.csv` | 32 个逻辑数据集或关键文件；只读哈希，不复制数据 |

Bundle SHA256 为 `2589d882126c06c272ee30f2c798fcaa141fee7545c4383923b559302e710cdd`。Tracked patch SHA256 为 `cc7eae08167f20499322ca5b27416a872cb956c75f08760fff84e2c3f57be896`。生产数据本身的完整哈希只保留在外部安全清单，不进入 Git 文档。

## 3. Git 历史保护结果

- 14 个本地分支已进入 bundle。
- 1 个本地分支没有同名 upstream，但其提交已存在于其他远端引用；不存在仅本机可达的提交。
- `git log --all --not --remotes` 返回 0 个 local-only commit。
- stash 为 0。
- 工作树拓扑、HEAD、upstream、remote-tracking refs、tags 和辅助 refs 均已单独记录。
- 以下关键成果同时由远端引用与 bundle 保护：
  - Search 0.1.4 convergence：`a562d7c267da70f7864f77ad45075747788b75a0`
  - Search 健康品牌修复以及 Tunnel、PDF Probe、旧 database search 收敛历史
  - 按书删除 design-only：`070f7c571b5d47b4ebf2b874151d1f74104718e9`
  - 文件夹收敛审计：`48f9d194b3fdfb3d54a1f1dfe32343a3599975b8`

## 4. 57 个 tracked 修改

### 4.1 分类统计

| 分类 | 文件数 | 处置 |
| --- | ---: | --- |
| A：稳定分支等价覆盖 | 25 | 不迁移；直接采用 stable convergence 版本 |
| B：独有且仍有价值 | 9 | patch 已保护；后续拆成小提交，选择性恢复并重新测试 |
| C：废弃、实验或旧正式链路 | 23 | 不迁入 canonical；patch 和旧仓库暂时保留 |

另有 7 个 C 类文件与已推送 R6 字节一致，因此可从远端恢复，但它们已被当前 convergence 的正式实现替代，不能据此回退 canonical 源码。

### 4.2 A：稳定分支已等价覆盖

以下 25 个 working-tree 文件与三个 stable-derived 推送分支中的内容字节一致：

```text
app/api/retrieval_api.py
frontend/src/features/library/components/NoteCorrectionReviewWorkbench.jsx
frontend/src/utils/noteFirstWorkflow.js
integrations/notebook_ai_chatgpt_app/package-lock.json
integrations/notebook_ai_chatgpt_app/scripts/build-widget.mjs
integrations/notebook_ai_chatgpt_app/server/app.ts
integrations/notebook_ai_chatgpt_app/server/contracts.ts
integrations/notebook_ai_chatgpt_app/server/logging.ts
integrations/notebook_ai_chatgpt_app/server/tools/exportEvidence.ts
integrations/notebook_ai_chatgpt_app/server/tools/fetch.ts
integrations/notebook_ai_chatgpt_app/server/tools/search.ts
integrations/notebook_ai_chatgpt_app/server/tools/shared.ts
integrations/notebook_ai_chatgpt_app/web/src/App.tsx
integrations/notebook_ai_chatgpt_app/web/src/components/EvidenceBasket.tsx
integrations/notebook_ai_chatgpt_app/web/src/components/ResultCard.test.tsx
integrations/notebook_ai_chatgpt_app/web/src/components/ResultCard.tsx
integrations/notebook_ai_chatgpt_app/web/src/components/SourceBadge.tsx
integrations/notebook_ai_chatgpt_app/web/src/main.tsx
integrations/notebook_ai_chatgpt_app/web/src/state/evidenceSelection.test.ts
integrations/notebook_ai_chatgpt_app/web/src/state/evidenceSelection.ts
integrations/notebook_ai_chatgpt_app/web/src/state/mcpBridge.contract.test.ts
integrations/notebook_ai_chatgpt_app/web/src/state/mcpBridge.ts
integrations/notebook_ai_chatgpt_app/web/src/state/toolData.ts
integrations/notebook_ai_chatgpt_app/web/src/styles.css
integrations/notebook_ai_chatgpt_app/web/src/types.ts
```

### 4.3 B：需要后续选择性恢复

```text
docs/ARCHITECTURE.md
docs/RUNNING.md
scripts/zotero/package_zotero_inspiration_plugin.py
tests/core/test_zotero_plugin_contract.py
zotero-plugin/bootstrap.js
zotero-plugin/manifest.json
zotero-plugin/README.md
zotero-plugin/src/syncClient.js
zotero-plugin/src/zoteroReaderBridge.js
```

其中两份文档包含可泛化的 locator/design-system 边界；其余文件包含 Zotero Search Locator 的一部分实现和测试。它们不能整体覆盖 stable 版本：同一差异中还混有旧产品名、占位 update URL、旧 runtime client 或 remote/hybrid 实验逻辑。后续只能按功能拆分并重新建立契约测试。

### 4.4 C：不迁入 canonical

C 类主要包括：重复搜索入口与旧 Workspace 第二套搜索、旧 chapter-review UI、带本机模型路径的 Import Preview、旧路由契约、旧 README、生成的 Node lock 变化，以及只与旧脚本哈希耦合的测试。完整 23 文件清单和每个文件的差异摘要位于外部 tracked 分类矩阵。

## 5. 1,253 个 untracked 文件

### 5.1 路径与来源分类

| 分类 | 文件数 | 大小（B） | 处理 |
| --- | ---: | ---: | --- |
| dist candidate / XPI build output | 1,214 | 949,815,299 | 不复制；逐项登记 |
| 第三方 cloudflared 可执行文件 | 1 | 54,159,760 | 不复制；逐项登记 |
| 手写源码、测试、文档、schema、脚本 | 38 | 364,782 | 非覆盖复制并逐文件哈希验证 |

全部 1,253 个路径均有明确分类；不存在 `unknown`。

### 5.2 38 个已复制文件的语义判定

| 语义分类 | 文件数 | 说明 |
| --- | ---: | --- |
| A：stable 精确或扩展等价 | 6 | stable 已提供正式实现，不需要恢复旧副本 |
| B：独有且仍有价值 | 7 | 已复制；需要后续小批次恢复审查 |
| C：旧 runtime、生产批次或实验代码 | 25 | 已复制仅为防丢失，不迁入 canonical |

B 类候选为：

```text
tests/core/test_fragment_locator_api.py
tests/core/test_search_design_system_contract.py
tests/core/test_vector_store_source_drift.py
tests/node/test_zotero_plugin_contract.mjs
tests/node/zotero_locator_highlight.test.mjs
zotero-plugin/src/locatorHighlight.js
zotero-plugin/src/searchLocator.js
```

`test_fragment_locator_api.py` 只能恢复使用临时 fixture 的通用 locator 覆盖；其中引用具体生产 fragment、Zotero key 或生产数据库的 characterization 测试不得迁入 canonical。Zotero Node 契约同样需要剥离旧 runtime 控制断言后再恢复。

25 个已复制 C 类文件主要是被现有 convergence 替代的 managed Tunnel/autostart runtime、旧 `NOTEBOOK_AI` AppData/任务计划路径、旧本机 Python 路径、Phase 批次 schema 和相关测试。1,215 个未复制项仍保留在旧仓库原位置；它们只是构建输出或第三方二进制，不能作为源码迁移输入。

## 6. 生产数据保护清单

本阶段未复制 `D:\LEARNING\Tools\notebook_ai\data`，也未通过 SQLite 连接打开数据库。外部清单按只读字节流记录了：

- Search SQLite 主库与历史备份；
- 原始 PDF、converted Markdown、notes；
- FTS 数据库与 manifest；
- legacy vector index；
- LanceDB、vector manifest、Zotero note vectors 与备份；
- Zotero snapshot 与历史 snapshot；
- exports、reports、seeds、layout 和空临时目录。

清单共 32 行。当前 `research_memory.db` 使用共享只读文件句柄连续计算两次哈希，两次结果一致；这只是在线保护检查点，正式迁移仍必须在 Search 优雅停止后重新计算两次。

主数据与 R6 cold-data 的核心 DB、notes、FTS、vector store 和当前 Zotero snapshot 五组对应项均为字节一致副本。Cold-data 是主数据的子集，不能成为迁移源，也不能与主数据合并。

## 7. 原工作树不变性

保护过程没有执行 add、commit、checkout、stash、clean、reset、GC、prune、移动或删除。所有输出均写入独立安全目录，审计文档写入单独的 audit worktree。最终状态校验必须继续满足：

- 旧仓库 HEAD 与分支未变；
- staged 仍为 0；
- modified 仍为 57；
- untracked 仍为 1,253；
- 状态清单与保护前快照逐字节一致。

## 8. 结论

未提交 tracked 修改已由 binary patch 保护，全部有价值的 untracked 源码候选已由独立副本保护，Git refs 已由验证通过的 bundle 保护，生产数据已有不复制的只读清单。后续不得整体应用 patch 或整体复制 `untracked-source`；只能以 stable convergence 为基线选择性恢复 B 类成果。

完成状态：`PASS_SEARCH_UNCOMMITTED_WORK_PROTECTED`
