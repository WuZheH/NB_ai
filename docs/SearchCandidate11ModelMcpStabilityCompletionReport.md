# Search Candidate11 Model and MCP Stability Completion Report

## 1. Outcome

Candidate11 fixes the Candidate10 third-cold-start model-readiness failure and
the MCP error-envelope/schema mismatch without removing or reimplementing the
Candidate10 safe book archive/deletion workflow.

The accepted rebuilt package completed:

- the full source regression;
- the packaged desktop smoke, including isolated archive, restore, deletion,
  vector/FTS cleanup, and recovery-package checks;
- real MCP backend-unavailable and model-failed error probes;
- ten fresh packaged cold starts with no retried or re-counted round;
- five formal cold starts with user-visible UI acceptance and graceful tray
  exit in every round;
- a final read-only production-data integrity check.

No production book was imported, archived, restored, or deleted. The active
`Search Desktop` Scheduled Task now points to Candidate11, with no arguments.
Candidate9 remains intact as the rollback package.

## 2. Repository and release identity

| Item | Value |
| --- | --- |
| Repository | `WuZheH/NB_ai` |
| Branch | `codex/search-canonical-root-migration` |
| Candidate9 baseline | `3b6c95a8332442dd5d6036de0ebeafe9cbafa23c` |
| Candidate10 deletion feature | `a7cd6eb6970f6c0b810f02702b13cf135a74aa28` |
| Candidate10 build source | `d8ed1b64931a2867df74cd4fe00a0eae87c5579e` |
| Candidate10 report | `7dbe7a396bbb98c35ad102e3e6721ef1402dddb8` |
| Initial readiness/MCP fix | `93d9bc56d46965d483c66307b755af8f73ea63fa` |
| Accepted Candidate11 source | `ddbbaa060d82f6ebf2689ec4b593eadd16eb8fa6` |
| Candidate11 source URL | `https://github.com/WuZheH/NB_ai/commit/ddbbaa060d82f6ebf2689ec4b593eadd16eb8fa6` |
| Report commit | The commit containing this document; its 40-character hash is supplied in the final handoff because a commit cannot contain its own hash. |
| Ahead / behind before report commit | `0 / 0` |
| Tracked state before report commit | clean |

The Candidate11-only diff from the Candidate10 report commit contains 23 files,
1,319 insertions, and 135 deletions. No Candidate10 deletion source was
removed.

## 3. Candidate10 failure timeline and root cause

Candidate10 round 3 produced this observed sequence:

1. `Search.exe` and the runtime supervisor started;
2. FastAPI started and `/health` returned HTTP 200;
3. runtime presentation still reported the configured embedding and reranker
   paths as ready;
4. the first high-quality request entered lazy model access;
5. model initialization failed with `model_load_failed`;
6. backend health changed to model failure, but runtime retained stale
   `embedding_model_ready=true` and `reranker_model_ready=true`;
7. MCP `search` mapped the backend failure to an error-shaped
   `structuredContent`;
8. the tool still declared a success-only output schema;
9. the MCP client rejected the response with
   `-32602 structured_content_output_schema_mismatch`.

The audit found no production-database corruption, persistent model-cache lock,
or required fixed startup delay. The root cause was a state and contract design
defect:

- readiness was inferred from model configuration/path validity instead of
  executable model instances;
- embedding and reranker lazy singleton initialization was not represented by
  one authoritative state machine and was not serialized for concurrent first
  access;
- `/health` and the runtime presentation could therefore disagree after a
  runtime model failure;
- there was no deterministic inference self-check before declaring retrieval
  ready;
- MCP failure payloads used `structuredContent` even though each tool's
  `outputSchema` described only success;
- a real Node `fetch` connection refusal was not initially classified as
  `BACKEND_UNAVAILABLE`.

The correction does not hide failure with a fixed sleep, unbounded polling,
timeout inflation, automatic success downgrade, or a retry that changes an
acceptance result.

## 4. Authoritative model-readiness state

`app/runtime/model_readiness.py` is now the process-local authority for:

- `unconfigured`;
- `loading`;
- `ready`;
- `failed`;
- `recovering`.

The public safe status contains:

- `api_ready`;
- `retrieval_ready`;
- `model_state`;
- `embedding_state`;
- `reranker_state`;
- `last_model_error_code`;
- `last_state_change`.

`retrieval_ready=true` requires the API to be ready, valid model
configuration, and both model roles to be in the executable `ready` state.
Runtime presentation refreshes these values from backend health and derives the
legacy booleans from current state; it no longer preserves a stale ready value.
Responses expose stable error codes and model directory identities/hashes, not
full local paths, tracebacks, user directories, or secrets.

Embedding and reranker loads have independent process locks. A concurrent first
request observes the same initialization instead of starting a second model
construction. A later inference exception moves the role from `ready` to
`failed`, and runtime health observes that transition.

### 4.1 Deterministic self-checks

Before a role becomes ready:

- embedding runs one fixed short-string inference, checks finite values and the
  expected vector dimension, and writes no vector-store data;
- reranker scores one fixed query/document pair and checks a finite score;
- failure records a stable role-specific load, self-check, or inference code.

FastAPI lifespan prewarms the same singleton instances used by retrieval; it
does not load a second copy. `/health=200` by itself is not treated as
high-quality retrieval readiness.

## 5. MCP error contract

Successful `search`, `fetch`, and `export_evidence` calls retain their declared
success `outputSchema` and return matching `structuredContent`.

Failures now use the MCP tool-error path:

```json
{
  "isError": true,
  "content": [
    {
      "type": "text",
      "text": "{\"status\":\"error\",\"error_code\":\"...\",\"message\":\"...\"}"
    }
  ]
}
```

Failure responses intentionally omit `structuredContent`, so they cannot
violate a success-only output schema. The three tools share this contract and
return stable codes for backend unavailability, model loading/failure, index
failure, document/ID errors, timeout, and malformed backend responses. Server
stack traces, machine configuration, and secrets are not returned.

An actual packaged MCP server was tested, not only a mocked unit:

- connection refusal returned `BACKEND_UNAVAILABLE` for all three tools;
- simulated backend `model_load_failed` returned that code for all three tools;
- neither scenario produced `-32602`, schema mismatch, or error-shaped
  `structuredContent`;
- MCP still exposes exactly `search`, `fetch`, and `export_evidence`.

## 6. Explicit readiness log boundary

`model_readiness.py` writes a transition log only when `SEARCH_LOG_DIR` exists
and is non-empty. If it is missing or empty, logging is disabled while
readiness behavior remains active. There is no fallback to `LOCALAPPDATA`,
current working directory, home, or production data.

The Desktop supervisor is the single source of the log directory and explicitly
passes it to FastAPI and MCP child environments:

- packaged/QA runs use per-round isolated directories on `D:`;
- formal Desktop uses the existing controlled product log directory
  `C:\Users\ROG\AppData\Local\Search\logs`;
- renderer does not require the variable;
- the path is not exposed through API or MCP responses.

Regression coverage proves:

- unset `SEARCH_LOG_DIR` creates no readiness log;
- empty `SEARCH_LOG_DIR` creates no readiness log;
- changing `LOCALAPPDATA` alone cannot trigger a fallback write;
- an explicit temporary directory creates `model-readiness.jsonl`;
- the supervisor passes the explicit directory to FastAPI;
- packaged writable runtime, log, and temporary roots stay under the isolated
  `D:` run root.

### 6.1 Authorized mistaken-log deletion audit

The one user-authorized deletion was exactly:

`C:\Users\ROG\AppData\Local\Search\logs\model-readiness.jsonl`

Before deletion it was verified as an ordinary file, not a directory,
junction, symlink, or reparse point. Its metadata was captured before deletion:

- size: 1,575 bytes;
- SHA256:
  `8CBFCFB06896978A89A9B51D4003A65F89C0DBDDB039976212BE688C6CFB00A2`;
- creation and last-write timestamps: captured in the live deletion audit;
- content: only `search.model-readiness` state-log records.

Only that literal path was deleted. `Test-Path` then returned false. No parent,
other log, directory, configuration, or empty directory was removed. Candidate9
was not started, stopped, or modified by the deletion.

Before and after the rebuilt packaged acceptance, the C-drive guard remained
unchanged with the three pre-existing files:

- `logs\runtime.jsonl`;
- `runtime\status.json`;
- `runtime\supervisor.lock`.

Formal operation then created `logs\model-readiness.jsonl` through the explicit
supervisor setting, as intended. Final enumeration contains only those four
controlled files and no second implicit readiness-log location.

## 7. Rebuild history

Rejected evidence was preserved and never promoted:

| Build | Source | Result |
| --- | --- | --- |
| Candidate11 initial | `93d9bc56d46965d483c66307b755af8f73ea63fa` | rejected: readiness logger unexpectedly fell back to C-drive AppData |
| Candidate11 rebuild 1 | `8e5728e8525a3c14eaa180978c2c4581ba1b2572` | rejected: packaged smoke script was not Windows PowerShell 5.1 parse-safe |
| Candidate11 rebuild 2 | `cd2a92b49b7c546257f427f73e3f216adebf6122` | rejected: real MCP connection refusal mapped to malformed backend response |
| Candidate11 rebuild 3 | `6152c09fa2f0a10b7927956f0d626d0ba539bc98` | rejected: smoke recovery archive collided with retained prior evidence |
| Candidate11 rebuild 4 | `5ff8b565ae3fa263dadfb17305350c919ade4af4` | rejected: unique archive root was not propagated to the backend |
| Candidate11 rebuild 5 | `ddbbaa060d82f6ebf2689ec4b593eadd16eb8fa6` | accepted |

No rejected build was overwritten or deleted.

## 8. Accepted package

| Item | Value |
| --- | --- |
| Build ID | `20260724-search-0.1.4-canonical-root-candidate11-rebuild5-ddbbaa06` |
| Candidate | `D:\LEARNING\Tools\search\dist-candidates\Search-0.1.4-canonical-c11-r5` |
| Smoke working copy | `D:\LEARNING\Tools\SearchPackageSmoke\Search-0.1.4-canonical-c11-r5` |
| Active formal | `D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-ddbbaa06` |
| Candidate/formal files | 424 |
| Candidate/formal bytes | 316,690,360 |
| Candidate/formal `search.tree-hash.v1` | `70DED610E42097E6A36C20F4FE24DF41F030753A1CB8879D6344394D0870111B` |
| `Search.exe` SHA256 | `5EB6E3B5C1CCD39A7F84DC1725CE2706C210BB7296D728F49C0C1ECFA439D1DD` |

Candidate and formal identities match exactly. The smoke working copy retained
its isolated acceptance artifacts after testing and is not used as the formal
package. Production data, `machine-config.json`, and `desktop-runtime.json`
were not bundled.

## 9. Regression results

| Suite | Result |
| --- | --- |
| Core | 253 passed, 4 skipped |
| Frontend | 45 passed |
| Desktop | 77 passed |
| MCP widget/server | 28 passed |
| Python compile | passed |
| Vite production build | passed |
| MCP widget/server build | passed |
| Model state/readiness tests | passed |
| Runtime health/stale-ready tests | passed |
| Explicit readiness-log boundary tests | passed |
| MCP success/error schema tests | passed |
| Secret scan | passed |
| Mutation/write-route exposure scan | passed |

Candidate10 deletion regression remained green:

- management mode, archive, archived view, and restore;
- read-only deletion preview and expiring preview token;
- local mutation session and two-step confirmation;
- isolated transactional permanent deletion;
- recovery package before mutation;
- shared-object/vector protection;
- personal and Zotero note retention;
- external and managed-PDF retention defaults;
- FTS, passage vector, object vector, manifest, and orphan reconciliation;
- local-only mutation routes;
- no MCP deletion tool;
- ordinary Workspace entry points hidden;
- direct `/workspace` retained.

The packaged valid smoke used an isolated database, data root, vector index, and
recovery root. Its permanent-delete test did not reference production.

## 10. Ten packaged cold starts

All ten rounds were restarted from 1 after the explicit-log correction. No
failed round was retried or counted as success.

| Round | Startup seconds | Result |
| ---: | ---: | --- |
| 1 | 21.991 | passed |
| 2 | 23.409 | passed |
| 3 | 21.909 | passed |
| 4 | 21.468 | passed |
| 5 | 21.833 | passed |
| 6 | 20.876 | passed |
| 7 | 22.398 | passed |
| 8 | 21.441 | passed |
| 9 | 21.875 | passed |
| 10 | 20.872 | passed |

Every round verified:

- Desktop, supervisor, FastAPI, MCP, and listener ownership;
- authoritative `retrieval_ready=true`;
- embedding/reranker ready after self-check;
- keyword, Chinese high-quality, and English high-quality search;
- MCP `search`, `fetch`, and `export_evidence`;
- PDF streaming;
- read shelf and read-only deletion preview;
- default `/retrieval`;
- Evidence Basket;
- Workspace ordinary-entry hiding and `/workspace` deep link;
- management mode with dangerous selection reset;
- graceful exit, zero residual process, and zero residual port;
- unchanged C-drive packaged guard;
- unchanged production tree hash.

Summary: `10 passed / 0 failed`.

## 11. Five formal cold starts

The Scheduled Task was changed only after packaged acceptance. It has:

- executable:
  `D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-ddbbaa06\win-unpacked\Search.exe`;
- working directory: the same `win-unpacked` directory;
- arguments: none;
- final state after graceful exit: `Ready`.

| Round | API/retrieval/model | Search and MCP | Shelf/preview/PDF | User-visible UI | Graceful exit |
| ---: | --- | --- | --- | --- | --- |
| 1 | passed | passed | passed | passed | passed |
| 2 | passed | passed | passed | passed | passed |
| 3 | passed | passed | passed | passed | passed |
| 4 | passed | passed | passed | passed | passed |
| 5 | passed | passed | passed | passed | passed |

Round 3, where Candidate10 failed, returned:

- `api_ready=true`;
- `retrieval_ready=true`;
- `model_state=ready`;
- `embedding_state=ready`;
- `reranker_state=ready`;
- `last_model_error_code=null`;
- Chinese and English high-quality results;
- MCP three-tool success with no schema mismatch.

In every formal round the user inspected the real UI and used the real tray
`完全退出`. After every exit, Candidate11 processes and ports 5173, 8000, and
8787 reached zero. No force termination was used.

## 12. Production-data guard

| Check | Fixed baseline | Final |
| --- | ---: | ---: |
| Files | 191 | 191 |
| Bytes | 670,300,309 | 670,300,309 |
| `search.tree-hash.v1` | `0FC6E59C6A0B54469AD80D71F0E219F0C99E7BC8B3A623D1B3E020C86BDEBE20` | unchanged |
| SQLite query-only validation | 41/41 | 41/41 |
| SQLite `integrity_check` | 41/41 `ok` | 41/41 `ok` |
| Foreign-key issues | 0 | 0 |
| WAL/SHM | 0 | 0 |

Formal validation viewed the shelf and a real document's deletion preview, then
closed the preview. It never requested a production mutation session or called
archive, restore, import, batch delete, or permanent delete. No production
document, note, PDF, FTS row, vector, manifest, or cache was changed.

## 13. Rollback and user deletion

Candidate9 formal remains at:

`D:\LEARNING\Tools\search\integrations\search_desktop\dist\formal\Search-0.1.4-3b6c95a8\win-unpacked`

The pre-switch Candidate9 Scheduled Task action and production hash are retained
under the D-drive Candidate11 switch evidence. Candidate10 and all rejected
Candidate11 builds are also retained.

Candidate11 is the active accepted formal package. The user may now perform the
first production deletion personally:

1. open `已读书架`;
2. choose `管理书架`;
3. select the intended book;
4. choose `查看删除影响`;
5. review blockers, retained notes/PDF/shared data, vectors, files, and recovery
   summary;
6. choose the permanent-delete action only if the preview is safe;
7. enter the exact title or `删除`;
8. press `永久删除此书的 Search 数据`.

No production deletion was performed during Candidate11 acceptance.

## 14. Status markers

- `PASS_SEARCH_MODEL_READINESS_STATE_MACHINE`
- `PASS_SEARCH_NO_STALE_MODEL_READY`
- `PASS_SEARCH_MODEL_SELF_CHECK`
- `PASS_SEARCH_MCP_ERROR_CONTRACT`
- `PASS_SEARCH_MCP_NO_SCHEMA_MISMATCH`
- `PASS_SEARCH_CANDIDATE10_DELETION_REGRESSION`
- `PASS_SEARCH_CANDIDATE11_10X_PACKAGED_COLD_START`
- `PASS_SEARCH_CANDIDATE11_5X_FORMAL_COLD_START`
- `PASS_SEARCH_NO_PRODUCTION_DATA_DRIFT`
- `PASS_SEARCH_CANDIDATE11_FORMAL_SWITCH`
- `PASS_SEARCH_NO_UNEXPECTED_C_DRIVE_WRITE`
- `PASS_SEARCH_EXPLICIT_READINESS_LOG_DIR`
- `READY_FOR_USER_BOOK_DELETION`
