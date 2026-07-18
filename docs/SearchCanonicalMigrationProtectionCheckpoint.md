# Search canonical root 迁移保护检查点

## 1. 检查点身份

本检查点建立在已推送的 `codex/search-folder-consolidation-audit` 上，用于在创建 `D:\LEARNING\Tools\search` 前证明旧主仓库的独有成果可恢复。

| 对象 | 检查点 |
| --- | --- |
| 旧活动仓库 | `D:\LEARNING\Tools\notebook_ai` @ `e21aa0f9c5af7fabe9bf88fae16b0a1bc1748e57` |
| canonical 源码基线 | `origin/codex/search-0.1.4-github-release-convergence` @ `a562d7c267da70f7864f77ad45075747788b75a0` |
| 按书删除设计 | `origin/codex/search-book-delete` @ `070f7c571b5d47b4ebf2b874151d1f74104718e9` |
| 文件夹审计 | `origin/codex/search-folder-consolidation-audit` @ `48f9d194b3fdfb3d54a1f1dfe32343a3599975b8` |
| 外部安全材料 | `D:\LEARNING\Archives\SearchMigrationSafety_20260718` |

本检查点不是发布产物，不是活动源码仓库，也不是生产数据副本。

## 2. 恢复路径

### 2.1 Git 历史

`git/search-all-refs-20260718.bundle` 保存 branches、tags、remote-tracking refs、worktree refs 和辅助 refs。验证记录位于 `reports/git-bundle-verify.txt`，refs 列表位于 `manifests/git-bundle-heads.txt`。

若远端不可用，应先在新的隔离恢复目录验证 bundle，再恢复指定 ref。不得把 bundle 解包到旧 dirty 仓库，也不得用 bundle 回退 stable convergence。

### 2.2 tracked 修改

`tracked-patches/notebook_ai-tracked-working-tree-e21aa0f.patch` 是相对旧仓库 HEAD 的完整 binary patch。它用于灾难恢复和逐文件审阅，不是 canonical 的批量迁移输入。

未来使用前必须：

1. 在隔离恢复分支验证目标基线；
2. 用 `git apply --check --binary` 检查；
3. 只选取分类矩阵中的 B 类语义块；
4. 删除旧产品名、私人路径、占位 URL 和旧 runtime 控制耦合；
5. 重新运行对应测试后单独提交。

### 2.3 untracked 手写成果

38 个候选文件位于 `untracked-source/`，保留旧仓库相对路径。`manifests/untracked-source-copies.csv` 同时记录源 SHA256 与归档 SHA256；所有副本均已验证一致。

恢复时必须依据该清单逐文件选择，不能复制整个目录。A 类直接采用 stable；C 类不恢复；B 类按第 4 节拆分。

### 2.4 生产数据

生产数据没有进入安全源码归档。`manifests/protected-production-data.csv` 和 `manifests/protected-data-duplicate-comparison.csv` 只记录路径、文件数、大小、只读哈希、可重建性和后续迁移方式。

正式数据迁移必须等用户另行授权并优雅停止现有运行时后执行。不能从 cold-data、candidate、packaged resources 或 clean clone 反向恢复。

## 3. 完整性门槛

| 门槛 | 结果 |
| --- | --- |
| Bundle 非空且 `git bundle verify` 通过 | PASS |
| Bundle 可列出关键 refs | PASS |
| Binary patch 非空并解析出 57 个路径 | PASS |
| Patch 对当前 dirty tree 的反向 apply check | PASS |
| 1,253 个 untracked 路径全部分类 | PASS |
| 38 个手写候选全部复制且哈希相同 | PASS |
| 未复制项均有路径、大小与排除理由 | PASS |
| 生产数据 32 行只读保护清单 | PASS |
| 核心 DB 在线连续两次哈希一致 | PASS |
| 未修改旧工作树 | PASS（提交前再次核验） |

## 4. 后续恢复提交建议

只有 B 类内容需要进入后续恢复评审，建议拆成三个小批次：

1. `docs(search): recover generic architecture boundaries`
   - 从旧 `ARCHITECTURE.md`、`RUNNING.md` 中只恢复 locator、路径定位和设计系统的通用说明。
   - 不恢复旧本机路径、旧 Tunnel 或旧产品名。
2. `test(search): recover generic locator and index contracts`
   - 恢复设计系统契约和 vector source-drift 契约。
   - `test_fragment_locator_api.py` 仅迁移临时 fixture 覆盖，删除生产 ID、生产 Zotero key 和生产数据读取。
3. `feat(zotero): integrate Search Locator on canonical plugin runtime`
   - 评审 `searchLocator.js`、`locatorHighlight.js` 及对应 Node 测试。
   - 从 tracked patch 中选择性吸收 bootstrap、reader bridge、manifest、packaging 和文档变化。
   - 不恢复旧 managed runtime client，不使用占位 update URL，不覆盖 canonical Zotero sync 行为。

按书删除目前仍保持 design-only；迁移 canonical 根目录后只能迁入既有设计提交，不得顺带实现删除 API、数据库、FTS 或向量删除。

## 5. 明确不迁入的内容

- dist candidates、win-unpacked、XPI build、自动生成 bundle 和缓存；
- cloudflared 第三方可执行文件；
- 旧 managed Tunnel、pause/resume、configure-tunnel 和 autostart facade；
- 重复搜索入口、Workspace 第二套搜索和旧 chapter-review 正式入口；
- 带固定本机路径、固定生产 key/ID 或旧品牌的实现；
- production batch schema、一次性验收脚本和与旧 Build ID 耦合的测试；
- cold-data 副本以及任何由 candidate/packaged runtime 反向恢复的数据。

## 6. 下一阶段边界

用户批准后，下一阶段才可以：

1. 冻结旧活动路径与运行时状态；
2. 优雅停止 Search、FastAPI 和 MCP；
3. 复算生产数据稳定哈希；
4. 从 pushed stable convergence 建立新的空 canonical 项目根；
5. 按逻辑数据集迁移并逐项验证；
6. 在 canonical 根完成编译、测试、构建、冷启动与数据一致性检查；
7. 最后生成旧目录归档/删除候选，但不自动删除。

本检查点没有授权执行上述动作。

在最终旧工作树不变性复核通过后，状态为：`READY_FOR_CANONICAL_SEARCH_ROOT_CREATION`。
