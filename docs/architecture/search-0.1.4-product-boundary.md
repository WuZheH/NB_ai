# Search 0.1.4 产品边界与 canonical architecture

本文定义 Search 0.1.4 的正式产品边界、状态所有权与运行入口。新增功能应接入下述 canonical implementation；不得通过复制页面、状态、Preview、进程管理器或路径规则扩展产品。

## 产品边界

Search 是本地优先的科研资料检索桌面应用。0.1.4 的正式能力包括：

- 导入和读取本地 PDF；
- 检索 PDF 正文、Zotero 标注与笔记；
- 关键词搜索和高质量搜索；
- 来源、文档和上下文筛选；
- 结果选择、证据篮子和 Research Workspace；
- PDF 原文页定位与高光；
- 通用的文档、章节、笔记和证据只读上下文；
- 可选的 Zotero、Codex MCP 和 ChatGPT App 集成。

以下能力不属于 0.1.4 的正式产品入口：

- 绑定特定文档、章节、审核记录或外部条目的工作流；
- 领域专用的 chapter-review 写入、审批或批处理流程；
- API 失败后返回预置论文内容、历史统计或示例证据；
- 自动创建、修改或持久化第三方 Tunnel/ChatGPT 配置；
- 随源码分发用户数据库、PDF、索引、模型、凭据或日志。

若未来需要 chapter-review 写入能力，应先定义独立、可参数化的产品契约、权限模型和数据迁移方案，不得从只读 Workspace 隐式进入。

## Canonical frontend

### 搜索与路由

唯一用户可见搜索入口是“搜索”，canonical 路由为 `/retrieval`：

```text
frontend/src/app/App.jsx
  -> frontend/src/pages/LocalRetrievalPage.jsx
  -> frontend/src/services/retrievalApi.js
  -> /api/v1/retrieval/*
```

历史搜索 URL 只允许在 `frontend/src/app/routes.js` 中做最小重定向。重定向不得拥有页面、业务状态或结果组件。

搜索模式、筛选、结果、当前选择、Preview、证据篮子和滚动位置由 `frontend/src/features/retrieval/state/searchSession.js` 所代表的单一 session 管理。Workspace 不得维护第二套检索状态。

### PDF Preview

`frontend/src/PdfLocationPreview.jsx` 是唯一 PDF 页定位与高光实现。搜索、资料详情和 Workspace 只能通过适配 props 复用它，不得复制 PDF.js 加载、页码、exact 高光或滚动逻辑。

### Workspace

Workspace 的职责是使用当前 search session 组织文档、章节、PDF、笔记和证据上下文：

```text
/retrieval
  -> captureSearchSessionBeforeNavigation()
  -> /workspace[/books/:documentId[/chapters/:chapterId]]
  -> /retrieval
  -> 恢复同一个 search session
```

后端不可用或上下文不存在时，Workspace 返回通用空状态和可操作指引；不得注入具体资料内容。章节信息在 0.1.4 中只读。

## Canonical backend

FastAPI 的唯一应用装配入口是 `app.main:app`。`app/main.py` 负责注册正式 API router；业务逻辑由 `app/services/` 编排，`app/domains/` 只承载有明确调用方的领域实现。

统一搜索使用 `/api/v1/retrieval/*`。旧搜索 API 不得作为 Workspace 或新 UI 的数据源。空数据目录必须产生空资料库响应或可理解的诊断，不得因缺少数据库、索引或模型而在导入阶段崩溃。

chapter-review 代码只有在仍被测试、迁移或通用只读能力调用时才可保留；保留模块不等于正式产品入口。正式 API router 不暴露未参数化的专用写入流程。

## Canonical desktop runtime

桌面运行链路只有一套 coordinator：

```text
Electron main application
  -> RuntimeCoordinator
  -> LauncherClient
  -> scripts/runtime/notebook_ai_launcher.py
       -> FastAPI (loopback)
       -> MCP server (loopback)
```

`RuntimeCoordinator` 负责就绪检查、所有权和退出语义；Python launcher 负责本地子进程生命周期。renderer、托盘和设置页只消费统一状态，不创建另一套 supervisor 或 process manager。

打包应用从 Electron `resources/app/runtime-project` 定位只读运行时代码，从 `resources/search-assets/frontend` 加载同一次正式构建产生的前端。源码运行从仓库根目录定位，不依赖临时工作目录或历史构建产物。

## MCP 与 Tunnel 边界

MCP 的唯一 Node 入口是 `integrations/notebook_ai_chatgpt_app/server/index.ts`，HTTP 端点是 loopback `/mcp`，健康检查是 `/healthz`。`search`、`fetch` 和 `export_evidence` 通过本地 FastAPI 读取同一资料库。

本地 Search、Zotero 和 Codex MCP 不需要公网 Tunnel。ChatGPT App 需要独立的 HTTPS 入口：

```text
ChatGPT App -> HTTPS Tunnel -> loopback MCP /mcp
```

Quick Tunnel 仅由独立辅助脚本用于临时开发验证；地址可能变化。持久部署需要用户在 Search 外部自行提供并管理 named tunnel、域名、凭据和认证策略。Search 管理 FastAPI 与 MCP 的本地进程生命周期，但对 Tunnel 只做只读诊断，不启动、暂停、恢复或配置 Tunnel，也不自动修改第三方账户或应用配置，不把凭据写入普通状态界面或 Git。

## 路径、配置与数据

路径解析遵循以下顺序：显式 `SEARCH_*` 配置、平台用户目录、安全探测、可理解的缺失错误。Python 的 canonical 路径定义位于 `app/core/paths.py`，Electron 的桌面配置入口位于 `integrations/search_desktop/electron/main/config.js`；二者必须表达同一数据目录与运行态边界。

代码、用户数据和运行态彼此分离：

- 代码与打包 runtime 是只读来源；
- `SEARCH_DATA_DIR` 指向可替换的用户资料目录；
- 用户配置、日志和进程状态位于平台的 Search 用户目录；
- 数据库、PDF、Zotero snapshot、FTS、向量索引、模型、凭据和日志不进入源码仓库。

兼容环境变量只能作为有测试覆盖的最小适配层存在。新代码必须使用 `SEARCH_*` 名称，不得再增加同义配置来源。

## 变更约束

合并前至少验证：

1. 导航中只有一个“搜索”；
2. 旧 URL 只重定向到 `/retrieval`；
3. Workspace 往返恢复同一个 search session；
4. 所有 PDF 预览调用复用 `PdfLocationPreview`；
5. 空数据目录可以启动并显示空资料库状态；
6. FastAPI、MCP 和 Electron 各只有一个正式启动入口；
7. packaged frontend 来自当前 source commit 的正式构建；
8. tracked 源码不包含用户数据、凭据、本机路径或临时 Tunnel 地址。

任何兼容层都必须有明确调用方、契约测试和移除条件。没有调用方的旧实现不应通过 fallback 或构建标识分支继续保留。
