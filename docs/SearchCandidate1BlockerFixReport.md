# Search Candidate1 阻塞修复报告

## 结论

本批修复了 candidate1 暴露的两个发布阻塞契约：PDF Preview 在 Workspace 返回后的语义 ready 无法收敛，以及 package tree hash 受文化排序影响而跨进程不稳定。

candidate1 及其独立 smoke 副本继续原样保留，失败结论不变。candidate1 的包内容、自包含边界和生产数据无漂移曾通过，但完整 smoke 因当时的 PDF ready 契约失败，因此仍不得作为正式包使用。

## PDF semantic-ready 根因

PDF 页面、页码、高光和滚动位置已经完成真实恢复，但旧状态机存在两个相同的阈值缺陷：

1. 自动适配宽度计算出的目标比例与当前比例差值不超过 `0.05` 时，不会实际更新 zoom，却把未应用的目标比例写入 ready 等待状态。
2. 高光自动聚焦遇到同一阈值时，也会等待未实际应用的目标比例。

因此 canvas 和高光层可以正确显示，`data-preview-ready` 却永久为 `false`。这不是 PDF.js 加载失败，也不是 DOM probe 读取过早。

## 统一 Preview 恢复状态机

现在普通打开 PDF、结果切换、Evidence Basket 跳转和 Workspace 返回共用同一套 Preview 状态机：

- PDF document 已加载；
- 请求页已经渲染，或越界页码已明确降级到最后一页；
- viewport 与非零 canvas/backing dimensions 已提交；
- 实际采用的 scale 与 render scale 一致；
- auto-fit 请求已消费；
- exact/bbox overlay 已提交，或明确判定不存在/不可用于降级页；
- `searchSession` 中保存的 PDF page、scale 与内部 scroll 已恢复；
- 不处于 loading，且不存在阻塞 error。

自动适配和聚焦现在记录“实际采用的比例”。不需要更新 zoom 时，settled scale 就是当前比例；需要更新时，settled scale 才是目标比例。

统一 `searchSession` 继续是唯一 Workspace 返回状态来源。导航前会在现有 Preview 数据中记录 `document_id`、`chunk_id`、requested page、scale、PDF 内部 scroll top/left；返回后由同一个 `PdfLocationPreview` 消费，不创建第二套 PDF Preview 或搜索状态。

### 降级与错误语义

- 请求页超过 PDF 页数：显示最后一页，标记 page fallback，不显示原请求页的错误高光，恢复流程可以结束。
- 缺少 bbox/highlight：页面渲染完成后允许 semantic-ready，不永久等待 overlay。
- PDF document、worker、page 或 render 真实失败：保持 `data-preview-ready=false`，并保留错误状态。
- 新文档或新选择：旧 render/restore key 不匹配，ready 自动重置。
- 没有固定 sleep、CDP bypass、mock PDF 或永久 true 条件。

## Workspace 与 PDF 回归

生产前端探针使用真实 PDF.js、两页 PDF fixture 和第 2 页目标位置。目标位于页面下部，因此 PDF Preview 内部滚动位置为非零。

验证覆盖：

- 1440、1600、1920 viewport；
- page 2；
- exact highlight 与两段 bbox；
- canvas CSS/backing dimensions；
- scale 与手动 zoom；
- PDF 内部独立滚动；
- 结果切换；
- Evidence Basket 保留；
- Workspace round-trip；
- query、Preview、page、highlight、scale、scroll/location 与 basket 恢复；
- `data-preview-ready=true`。

1600 viewport 连续完成 5 次 Workspace 往返，未出现偶发失败；1440 和 1920 各完成同一往返契约。

## Stable tree-hash 根因与新契约

旧构建 helper 使用 `Sort-Object FullName` 和文本行拼接。该排序依赖当前文化；带连字符或不同 locale 的文件名在不同进程中可能获得不同顺序，使相同逐文件内容产生不同聚合 hash。

新契约 `search.tree-hash.v1`：

1. 递归枚举普通文件；
2. 相对路径分隔符统一为 `/`；
3. 使用 `OrdinalIgnoreCase` 排序，并用 ordinal 作为相等项的确定性决胜；
4. hash 中保留规范化后路径的实际大小写；
5. 输入头为 `SearchTreeHashV1\0` 与文件数；
6. 每个文件依次写入 UTF-8 路径字节长度、路径字节、文件长度、原始 32-byte SHA256；
7. 所有整数使用无符号 64-bit little-endian；
8. 单文件 SHA256 算法不变；
9. 不使用时间戳或枚举顺序；
10. 空目录明确不参与 tree hash。

测试覆盖中文路径、大小写差异、`file2`/`file10`、空文件、空目录、相同内容不同路径、相同路径不同内容、反向创建顺序、`en-US`/`tr-TR`/`zh-CN` locale，以及独立进程重复计算。Node 独立实现的二进制编码得到相同 SHA256。

## Candidate1 只读重算

以下两个保留目录分别在独立进程中计算 3 次，共 6 次：

- canonical candidate1；
- candidate1 独立 smoke 副本。

六次结果完全一致：

- schema：`search.tree-hash.v1`；
- 文件：409；
- 字节：316,477,470；
- SHA256：`8A4A5D96072A3D1997F42F3C1FA58C33163BD65EEAEF033B6C6C5E1A0C50A269`。

该结果只读证明 candidate1 与 smoke 副本逐文件一致；不会改变 candidate1 的完整 smoke 失败结论。

## 测试结果

| 验证 | 结果 |
| --- | --- |
| Python/Core | 194 passed |
| tracked Python compile | 397/397 |
| `app.main` import / routes | PASS / 93 routes |
| Frontend | 28/28 |
| Desktop | 56/56 |
| MCP | 23/23 |
| PDF Preview 三档 viewport | PASS |
| Workspace 连续 5 次 round-trip | PASS |
| Evidence Basket restore | PASS |
| stable tree-hash contract | PASS |
| Vite production build | PASS |
| MCP widget/server build | PASS |
| packaged source resource contract | 33/33 |
| 四个 lockfile | SHA256 未变化，Git diff 为 0 |
| 旧 Build ID（正式源码/新 frontend bundle） | 0 / 0 |

全量 Desktop 首次与 Vite build 并行执行时，探针恰好加载到正在被替换的动态模块，记录为验收编排冲突。Vite 完成后串行重跑完整 Desktop，56/56 通过；未修改断言、timeout 或 Preview ready 条件。

## 生产数据保护

修复和测试没有写入生产数据。只读 guard 复核：

- 文件：191；
- 目录：92；
- 总字节：670,300,309；
- 全树 SHA256：`7de213d494bb5387b21e037248a2da4fcf3c51dbaf148cfcc28acb5240e37c64`；
- SQLite：41 个，全部 `integrity_check=ok`，`foreign_key_check=[]`；
- FTS：11,803；
- passage vectors：11,373；
- object vectors：35；
- legacy vectors：6,114；
- Zotero note vectors：161；
- WAL/SHM：0。

## Candidate2 前置结论

本批修复通过后，新的修复提交才是 candidate2 唯一合法 source commit。candidate2 必须使用正式 `build_windows.ps1`、新的 Build ID 和独立 output root 构建，并重新完成 package smoke、身份交叉验证、生产数据无漂移与受控退出；不得沿用 candidate1 的 smoke 结论。
