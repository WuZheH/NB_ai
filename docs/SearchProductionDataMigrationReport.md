# Search 正式生产数据迁移报告

## 1. 报告范围

本报告记录 Search 正式生产数据从旧项目根复制到 canonical 项目根的只读校验与同盘落位结果。这里出现的绝对路径仅用于本次迁移审计，不是运行时配置、源码依赖或安装说明。

- 迁移日期：2026-07-18
- canonical 迁移分支：`codex/search-canonical-root-migration`
- 迁移前源码提交：`8f9482da2e0ea6389d60f46e1767f50da0bbf775`
- 唯一数据源：`D:\LEARNING\Tools\notebook_ai\data`
- 独立 staging：`D:\LEARNING\Tools\search_data_staging_20260718`
- 最终目标：`D:\LEARNING\Tools\search\data`

本阶段未启动旧或新 Search Runtime，未切换正式 `Search.exe`，未修改 Tunnel、ChatGPT App、快捷方式或用户配置。

## 2. 执行前冻结检查

执行前和关键落位点均确认：

- TCP 8000、8787 均未监听；
- 未发现以旧数据、staging 或 canonical data 为工作目录的 Python、Node、Electron、Search 或 SQLite 进程；
- 未启动、停止或修改 cloudflared；
- 源数据根及其子项无 reparse point；
- 源数据中无 SQLite `-wal` 或 `-shm` 文件；
- canonical Git 工作树 clean，分支和 HEAD 与任务基线一致；
- 最终目标最初只有两个 0 文件、0 字节的空目录 `data/` 与 `data/db/`；
- staging 路径最初不存在；
- 旧主仓库保持 57 modified、1,253 untracked、0 staged。

若上述任一条件不满足，脚本会在复制或落位前终止。

## 3. 源数据双轮稳定性检查

对源树连续执行两轮只读清单与 SHA256。两轮的逐文件相对路径、大小、SHA256、最后修改时间、属性、目录集合、空目录集合及逻辑数据集摘要完全一致。

| 指标 | Round 1 | Round 2 | 结果 |
| --- | ---: | ---: | --- |
| 文件数 | 191 | 191 | 一致 |
| 目录数（含根） | 92 | 92 | 一致 |
| 总字节数 | 670,300,309 | 670,300,309 | 一致 |
| content tree SHA256 | `2cfa22f884782ce3910f6884a6db72f51f794a388a704b15f7168a68f8d01bc7` | 相同 | PASS |
| structure tree SHA256 | `3699ed3ec6332569aadf4a0e903b07d881bd792701ecc47e352491139d1dac67` | 相同 | PASS |
| full tree SHA256 | `7de213d494bb5387b21e037248a2da4fcf3c51dbaf148cfcc28acb5240e37c64` | 相同 | PASS |

## 4. staging 复制与逐项验证

复制使用非破坏性命令边界：

```powershell
robocopy <source> <staging> /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ
```

未使用 `/MIR`、`/MOVE`、`/PURGE` 或 `/DELETE`。staging 从不存在的路径创建，robocopy 返回码为 1（成功复制新文件）；返回码没有被单独当作成功证据。

复制后逐文件验证结果：

- 源与 staging 均为 191 个文件、92 个目录、670,300,309 字节；
- 逐文件相对路径、大小、SHA256、mtime 和属性完全相同；
- 0 个缺失文件，0 个额外文件；
- 0 个缺失目录，0 个额外目录；
- 空目录集合完整保留；
- 全树和所有逻辑数据集 tree hash 完全相同；
- 源数据在复制后再次扫描，仍与复制前 Round 2 完全相同。

## 5. 逻辑数据集一致性

下表的 hash 为文件内容树与目录结构树的组合 SHA256。`backups`、`vector_manifests` 等逻辑选择器会与其他数据集重叠，不能相加得到总文件数。

| 逻辑数据集 | 文件 | 目录 | 字节 | combined tree SHA256 | 源/staging/最终目标 |
| --- | ---: | ---: | ---: | --- | --- |
| `data/db` | 24 | 2 | 314,621,952 | `09d34f6c28a25e4f15c3bd32e95b509bffc749fb2394810476b839b10c03c406` | 一致 |
| `research_memory.db` | 1 | 0 | 18,866,176 | `66bfa70ad9d180ccc958a813e2ae4c6c4ca746fa062a76650f24192733427d29` | 一致 |
| FTS | 2 | 1 | 140,916,849 | `19b212a0a5ca47ffa9e5bbe452112b00a75e8abee74366a75461f5714c6f8aa0` | 一致 |
| `vector_store` | 62 | 24 | 72,303,647 | `09bf3acaf3f60393dc529869700d718646ca22f4adff5f8e7c889327be1f0090` | 一致 |
| LanceDB | 35 | 11 | 66,466,913 | `4672868420d53ef25d611e7580dbe38b0c062ca91ec4bec56c13a3a7ee407afb` | 一致 |
| legacy `vector_index` | 2 | 1 | 29,718,957 | `31ac1a52270d428df78200902db8ee53ccb58dc8e36088713119cdcc2d1123f4` | 一致 |
| vector/Lance manifests | 26 | 0 | 229,476 | `76b5d4feffd91fab41c0e9407396922e9356b475583b0ba759c29418a50a087a` | 一致 |
| PDFs | 8 | 4 | 47,466,300 | `df3de042b41c252dbc8be87bac2da32ff54ac7a4ab32b603108229e5b4f4a24f` | 一致 |
| converted markdown | 10 | 7 | 351,765 | `670f6712a5c8c75a699ca565c1e0e76f868385dbbc0b3d479e3d89aa1adacee6` | 一致 |
| Zotero | 17 | 18 | 58,532,147 | `819562c8a2bd26596d88de8037a2123b2566a9bb36b051b381ac5ec707c4df89` | 一致 |
| notes | 5 | 3 | 3,610 | `aab6f8ffb28a1c7759f6db0d3201b246d0addc1b773b936cf61c53cff736af1c` | 一致 |
| exports | 22 | 3 | 4,736,912 | `e0793bb5412ab7deccc0b92ab2bb02179f9b7fbe1cbe6fd49ac739577a603da7` | 一致 |
| covers | 0 | 0 | 0 | `27163ab962d0833a026fc2af5143528164484f2fbec3a7d90e53a82a960a839c` | 源和目标均不存在 |
| backups（跨数据集逻辑选择） | 62 | 28 | 352,291,224 | `a85460e33885187f9296b369e63185377c88b60096c7c1f415e83e9896ac0e0f` | 一致 |

## 6. SQLite 验证

SQLite 连接全部使用 `mode=ro&immutable=1`、`PRAGMA query_only=ON` 和内存临时存储；未执行 migration、写事务、checkpoint、VACUUM 或 schema 修改。

- 发现 43 个 SQLite 路径：41 个非空数据库、2 个 0 字节历史占位；
- 41/41 非空数据库在源、staging 和最终目标上均可只读打开；
- 所有非空数据库 `PRAGMA integrity_check` 均为 `ok`；
- 所有非空数据库 `PRAGMA foreign_key_check` 均返回空；
- 源与复制品的 schema hash、schema 对象数和所有表行数完全一致；
- 每次验证前后数据库 SHA256 均未变化；
- 正式主库核心计数：`documents=10`、`knowledge_chunks=11,380`、`book_chapters=84`；
- 正式 FTS 计数：ordinary、Unicode FTS、trigram FTS 均为 11,803。

验证过程没有读取或输出 PDF 正文、笔记正文或私人字段；详细结果只记录 schema、计数和哈希。

## 7. FTS、向量与 manifest 验证

### 7.1 FTS

使用项目 canonical 只读状态函数，并显式传入源或复制品的 DB、Zotero snapshot、notes、FTS DB 和 manifest：

- 状态：`ready`；
- `integrity_check=ok`；
- 必需表全部存在；
- ordinary、Unicode FTS、trigram FTS 与 manifest 均为 11,803；
- duplicate fragment ID 为 0；
- 主库、Zotero 和 notes source fingerprints 与 manifest 匹配；
- FTS DB SHA256：`a71cb156b63986c54874a110f3f0900727d1c5714a60a53d18bf8ad47544d8f3`；
- FTS manifest SHA256：`93bdb3890ed839c34079c849ab015b8c677a770798e684f604c29387a3c25163`；
- 所有 write flags 为 false。

### 7.2 LanceDB

没有调用会创建目录或访问默认数据源的 `open_vector_store()` / `check_vector_store_status()` facade。校验器绕过公开连接中的 `mkdir`，仅执行表列举、schema、行数和选择性元数据读取，并在前后重算完整数据树哈希。

- 正式表仅有 `passage_embeddings` 与 `object_embeddings`；
- passage vectors：11,373；object vectors：35；
- 两表向量维度均为 1,024；
- vector manifest 的模型、模型路径摘要、维度、profile version 和两表计数与实际表一致；
- source ID 无重复；
- vector 中的 document/chunk ID 均能映射到正式主库，非法映射数为 0；
- 源与最终目标的表 schema、元数据摘要和计数完全一致；
- 读取前后 LanceDB 与全 data tree hash 均未变化。

### 7.3 legacy vector 与 Zotero note vectors

- legacy JSONL：6,114 条，manifest 计数一致，embedding dimension 为 256，源与目标文件 SHA256 相同；
- legacy manifest SHA256：`6fef49bd321995ef41b1700b893fc546668a23a085f75e70b495c95ec2e39d5f`；
- Zotero note vector manifest：161 条、1,024 维；
- note vector index 文件名为安全相对 basename，文件存在，payload SHA256 与 manifest 匹配；
- note vector manifest SHA256：`b6bcb1fbd8d09c15187a9746b9d09b7934579ec9f25b636787ad822ac9423685`；
- 5 份 JSON manifest 均可解析，源与目标原始字节完全相同；
- 未重建、compact、同步或修改任何向量与 manifest。

## 8. 最终同盘落位

只有源稳定、复制完整、SQLite/FTS/vector/manifest 全部通过后才执行落位。落位前再次确认最终目标仍只有两个精确空目录且无文件、无 reparse point、无运行时占用。

执行顺序：

1. 删除空目录 `D:\LEARNING\Tools\search\data\db`；
2. 删除空目录 `D:\LEARNING\Tools\search\data`；
3. 将已验证 staging 在同一 D 盘内重命名为 `D:\LEARNING\Tools\search\data`。

未执行复制覆盖。落位时间为 `2026-07-18T14:55:19.8608553Z`。同盘重命名完成后 staging 路径不存在，最终目标存在。

落位后重新生成逐文件清单并复跑 SQLite、FTS、LanceDB 和 manifest 校验：

- 最终目标：191 个文件、92 个目录、670,300,309 字节；
- 0 个缺失或额外文件，0 个缺失或额外目录；
- 最终 full tree SHA256 仍为 `7de213d494bb5387b21e037248a2da4fcf3c51dbaf148cfcc28acb5240e37c64`；
- 最终目标与旧源逐文件、逐目录、逐逻辑数据集完全一致；
- 最终验证前后 data tree 未变化。

## 9. 旧源保护复核

落位后再次验证旧源和旧仓库：

- 旧源的文件数、目录数、总字节、逐文件 SHA256 和全树 SHA256 与复制前 Round 2 完全一致；
- 主数据库、PDF、FTS、LanceDB、manifest、Zotero、notes、exports 和 backups 均未变化；
- 旧仓库仍为 57 modified、1,253 untracked、0 staged；
- 57 个 modified 与安全归档 post-stop 清单逐文件重哈希：0 差异；
- 1,253 个 untracked 与安全归档 post-stop 清单逐文件重哈希：0 差异；
- tracked aggregate SHA256：`890254ec366daa8cb5fd09aca1cf371541f40cdd5861e3dc28df02551b83303d`；
- untracked aggregate SHA256：`ba83e39af4d20de3b7a7917a6a7e594af8a3700bd979ffe3885974fc2fe88cb7`。

旧数据没有被删除、改名、归档、checkpoint 或写入。

## 10. 异常与处置

没有发生数据、数据库、索引或 manifest 异常。发生过两次校验工具层面的 Windows 长路径兼容错误：

1. 首次 staging 哈希校验在一个超过传统 `MAX_PATH` 的路径上返回 `FileNotFoundError`。校验器改用 `\\?\` 扩展长度路径后，原断言不变并全部通过。
2. 首次 SQLite 校验将 `\\?\` 前缀直接放入 SQLite URI，返回 `invalid uri authority`。该失败发生在 staging 数据库打开前；随后遍历继续使用扩展路径，SQLite URI 使用项目正式的普通 `file:D:/...?...` 形式，原只读断言不变并全部通过。

两次错误后均先确认源、staging 哈希未变化；没有通过降低断言、重建数据或修改生产文件绕过问题。

## 11. 审计材料边界

详细逐文件清单、复制日志、SQLite 表计数、索引元数据摘要、前后哈希和落位记录仅保存在：

```text
.codex_tmp/production-data-migration/20260718-production-data/
```

该目录被 Git 忽略。生产 `data/` 由 `.gitignore` 覆盖，没有进入 Git staging 或提交。本提交只包含本报告。

## 12. Runtime 启动验证前置条件

生产数据已经可以进入 canonical Runtime 启动验证，但启动仍保持冻结。下一阶段开始前必须：

1. 获得独立的 Runtime 启动验证授权；
2. 再次确认 8000/8787 空闲且旧 Runtime 不会自动恢复；
3. 确认 Runtime 解析到 canonical `D:\LEARNING\Tools\search\data`，正式源码不再依赖旧根；
4. 保留旧源和安全归档，直到 canonical Runtime、PDF Preview、Workspace、Evidence Basket、FTS、LanceDB 和 Zotero backend 全部通过；
5. 启动验证期间禁止 migration、FTS/vector rebuild、manifest 更新和自动同步写入；
6. 验证退出后确认端口与 PID 无残留，并再次比较数据库、manifest 和数据树哈希；
7. 在上述验证完成前不切换正式包或快捷方式。

## 13. 结论

- `PASS_SEARCH_PRODUCTION_DATA_COPIED`
- `PASS_SEARCH_PRODUCTION_DATA_HASH_VERIFIED`
- `PASS_SEARCH_DATABASE_AND_INDEX_INTEGRITY_VERIFIED`
- `READY_FOR_CANONICAL_RUNTIME_STARTUP_VALIDATION`
