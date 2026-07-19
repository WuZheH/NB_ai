# Search 本机模型配置契约

## 结论

Candidate2 的 package 与生产检索数据都没有损坏。独立 smoke 能通过高质量检索，是因为 smoke 进程临时设置了 embedding 和 reranker 路径；正式桌面入口没有等价的持久配置，于是旧代码回落到 package 外不存在的 `data/models`。该差异导致高质量搜索和 MCP `search` 失败，而关键词搜索、PDF Preview、Workspace 与 Evidence Basket 仍可使用。

Search 0.1.4 现在使用一个版本无关的本机配置文件。Electron 以 `app.getPath("userData")` 解析 `machine-config.json`，正式 Windows 默认位置为 `%APPDATA%\Search\machine-config.json`。配置位于 package 和 production data 之外，不进入 Git，也不会随 candidate 更新而丢失。

## 已审计模型

本机 Search 检索实际使用：

- embedding：`Qwen3-Embedding-0.6B`，本机目录 `D:\LEARNING\Tools\model_cache\Qwen3-Embedding-0.6B`；`qwen3` 架构、hidden size 1024，Sentence Transformers 模块包含 Pooling 与 Normalize。
- reranker：`Qwen3-Reranker-0.6B`，本机目录 `D:\LEARNING\Tools\model_cache\Qwen3-Reranker-0.6B`；`qwen3` 架构、hidden size 1024，CrossEncoder 模块包含 LogitScore。
- Marker：`D:\LEARNING\Tools\marker_cache\datalab\models`，包含 layout、OCR error detection、table recognition、text detection 与 text recognition 模型。它服务 PDF 转换/OCR，不是高质量检索模型，不能写入本配置。

两个检索模型均已只读验证 `config.json`、tokenizer 配置、`modules.json` 和 `model.safetensors`。本次不下载、复制或移动任何模型。

## Schema

```json
{
  "schema_version": 1,
  "high_quality_search": {
    "embedding_model_path": "<Qwen3-Embedding-0.6B 的绝对目录>",
    "reranker_model_path": "<Qwen3-Reranker-0.6B 的绝对目录>"
  }
}
```

两个字段均必需且职责单一。配置拒绝相对路径、不存在目录、错误模型结构、未知 schema 和未知字段，不接受 API key、token 或密码。

## Canonical 传递链

```text
Electron main
  -> app.getPath("userData")/machine-config.json
  -> launcher --machine-config <absolute-path>
  -> RuntimeConfig 只读解析与验证
  -> supervisor 向 FastAPI/MCP 子进程传递同一配置文件事实
  -> FastAPI 高质量检索与 MCP backend adapter 共享同一规范化状态
```

`SEARCH_MACHINE_CONFIG_PATH` 仅是 supervisor 向受控子进程传递已验证文件位置的内部进程通道，不是用户设置模型路径的接口。Electron 会清除 ambient `SEARCH_EMBEDDING_MODEL`、`SEARCH_RERANKER_MODEL` 及旧 NOTEBOOK_AI 模型变量；正式 package 不依赖 cwd、源码根或旧项目根。

## 状态与错误

配置层明确区分：

- `config_missing`
- `config_invalid_json`
- `schema_unsupported`
- `required_field_missing`
- `model_path_not_absolute`
- `model_path_not_found`
- `model_structure_invalid`
- `model_ready`
- `model_load_failed`

配置缺失或无效时，Search 主程序、关键词搜索、PDF Preview、Workspace 与 Evidence Basket 继续启动；高质量搜索返回 HTTP 503 结构化错误，MCP `search` 保留相同安全 error code。它不会伪装成“无结果”，也不会触发 supervisor 无限重启。

## 隐私边界

用户可见 health、Runtime status、MCP 错误与前端错误只公开配置状态、模型名称、模型目录 basename 和路径 SHA256，不公开完整模型路径、Windows 用户目录、源码路径或配置正文。模型加载异常被归一化为 `model_load_failed`，日志不包含原始异常中的绝对路径。

## 官方配置工具

`scripts/configure_search_machine.ps1` 支持 `inspect`、`validate` 和 `set`。`set` 在写入前用生产解析器验证模型角色与结构，使用同目录临时文件原子替换；已有受支持配置会备份为 `machine-config.json.bak`，未知 schema 不会被覆盖。工具不修改环境变量、PATH、注册表、package 或 production data，也不下载或移动模型。

## 测试边界

自动化覆盖无配置、空/非法 JSON、未知 schema、缺字段、相对/不存在路径、中文和空格路径、trailing slash、错误模型角色、原子备份、显式 Electron/launcher/supervisor 传递、无 cwd/旧根 fallback、无配置可启动、结构化 FastAPI/MCP 错误、health 路径脱敏和 package 资源契约。所有写入测试使用项目 `.codex_tmp` 下的隔离 fixture；production data 保持只读。
