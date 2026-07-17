# Search for ChatGPT

This local Developer Mode app lets ChatGPT search the private Search collection, render PDF passages and Zotero reading notes in an embedded React widget, fetch one complete evidence fragment, and export selected evidence. The MCP server is deliberately a thin HTTP adapter: embedding, semantic recall, reranking, fragment resolution, and evidence formatting remain in the Search Python backend.

The app does **not** call the OpenAI API, run an external LLM, access SQLite from Node or the widget, or write to the Search collection.

## Architecture

```text
ChatGPT
  -> HTTPS tunnel -> MCP Streamable HTTP /mcp (loopback-only Node server)
  -> Search FastAPI on 127.0.0.1
  -> existing Qwen3 embedding + semantic recall + Qwen3 reranker + final ranking
```

The React widget uses `@modelcontextprotocol/ext-apps` `App` with
`PostMessageTransport`, registers its tool-result listener before the
initialize handshake, and then uses the official MCP Apps bridge for tool
calls, model-context updates, and link opening. `window.openai` is used only as
an additive ChatGPT compatibility surface. The widget never calls the backend
or production data directly.

The three MCP tools are read-only:

- `search`: extended Developer Mode search input (`query`, `limit`, `source_types`, `document_ids`, `include_context`), with at most 20 results.
- `fetch`: one stable `fragment_id`.
- `export_evidence`: at most 50 fragment ids in `markdown`, `jsonl`, or `json` format.

This extended `search` contract is intended for this private Developer Mode app; it is not the strict two-tool company-knowledge `search`/`fetch` compatibility profile.

## Requirements

- Search backend dependencies in the existing project conda environment.
- Node.js 20.19 or newer.
- An HTTPS tunnel program already installed by the user for the short connection test.
- ChatGPT Developer Mode access.

No OpenAI API key is used or required.

## Install and build

From this directory:

```powershell
npm ci
npm run check
```

`npm ci` uses the committed lockfile. The build produces `web/dist/widget.html` and `dist/server/index.js`; neither `node_modules` nor local `.env` files belong in Git. MCP Inspector is kept outside the app dependency tree because it is a separate interactive testing application, not a runtime dependency.

## Start Search

From the repository root, use the project conda interpreter (do not use an unverified system Python):

First validate the separate derived Zotero user-note index. This is read-only:

```powershell
$PythonExe = $env:SEARCH_PYTHON
if (-not $PythonExe) { throw "Set SEARCH_PYTHON to the active Python 3.11 interpreter." }
& $PythonExe -B scripts/index/status_zotero_note_vectors.py
```

If it is not ready, explicitly build it once; later source changes use the
incremental sync command. These commands write only
`data/vector_store/zotero_user_notes_v1/`, never production SQLite or Zotero:

```powershell
& $PythonExe -B scripts/index/build_zotero_note_vectors.py
& $PythonExe -B scripts/index/sync_zotero_note_vectors.py
```

Then start FastAPI:

```powershell
& $PythonExe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify the local backend before exposing the MCP endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/retrieval/index/status
```

This status call is read-only and does not rebuild the FTS index. Do not bind the backend to a public interface.

## Start the MCP app

Copy `.env.example` to an untracked `.env` only if your process runner loads it. The server itself reads process environment variables and will refuse to start without the explicit development switch:

```powershell
$env:SEARCH_BACKEND_URL = "http://127.0.0.1:8000"
$env:SEARCH_ALLOW_UNAUTHENTICATED_MCP_DEV = "1"
$env:SEARCH_MCP_PORT = "8787"
npm start
```

Or run `scripts/start-mcp-dev.ps1`. The server always binds to `127.0.0.1` and prints a security warning. Logs contain only the tool name, duration, result count, and error code—never query text, PDF text, notes, fragment ids, or provenance.

Check the local endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/healthz
```

Run the official SDK Streamable HTTP smoke after both services are up. It performs the full read-only `search → fetch → export_evidence` sequence and prints only compact status/count metadata, never evidence text or fragment IDs:

```powershell
npm run smoke -- http://127.0.0.1:8787/mcp "避免脚步滑动"
```

## Test with MCP Inspector

The official testing workflow uses MCP Inspector. If MCP Inspector 0.22.0 is already available locally, point the product script to it:

```powershell
$env:SEARCH_MCP_INSPECTOR = "X:\Tools\mcp-inspector.cmd"
.\scripts\inspect-mcp.ps1
```

In Inspector:

1. Select **Streamable HTTP**.
2. Enter `http://127.0.0.1:8787/mcp`.
3. Connect and run **List Tools**.
4. Confirm `search`, `fetch`, and `export_evidence` are present and read-only.
5. Call `search`, pass one returned `fragment_id` to `fetch`, then pass that id to `export_evidence`.
6. Open the widget resource and confirm the MIME type is `text/html;profile=mcp-app`.

The repository tests use the official MCP SDK in-memory transport and a real Streamable HTTP client smoke; they do not write production data. Installing Inspector itself is optional and separate from `npm ci`; if it is not already installed, obtain the pinned `@modelcontextprotocol/inspector@0.22.0` only under the user's normal package-install policy.

## Manual development fallback: short-lived HTTPS tunnel

Local Search, Codex, and Zotero do not need a Tunnel. ChatGPT App requires an HTTPS endpoint; the commands below are only a temporary, unauthenticated development fallback and must not be configured for login autostart.

For a brief manual Developer Mode diagnostic, start a tunnel only after both local services pass their checks. Use a tunnel program you already trust and have installed; do not put its token or generated URL in Git. Examples:

```powershell
$env:SEARCH_CLOUDFLARED = "C:\Tools\cloudflared.exe"
powershell -NoProfile -ExecutionPolicy Bypass -File ..\..\scripts\start_quick_tunnel.ps1
```

Only use the `/mcp` URL printed after the script's public health check succeeds. The script does not modify ChatGPT App. Clear the development switch from the current shell after testing.

Unauthenticated tunnel mode is for brief local Developer Mode testing only. A hosted deployment must implement the Apps SDK/MCP OAuth 2.1 protected-resource flow and token validation before accepting traffic. `SEARCH_BACKEND_BEARER_TOKEN` is only a backend-authentication extension point; never commit a real value.

## Connect from ChatGPT

1. In ChatGPT open **Settings → Security and login → Developer mode** and enable Developer Mode.
2. Open **Settings → Plugins** (or `chatgpt.com/plugins`).
3. Select **Create** / the plus button for a new developer-mode app.
4. Name it **Search**.
5. Use this description:

   > Searches my private Search collection for relevant PDF passages and Zotero reading notes before answering research and literature questions.

6. Enter the short-lived HTTPS tunnel URL ending in `/mcp`.
7. Finish creating the app and enable it for a new chat.
8. After changing tool or widget metadata, return to the app in **Settings → Plugins** and use **Refresh** before testing again.

First test prompt:

> 请在我的资料中搜索“避免动作生成中的脚步滑动”，分别列出 PDF 原文和我的 Zotero 笔记，并标注页码和 fragment_id。

Creating and enabling the app in ChatGPT is a manual user action; this repository never registers the app automatically. Until `search`, `fetch`, and `export_evidence` have all succeeded inside ChatGPT, the truthful status is `PENDING_CHATGPT_TUNNEL_CONFIGURATION`.

## Official sources and example lineage

Implementation follows the current official OpenAI Apps SDK documentation:

- [Build an MCP server](https://developers.openai.com/apps-sdk/build/mcp-server/)
- [Build your ChatGPT UI](https://developers.openai.com/apps-sdk/build/chatgpt-ui/)
- [Apps SDK quickstart](https://developers.openai.com/apps-sdk/quickstart/)
- [Define tools](https://developers.openai.com/apps-sdk/plan/tools/)
- [Apps SDK reference](https://developers.openai.com/apps-sdk/reference/)
- [Authentication](https://developers.openai.com/apps-sdk/build/auth/)
- [Connect from ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt/)
- [Testing your integration](https://developers.openai.com/apps-sdk/deploy/testing/)
- [Official examples](https://developers.openai.com/apps-sdk/build/examples/)

The widget structure and list-card interaction patterns are based on the official [`openai/openai-apps-sdk-examples`](https://github.com/openai/openai-apps-sdk-examples) **Pizzaz list-view** example (repository main observed at `18cc38e`), while the server transport follows the official stateless Streamable HTTP quickstart. The lockfile uses the current compatible registry contract (`@modelcontextprotocol/ext-apps` 1.7.4 with `@modelcontextprotocol/sdk` 1.29.0); this supersedes older quickstart version snippets whose peer range no longer resolves. No search or ranking algorithm is copied into TypeScript.
