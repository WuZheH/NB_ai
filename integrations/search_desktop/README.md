# Search Desktop

Search Desktop is the Windows shell for the Search React application. It does not contain a second search implementation: it starts the local Runtime Supervisor, waits for FastAPI and MCP, and then serves the existing `frontend/dist` bundle on `http://127.0.0.1:5173`, the established allowlisted frontend origin.

## Product boundaries

- Search owns importing, indexing, search, preview, and stable fragment IDs.
- ChatGPT uses the existing MCP server.
- Zotero Locator accepts a fragment ID and performs location inside Zotero.
- `SEARCH_*` is the public configuration surface. Historical internal aliases remain compatibility-only and are not product branding.

The desktop process never runs `npm install`, `npm ci`, or a full index build at startup. The renderer build and Electron dependency are development or packaging prerequisites, not runtime work.

The private unpacked Windows build is produced with `npm run build` under
`dist/win-unpacked/`. Its package step avoids electron-builder's optional
signing helper (which can require symbolic-link privileges on Windows), then
applies the Search icon and version metadata with the fixed project-local
`rcedit` dependency. No administrator rights, global install, or signing key
is required. Portable release archives do not include
`search-desktop.local.json`; optional machine-local configuration is created
explicitly by the user and must never be committed.

## One-time development preparation

Only run dependency installation after it has been explicitly authorized for this project. Electron is pinned exactly in `package.json`; it is not installed globally.

```powershell
npm --prefix frontend run build
npm --prefix integrations/search_desktop start
```

If `frontend/dist/index.html` is absent, Search opens a local diagnostic screen with the error `renderer_build_missing`. Secure Tunnel configuration is optional for local search and does not block the window.

## Runtime and shutdown

Search invokes:

```text
$env:SEARCH_PYTHON
  -B scripts/runtime/notebook_ai_launcher.py <command>
```

with `shell=false` and a hidden Windows process. Closing the window minimizes Search to its tray by default. “完全退出” asks the Runtime Supervisor to stop only when this desktop session started that runtime; a pre-existing healthy runtime is reused and left running.

The autostart setting owns a separate current-user `Search Desktop` Task Scheduler entry whose action is the packaged Search executable. Development mode and a missing packaged executable report `search_desktop_executable_unavailable` and never register a task. No registry Run key or administrator service is used. Merely opening Search only reads the Desktop task status and never changes either the Desktop task or the legacy runtime-only task.

## Security

- Renderer content is served on `127.0.0.1` only.
- `/api` is proxied only to the configured loopback FastAPI URL.
- Electron renderer uses context isolation, sandboxing, no Node integration, and an allowlisted preload bridge.
- Navigation and new-window requests outside the Search loopback origin are blocked.
- Runtime command output is capped and no query, note, PDF text, fragment ID, provenance, or secret is logged by this shell.
