# Search ChatGPT and Codex Integration

Audit date: 2026-07-24

## Chosen route

The primary route is:

```text
Search MCP (loopback)
  -> OpenAI Secure MCP Tunnel
  -> private developer-mode Search app
  -> Search plugin
  -> ChatGPT / Codex
```

OpenAI currently documents full MCP support, including write/modify actions,
for ChatGPT Business and Enterprise/Edu workspaces. Pro users can build Apps
SDK apps, but custom MCP connections in developer mode are currently limited
to read/fetch permissions. Search therefore keeps an authenticated Actions
fallback for accounts or workspaces where write-capable custom MCP is not
available.

Official sources:

- [Connect from ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt)
- [Developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt-beta)
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Security and privacy](https://developers.openai.com/apps-sdk/guides/security-privacy)
- [Authentication](https://developers.openai.com/apps-sdk/build/auth)
- [Build plugins](https://learn.chatgpt.com/docs/build-plugins)

## Secure MCP Tunnel

Secure MCP Tunnel is outbound-only. `tunnel-client` reaches the loopback Search
MCP server and polls an OpenAI-hosted endpoint; Search does not open inbound
ports or publicly expose 8000/8787, SQLite, the data root, or PDF directories.

Provisioning requires a Platform `tunnel_id`, a runtime API key, Tunnel
permissions, and the official `tunnel-client`. These are account actions and
secret material, not repository state. Candidate12 does not download a client,
create a key, or write a secret without separate user authorization.

## Plugin

The repository contains a private Search plugin with:

- the local MCP configuration;
- one Search workflow skill;
- an App mapping file reserved for the real `plugin_asdk_app...` ID.

The workflow skill contains only tool-use policy. Search logic remains in Core.
Official plugin guidance requires creating the developer-mode App first, then
copying its real `plugin_asdk_app...` ID into `.app.json`. Until the user
creates that App, the repository intentionally does not fabricate an ID.

## Actions fallback

The thin Actions adapter exposes the same eight operations under
`/actions/v1/*`, requires an explicit bearer token, caps request bodies, and
returns compact stable errors. Its OpenAPI document includes the server only
when `SEARCH_ACTIONS_PUBLIC_BASE_URL` is an explicit HTTPS URL.

Current official GPT guidance states:

- Actions can retrieve data and take actions through external APIs.
- A GPT can use Apps or Actions, but not both.
- Pro mode does not support Actions; the GPT editor offers a supported
  non-Pro model instead.

Source:
[Configuring actions in GPTs](https://help.openai.com/articles/9442513)

Actions are used only if the user's actual ChatGPT UI cannot enable the private
App. No public raw backend or unauthenticated gateway is acceptable.

## Real acceptance

Repository tests and local MCP smoke are not ChatGPT acceptance. The remaining
manual acceptance must use the user's real account:

1. enable Developer mode;
2. create/select the Secure MCP Tunnel;
3. create the private Search app and refresh eight tools;
4. copy the real App ID into the plugin;
5. run search, fetch, export, and list-library prompts;
6. run import and delete only against an isolated fixture with separate user
   confirmations.

Production import/delete are outside Candidate12 development acceptance.

## Completed local validation

- Core: 269/269, including a real isolated import commit and duplicate check.
- Desktop: 77/77.
- MCP/Actions/widget: 32/32.
- Frontend and MCP widget/server builds: passed.
- Source Runtime read-only smoke: authoritative retrieval ready, all eight MCP
  tools listed, three `motion diffusion` results, fetch provenance present,
  evidence export produced, and five compact library rows returned.
- Runtime stopped through its own supervisor; ports 18000 and 18787 returned
  to zero.
- Production remained 197 files, 670,314,964 bytes, with tree hash
  `93A2612B06A74ED31504AA1A371CE766B5E8D9A9A5B977A61CD1FD9B639594EC`.
- Non-empty SQLite integrity remained 41/41 `ok`, foreign-key issues remained
  zero, and WAL/SHM remained zero.
- `C:\Users\ROG\AppData\Local\Search` remained at four files, 16,926 bytes,
  tree hash
  `ED8E25839E9AD8B602DC7FCEF35A107039B10119A30B8CE1D2BDC1598C9628A7`.

These results establish local readiness only. They do not replace real
ChatGPT/Plugin acceptance.
