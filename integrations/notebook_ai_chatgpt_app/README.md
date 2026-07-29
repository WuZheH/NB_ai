# Search for ChatGPT

This local Developer Mode app exposes the Chat-first Search surface: research
search, exact evidence fetch, evidence export, compact library listing, PDF
import preview/commit, and safe book deletion preview/commit. The MCP server is
a thin adapter; ranking, importing, deletion, recovery, database, FTS, and
vector behavior remain in the Search Python backend.

Node and the widget never access SQLite. Read tools cannot mutate Search.
`import_document` and `delete_document` are explicit write tools and require a
fresh preview token plus current-conversation confirmation.

## Architecture

```text
ChatGPT / Codex
  -> Secure MCP Tunnel -> MCP Streamable HTTP /mcp (loopback-only Node server)
  -> Search FastAPI on 127.0.0.1
  -> existing Qwen3 embedding + semantic recall + Qwen3 reranker + final ranking
```

The React widget uses `@modelcontextprotocol/ext-apps` `App` with
`PostMessageTransport`, registers its tool-result listener before the
initialize handshake, and then uses the official MCP Apps bridge for tool
calls, model-context updates, and link opening. `window.openai` is used only as
an additive ChatGPT compatibility surface. The widget never calls the backend
or production data directly.

The stable tool surface is:

- `search`: compact ranked snippets, at most 20 results.
- `fetch`: one stable `fragment_id`.
- `export_evidence`: at most 50 fragment ids in `markdown`, `jsonl`, or `json` format.
- `list_library`: compact active/archived library results.
- `import_preview`: ChatGPT PDF attachment or Search Import Inbox preview.
- `import_document`: confirmed import through the existing Core pipeline.
- `delete_preview`: compact Candidate10 deletion preview.
- `delete_document`: confirmed destructive deletion through the existing Core
  transaction and recovery service.

The same Core has a thin authenticated Actions fallback. Apps and Actions are
never configured in the same GPT.

## Requirements

- Search backend dependencies in the existing project conda environment.
- Node.js 20.19 or newer.
- OpenAI Secure MCP Tunnel access for the private connection.
- ChatGPT Developer Mode access.

Tunnel provisioning uses a Platform tunnel runtime key managed outside this
repository. Search itself does not call an OpenAI model API.

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
4. Confirm all nine tools and their read/write/destructive annotations.

The results widget uses the dedicated
`https://cread-search-widget.openaiusercontent.com` origin with an empty,
least-privilege network/resource CSP because the packaged widget is
self-contained.
5. Call `search`, `fetch`, `export_evidence`, and `list_library`. Use only
   isolated fixtures for confirmed import/delete tests.
6. Open the widget resource and confirm the MIME type is `text/html;profile=mcp-app`.

The repository tests use the official MCP SDK in-memory transport and a real Streamable HTTP client smoke; they do not write production data. Installing Inspector itself is optional and separate from `npm ci`; if it is not already installed, obtain the pinned `@modelcontextprotocol/inspector@0.22.0` only under the user's normal package-install policy.

## Secure MCP Tunnel

Local Search keeps 8000 and 8787 on loopback. The formal ChatGPT connection
uses the official outbound-only Secure MCP Tunnel. Do not use Quick Tunnel,
public raw ports, or an unauthenticated public URL. Tunnel ID, runtime API key,
and profile live outside Git and logs.

## Connect from ChatGPT

1. In ChatGPT open **Settings → Security and login → Developer mode** and enable Developer Mode.
2. Open **Settings → Plugins** (or `chatgpt.com/plugins`).
3. Select **Create** / the plus button for a new developer-mode app.
4. Name it **Search**.
5. Use this description:

   > Searches my private Search collection for relevant PDF passages and Zotero reading notes before answering research and literature questions.

6. Select the configured Secure MCP Tunnel.
7. Finish creating the app and enable it for a new chat.
8. After changing tool or widget metadata, return to the app in **Settings → Plugins** and use **Refresh** before testing again.

First test prompt:

> 请在我的资料中搜索“避免动作生成中的脚步滑动”，分别列出 PDF 原文和我的 Zotero 笔记，并标注页码和 fragment_id。

Creating and enabling the app is a manual account action. Until all four read
workflows succeed in a real chat, the truthful status is
`PENDING_CHATGPT_TUNNEL_CONFIGURATION`.

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
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Plugins](https://learn.chatgpt.com/docs/plugins)
- [Official examples](https://developers.openai.com/apps-sdk/build/examples/)

The widget structure and list-card interaction patterns are based on the official [`openai/openai-apps-sdk-examples`](https://github.com/openai/openai-apps-sdk-examples) **Pizzaz list-view** example (repository main observed at `18cc38e`), while the server transport follows the official stateless Streamable HTTP quickstart. The lockfile uses the current compatible registry contract (`@modelcontextprotocol/ext-apps` 1.7.4 with `@modelcontextprotocol/sdk` 1.29.0); this supersedes older quickstart version snippets whose peer range no longer resolves. No search or ranking algorithm is copied into TypeScript.
