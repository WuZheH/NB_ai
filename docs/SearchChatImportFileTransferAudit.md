# Search Chat Import File Transfer Audit

Audit date: 2026-07-24

## Official result

Current OpenAI Apps SDK documentation explicitly supports passing files to an
MCP tool. A tool lists top-level file fields in
`_meta["openai/fileParams"]`. ChatGPT supplies:

- `download_url` (required)
- `file_id` (required)
- `mime_type` (optional value, required schema property)
- `file_name` (optional value, required schema property)

Official reference:
[Apps SDK Reference — Define file inputs](https://developers.openai.com/apps-sdk/reference)

This is a file transport, not PDF text inserted into the model prompt.
Candidate12 therefore implements the direct MCP attachment path instead of
using the Inbox as the only route.

## Implemented proof of concept

The `import_preview` MCP descriptor declares the exact four-property file
schema and `_meta["openai/fileParams"] = ["file"]`. The adapter:

1. accepts only HTTPS URLs without embedded credentials;
2. rejects loopback and literal private-network hosts;
3. allows only PDF MIME;
4. limits declared and streamed size to 200 MB;
5. streams bytes directly to an explicitly configured D-drive Inbox;
6. verifies `%PDF-` magic and computes SHA256;
7. uses a random staged name and never returns the path;
8. passes only that name to the loopback Core;
9. removes the staged attachment after confirmed import or terminal failure.

The automated PoC uses an isolated D-drive temporary directory and a synthetic
PDF response. It proves streaming, hash-preserving staging, Core preview, and
post-confirmation cleanup without touching production.

## Fallback

The same `import_preview` tool can omit `file` and use `inbox_filename`. The
default product Inbox is:

`D:\LEARNING\Tools\search-import-inbox`

Search does not auto-create or scan another directory when the explicit Inbox
is unavailable. Users can place a PDF there and say “导入刚才那个 PDF”.

## Actions boundary

The current official Apps SDK file parameter is an MCP Apps contract. The
Candidate12 Actions adapter keeps the Inbox route; it does not pretend that a
Custom GPT conversation attachment is automatically available to an Action.
