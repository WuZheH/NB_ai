# Search 受保护数据迁移矩阵

## 1. 范围与结论

本矩阵只读检查 `D:\LEARNING\Tools` 中与 Search 项目目录相关的数据。没有执行 SQLite 查询写入、FTS 更新、LanceDB 连接、索引重建或文件复制。

完整生产数据唯一主树是：

```text
D:\LEARNING\Tools\notebook_ai\data
```

旧 R6 worktree 的 `data` 是 junction，实际指向：

```text
D:\LEARNING\Tools\notebook_ai_worktrees\unified-local-backend-bootstrap-0.1.3\.codex_tmp\cold-data\data
```

该 cold-data 树是主数据的严格子集：其中核心 DB、FTS、notes、vector_store 和当前 Zotero snapshot 均与主树逐字节相同，没有发现它独有的生产数据。三个 independent clean clone 没有 `data` 根；其中发现的数据库只是 0 字节测试占位。

推荐最终位置是：

```text
D:\LEARNING\Tools\search\data
```

迁移必须从完整主数据树按逻辑数据集复制到新的空 staging 区，验证后再切换 `SEARCH_DATA_DIR`。不能从 cold-data junction、candidate、clean clone 或 packaged runtime 反向恢复生产数据。

## 2. 哈希方法

单文件使用 SHA256。目录使用确定性 tree SHA256：

1. 递归列出普通文件，不跟随 reparse point；
2. 按绝对路径序排序；
3. 对每个文件计算 SHA256；
4. 对 UTF-8 行 `relative/path\0size\0file_sha256\n` 顺序求 SHA256。

因此目录 tree hash 同时约束相对路径、文件长度和文件内容。空目录 tree hash 为 SHA256 空值 `e3b0c442…b855`。

`research_memory.db` 被活动 FastAPI 进程占用，审计使用允许共享读写的只读句柄求哈希。该值可用于识别当前重复副本，但属于在线哈希；正式迁移必须在优雅停止 Search 后重新计算两次并确认稳定。

## 3. 完整主数据树

主数据树共有 670,300,309 B 的逻辑文件内容。以下绝对路径均位于主仓库，均被 `.gitignore` 的 `data/` 规则排除，不会进入 Git。

| 数据集绝对路径 | 类型 | 文件数 | 大小（B） | SHA256/tree SHA256 | 可重建性 | 当前代码引用 | 推荐最终位置 | 风险 |
| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| `D:\LEARNING\Tools\notebook_ai\data\db` | Search SQLite 与历史备份 | 24 | 314,621,952 | `dcf7d2ceef1195210d241eaeb8feef2b560e3b8e2a7f4372a5f6ee9e1b4d0fe8` | 核心 DB 不可重建；备份是历史证据 | `app.core.paths.DEFAULT_DB_PATH` | `D:\LEARNING\Tools\search\data\db` | 极高 |
| `D:\LEARNING\Tools\notebook_ai\data\pdfs` | 原始 PDF | 8 | 47,466,300 | `5674b1ff2e5b23ecac1aaed6b3f0b1fac9fff8dd6fc76f2c42112204a07c07bd` | 不可假定可重建 | `app.core.paths.PDFS_DIR` | `D:\LEARNING\Tools\search\data\pdfs` | 极高 |
| `D:\LEARNING\Tools\notebook_ai\data\converted_md` | PDF 转换 Markdown | 10 | 351,765 | `511f006d782f0d80f2bc26092028ae5f80ff9ac11b0c5f22bdf16f35ac1c15d5` | 理论可由 PDF 重建，但转换版本可能不同 | `CONVERTED_MD_DIR` 与导入流程 | `D:\LEARNING\Tools\search\data\converted_md` | 高 |
| `D:\LEARNING\Tools\notebook_ai\data\notes` | 本地笔记数据 | 5 | 3,610 | `01f3075c28755748c01d51e37f2158a0ca99e3895dc72cc2a5260c90de21e615` | 可能含用户编辑，不可重建 | `NOTES_DIR` 与检索 source adapters | `D:\LEARNING\Tools\search\data\notes` | 高 |
| `D:\LEARNING\Tools\notebook_ai\data\search_index` | SQLite FTS 与 manifest | 2 | 140,916,849 | `a54eb6c5854432721072e091ba337ed2432f745759538315be249ae7b82ae18c` | 可由 DB 重建，但成本高；本次不重建 | `FTS_DB_PATH`、`FTS_MANIFEST_PATH` | `D:\LEARNING\Tools\search\data\search_index` | 高 |
| `D:\LEARNING\Tools\notebook_ai\data\vector_index` | legacy JSON 向量索引 | 2 | 29,718,957 | `4cd3038f2bd843bf983450d34c030aaf06c0c8d4e33dcdc07d45d51bfc644e86` | 可重建但需模型；本次保留 | `VECTOR_INDEX_DIR` | `D:\LEARNING\Tools\search\data\vector_index` | 高 |
| `D:\LEARNING\Tools\notebook_ai\data\vector_store` | LanceDB、manifest、Zotero note vector 与备份 | 62 | 72,303,647 | `b79322618d01cdffaa0b1299f2436d5b19656f1dd923f5aa047c3a47ea10b6b1` | 可重建但成本高且需模型；本次保留 | `VECTOR_STORE_DIR`、`LANCEDB_DIR` | `D:\LEARNING\Tools\search\data\vector_store` | 极高 |
| `D:\LEARNING\Tools\notebook_ai\data\zotero` | Zotero snapshot 与历史备份 | 17 | 58,532,147 | `c2c51bdd8ee95fd229723d68846d7dc2190ec0c45cb8c18ac71204917ef942c0` | snapshot 可重抓，历史备份不可假定可重建 | `ZOTERO_SNAPSHOT_PATH` | `D:\LEARNING\Tools\search\data\zotero` | 极高 |
| `D:\LEARNING\Tools\notebook_ai\data\exports` | 用户导出 | 22 | 4,736,912 | `7a46935478c50288a26abfd067a56cbf6f4ba563115096586d5bdf6b61c0684f` | 不可假定可重建 | 导出服务写入数据根 | `D:\LEARNING\Tools\search\data\exports` | 高 |
| `D:\LEARNING\Tools\notebook_ai\data\reports` | 研究报告/会话输出 | 33 | 1,607,318 | `c64e439fe1b7ea5701fbe8e70f986a4f1ed4b7424c75e12b54c20f1bd24dac03` | 部分不可重建 | 历史报告与 session 消费方 | `D:\LEARNING\Tools\search\data\reports` | 高 |
| `D:\LEARNING\Tools\notebook_ai\data\seed_md` | seed Markdown | 1 | 32,373 | `6d6e19b653535690d0f2e0521532147c2a24a72e6e39cda7f5cccd420817bf04` | 可由来源重建与否未知 | 导入/维护脚本 | `D:\LEARNING\Tools\search\data\seed_md` | 中 |
| `D:\LEARNING\Tools\notebook_ai\data\seeds` | seed JSON | 1 | 7,909 | `c741266157fe19077781bed4beb6a6899199a38dfdff08556926370b066fa7d3` | 可能是人工验收输入 | 维护脚本 | `D:\LEARNING\Tools\search\data\seeds` | 中 |
| `D:\LEARNING\Tools\notebook_ai\data\layout_json` | 页面布局数据 | 2 | 570 | `8b3d6240607361149c02c88baec5a2ec1c0c2444a081b2ec95ffb61ee52b3c5f` | 可重建性未知 | PDF/import pipeline | `D:\LEARNING\Tools\search\data\layout_json` | 中 |
| `D:\LEARNING\Tools\notebook_ai\data\marker_tmp` | Marker 临时目录 | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 可重建 | 临时转换 | 不迁移内容；目标按需创建 | 低 |
| `D:\LEARNING\Tools\notebook_ai\data\sqlite-db` | 空兼容目录 | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 可重建 | 未发现正式数据 | 不复制；按代码需要创建 | 低 |
| `D:\LEARNING\Tools\notebook_ai\data\tmp` | 空临时目录 | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 可重建 | 临时任务 | 不迁移内容 | 低 |
| `D:\LEARNING\Tools\notebook_ai\data\uploads` | 空上传目录 | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 可重建 | import API | 不迁移内容 | 低 |
| `D:\LEARNING\Tools\notebook_ai\data\notebook_ai.db` | 0 字节历史占位 | 1 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 无有效内容 | 未发现 canonical 引用 | 不复制；保留旧根原物 | 低 |
| `D:\LEARNING\Tools\notebook_ai\data\notebook.db` | 0 字节历史占位 | 1 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 无有效内容 | 未发现 canonical 引用 | 不复制；保留旧根原物 | 低 |

## 4. 核心文件与子数据集

| 绝对路径 | 文件/树 | 大小（B） | SHA256 | 说明 |
| --- | --- | ---: | --- | --- |
| `D:\LEARNING\Tools\notebook_ai\data\db\research_memory.db` | 文件 | 18,866,176 | `6452bebc924f63f500fb9edd149aafd680f4dc4d5f1a97d86e323b0614d69c09` | canonical Search DB；在线共享读哈希，冻结后必须复算 |
| `D:\LEARNING\Tools\notebook_ai\data\db\backups` | 22 文件树 | 276,889,600 | `56d33097076742b7b719ab3db616bb60d004affbf44015722c4a444f12e1c945` | 历史 DB 备份，整体保留 |
| `D:\LEARNING\Tools\notebook_ai\data\search_index\retrieval_fts_v1.db` | 文件 | 140,914,688 | `a71cb156b63986c54874a110f3f0900727d1c5714a60a53d18bf8ad47544d8f3` | FTS 数据库 |
| `D:\LEARNING\Tools\notebook_ai\data\search_index\retrieval_fts_v1_manifest.json` | 文件 | 2,161 | `93bdb3890ed839c34079c849ab015b8c677a770798e684f604c29387a3c25163` | FTS manifest |
| `D:\LEARNING\Tools\notebook_ai\data\vector_index\manifest.json` | 文件 | 326 | `6fef49bd321995ef41b1700b893fc546668a23a085f75e70b495c95ec2e39d5f` | legacy vector manifest |
| `D:\LEARNING\Tools\notebook_ai\data\vector_store\lancedb` | 35 文件树 | 66,466,913 | `bfcff83289391b831d8f9f5136988f98f91c05723416aa9df18f1ad3b319eecd` | 当前 LanceDB；必须作为一个冻结树复制 |
| `D:\LEARNING\Tools\notebook_ai\data\vector_store\vector_manifest.json` | 文件 | 443 | `bbaed9277866c092fa103e817af77e2e930435bf0d4fabdd3456aa9645645379` | 当前 LanceDB manifest |
| `D:\LEARNING\Tools\notebook_ai\data\vector_store\zotero_user_notes_v1` | 2 文件树 | 4,007,019 | `953fd4384b5566fd3ff2f9600fd4315a7fd7366ed7f7d48d2da4a235717c1c5c` | Zotero note vector 数据 |
| `D:\LEARNING\Tools\notebook_ai\data\vector_store\backups` | 24 文件树 | 1,829,272 | `97b823aac8288f3971d06f730289710932f8f9d9265286c27e37cd2f8c9daccf` | Lance schema upgrade 前备份 |
| `D:\LEARNING\Tools\notebook_ai\data\zotero\snapshot\zotero.sqlite` | 文件 | 3,825,664 | `19913c07efe50957dfd6100b11c98d11111872c16b4f12c25a5a628e009ea194` | 当前只读 Zotero snapshot |
| `D:\LEARNING\Tools\notebook_ai\data\zotero\snapshot\backups` | 15 文件树 | 54,706,176 | `198b1063abeb876afe5c917d9a3d99c49726c9dd195876842bb6852041b60888` | Zotero snapshot 历史备份 |
| `D:\LEARNING\Tools\notebook_ai\data\pdfs\candidate_intake` | 1 文件树 | 978,059 | `6fcb36f2b3cc8b7d907ca2760d32e8ab9bd78ca4d981627690e8419889045ddf` | 原始 PDF 子集 |
| `D:\LEARNING\Tools\notebook_ai\data\pdfs\papers` | 7 文件树 | 46,488,241 | `355dccd4635e7f405f35cd28a110b9b8c901e551e1725847360a8e5bfa49202d` | 原始 PDF 子集 |

## 5. cold-data 重复矩阵

| cold-data 绝对路径 | 文件数 | 大小（B） | tree SHA256 | 主树对应项 | 结论 |
| --- | ---: | ---: | --- | --- | --- |
| `...\.codex_tmp\cold-data\data\db` | 1 | 18,866,176 | `382083229d1758c0bbef694a8d75f5edb2e6de8683358fa4bbb9c53b79ba3a04` | 主树核心 DB 文件 | 文件 SHA 相同；tree hash 因目录层级定义不同而不同 |
| `...\.codex_tmp\cold-data\data\notes` | 5 | 3,610 | `01f3075c28755748c01d51e37f2158a0ca99e3895dc72cc2a5260c90de21e615` | 主树 `notes` | 完全相同 |
| `...\.codex_tmp\cold-data\data\search_index` | 2 | 140,916,849 | `a54eb6c5854432721072e091ba337ed2432f745759538315be249ae7b82ae18c` | 主树 `search_index` | 完全相同 |
| `...\.codex_tmp\cold-data\data\vector_store` | 62 | 72,303,647 | `b79322618d01cdffaa0b1299f2436d5b19656f1dd923f5aa047c3a47ea10b6b1` | 主树 `vector_store` | 完全相同 |
| `...\.codex_tmp\cold-data\data\zotero` | 1 | 3,825,664 | `acd07e6b4d6aecd96db8a368824e17da0f5e8193484a75cc67ea2e69c58deeb7` | 主树当前 snapshot | snapshot 文件 SHA 相同；cold-data 没有主树的 15 个历史备份 |

cold-data 总计 235,915,946 B，全部可在主数据树找到逐字节相同的对应内容。因此它不应作为迁移源，但必须暂时保留到新根的最终数据 hash 和应用验证完成。

## 6. 重复文件结论

在六个 Git 工作根中共发现 52 个非空 `.db`、`.sqlite` 或 `.pdf` 文件。关键重复组为：

- `research_memory.db`：主树与 cold-data 完全相同；
- `retrieval_fts_v1.db`：主树与 cold-data 完全相同；
- 当前 `zotero.sqlite`：主树、cold-data 和主树的一份历史备份完全相同；
- Zotero 历史 snapshot 另有 7 份、3 份和 2 份的重复组；
- 8 个 PDF 中有 1 对内容重复但路径不同。

重复只用于迁移验证，不能据此删除任一原文件。尤其 PDF 同内容不同路径可能承载不同导入语义，不能自动去重。

## 7. 其他高保护类型

| 类型 | 结果 | 处置 |
| --- | --- | --- |
| 模型权重 | 相关项目根中未发现 `.safetensors`、`.pt`、`.pth`、`.onnx`、`.ckpt` 或 `.model` 文件；`app/models` 是 Python 包而非权重 | 不迁移依赖目录；未来通过 `SEARCH_MODEL_CACHE_DIR` 重新配置外部模型 |
| covers | 未发现生产 covers 数据集 | 无迁移项 |
| 用户配置 | D 盘发现 24 个 candidate/package local config；没有 `.env` | 不复制旧 config；新根重新生成。C 盘 AppData 未扫描 |
| 日志 | 名称扫描发现 286 个 `logs` 目录，均在 `.codex_tmp`、packaged smoke 或测试 user-data 范围 | 可重建验收产物；不迁移为正式数据 |
| build/cache | `.codex_tmp`、node_modules、dist 和 dist-candidates 约占多数目录空间 | 不进入 canonical 源码/数据复制；由 lockfile/build 重建 |
| audit/export | exports 与 reports 位于完整主数据树 | 作为不可假定可重建的数据集整体迁移 |

`D:\LEARNING\Tools\model_cache` 和 `marker_cache` 的目录名不符合本次 Search 目录候选规则，tracked 代码、当前 shell 环境和 D 盘 local config 也没有把它们解析为当前 Search 正式路径，因此没有扫描其内容，也不会在迁移中触碰。

## 8. 一致性与迁移门禁

### 迁移前

1. 取得授权后优雅停止旧根 Search supervisor；确认 FastAPI 8000、MCP 8787 和所有子进程退出。
2. 确认 DB、FTS、LanceDB、Zotero snapshot 没有写句柄。
3. 对核心 DB、全部数据集重新计算两次 SHA256/tree SHA256；两次结果必须相同。
4. 以 SQLite URI `mode=ro` 执行 `PRAGMA integrity_check` 和 `PRAGMA foreign_key_check`，禁止生成或修改 WAL。
5. 记录 LanceDB 文件数、tree hash、manifest hash和版本文件集合；不连接写入。

### 复制规则

1. 目标 `D:\LEARNING\Tools\search\data` 必须不存在或为空；发现同名文件即阻止，不覆盖。
2. 按本矩阵逐数据集复制；不跟随 junction，不复制 node_modules、dist、`.codex_tmp` 或 package local config。
3. SQLite/FTS 作为已冻结普通文件复制；不得合并数据库。
4. LanceDB 与 manifest 作为同一冻结单元复制；不得单独覆盖表目录或 manifest。
5. PDF 保留源文件；复制后逐文件 SHA256 匹配。
6. 目标 tree hash、文件数和总字节必须与冻结源完全一致。

### 切换后

1. 在新根使用 `SEARCH_DATA_DIR=D:\LEARNING\Tools\search\data` 启动隔离端口实例。
2. 再次执行 DB integrity/foreign-key、FTS manifest、LanceDB manifest 与检索一致性检查。
3. 验证 PDF Preview、Workspace、Evidence Basket、Zotero readiness、FastAPI 和 MCP。
4. 旧根数据保持只读原状，直到用户单独批准归档或删除。

任何 hash、文件数、manifest 或 SQLite 检查不一致都必须停止切换；不允许通过重建索引来掩盖迁移差异。
