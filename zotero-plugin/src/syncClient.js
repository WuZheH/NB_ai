(function (ns) {
  "use strict";

  var DEFAULT_ENDPOINT =
    "http://127.0.0.1:8000/api/v1/zotero/inspiration-notes/upsert";
  var DEFAULT_STATUS_ENDPOINT =
    "http://127.0.0.1:8000/api/v1/zotero/inspiration-notes/sync-status";
  var DEFAULT_LIST_ENDPOINT_PREFIX =
    "http://127.0.0.1:8000/api/v1/zotero/inspiration-notes/by-attachment/";
  var DEFAULT_MARKDOWN_EXPORT_ENDPOINT =
    "http://127.0.0.1:8000/api/v1/zotero/export-markdown";
  var REQUEST_TIMEOUT_MS = 10000;
  var UPSERT_REQUEST_FIELDS = [
    "client_note_id",
    "source",
    "zotero_item_key",
    "zotero_attachment_key",
    "zotero_annotation_key",
    "pdf_page",
    "page_label",
    "selected_text",
    "selected_text_hash",
    "note_text",
    "user_tags",
    "selection_type",
    "context_before",
    "context_after",
    "bbox",
    "created_at",
    "updated_at",
    "sync_status"
  ];

  function isAllowedLoopbackEndpoint(endpoint) {
    try {
      var url = new URL(endpoint);
      return url.protocol === "http:" &&
        (url.hostname === "127.0.0.1" || url.hostname === "localhost");
    } catch (error) {
      return false;
    }
  }

  class InspirationSyncClient {
    constructor(options) {
      options = options || {};
      this.endpoint = options.endpoint || DEFAULT_ENDPOINT;
      if (!isAllowedLoopbackEndpoint(this.endpoint)) {
        throw new Error("Only a localhost inspiration sync endpoint is allowed.");
      }
      this.statusEndpoint = options.statusEndpoint || DEFAULT_STATUS_ENDPOINT;
      if (!isAllowedLoopbackEndpoint(this.statusEndpoint)) {
        throw new Error("Only a localhost inspiration status endpoint is allowed.");
      }
      this.listEndpointPrefix = options.listEndpointPrefix || DEFAULT_LIST_ENDPOINT_PREFIX;
      if (!isAllowedLoopbackEndpoint(this.listEndpointPrefix)) {
        throw new Error("Only a localhost inspiration list endpoint is allowed.");
      }
      this.markdownExportEndpoint =
        options.markdownExportEndpoint || DEFAULT_MARKDOWN_EXPORT_ENDPOINT;
      if (!isAllowedLoopbackEndpoint(this.markdownExportEndpoint)) {
        throw new Error("Only a localhost Markdown export endpoint is allowed.");
      }
      this.store = options.store || null;
      this.dryRun = options.dryRun !== false;
      this.zotero = options.zotero ||
        (typeof Zotero !== "undefined" ? Zotero : null);
      this.fetchImpl = options.fetchImpl === undefined
        ? (typeof fetch === "function" ? fetch.bind(globalThis) : null)
        : options.fetchImpl;
      this.logger = options.logger || null;
    }

    async upsertNote(note, options) {
      options = options || {};
      var dryRun = options.dryRun === undefined ? this.dryRun : options.dryRun;
      var outboundNote = this._buildOutboundUpsertPayload(note);

      if (dryRun) {
        if (options.logPayload && this.logger &&
            typeof this.logger.log === "function") {
          this.logger.log("[inspiration sync dry-run] " + JSON.stringify(note));
        }
        return {
          status: "DRY_RUN",
          client_note_id: outboundNote.client_note_id,
          client: "none",
          sync_status: outboundNote.sync_status,
          endpoint: this.endpoint,
          http_status: null,
          error: null,
          payload: outboundNote
        };
      }

      var response = await this._requestJSON("POST", this.endpoint, outboundNote);
      if (!response.ok) {
        return this._failed(note, response.error, response);
      }

      var result = response.body;
      if (result.status !== "OK" || result.sync_status !== "synced") {
        return this._failed(
          note,
          "loopback endpoint returned an invalid acknowledgement",
          response
        );
      }
      if (this.store) {
        this.store.markSynced(note.client_note_id, result);
      }
      return Object.assign({}, result, {
        client: response.client,
        endpoint: response.endpoint,
        http_status: response.http_status,
        error: null
      });
    }

    async checkBackendStatus() {
      var response = await this._requestJSON("GET", this.statusEndpoint);
      if (!response.ok) {
        return {
          status: "FAILED",
          client: response.client,
          endpoint: response.endpoint,
          http_status: response.http_status,
          error: response.error
        };
      }
      return Object.assign({}, response.body, {
        client: response.client,
        endpoint: response.endpoint,
        http_status: response.http_status,
        error: null
      });
    }

    async listRemoteNotesByAttachment(attachmentKey) {
      var key = String(attachmentKey === null || attachmentKey === undefined
        ? ""
        : attachmentKey);
      var endpoint = this.listEndpointPrefix + encodeURIComponent(key);
      if (!key) {
        return {
          status: "FAILED",
          attachment_key: null,
          count: 0,
          items: [],
          client: "none",
          endpoint: endpoint,
          http_status: null,
          error: "attachment_key_unavailable"
        };
      }
      var response = await this._requestJSON("GET", endpoint);
      if (!response.ok) {
        return {
          status: "FAILED",
          attachment_key: key,
          count: 0,
          items: [],
          client: response.client,
          endpoint: response.endpoint,
          http_status: response.http_status,
          error: response.error
        };
      }
      var body = response.body || {};
      var items = Array.isArray(body.items) ? body.items : [];
      return {
        status: body.status || "OK",
        attachment_key: key,
        zotero_attachment_key: body.zotero_attachment_key || key,
        count: typeof body.count === "number" ? body.count : items.length,
        items: items,
        client: response.client,
        endpoint: response.endpoint,
        http_status: response.http_status,
        error: null
      };
    }

    async exportMarkdownForAttachment(options) {
      options = options || {};
      var attachmentKey = String(options.attachmentKey === null ||
        options.attachmentKey === undefined ? "" : options.attachmentKey).trim();
      var itemKey = options.itemKey === null || options.itemKey === undefined
        ? null
        : String(options.itemKey).trim();
      var saveToFile = Boolean(options.saveToFile);
      var endpoint = this.markdownExportEndpoint;
      if (!attachmentKey) {
        return {
          status: "FAILED",
          zotero_attachment_key: null,
          markdown: "",
          metadata: null,
          endpoint: endpoint,
          http_status: null,
          client: "none",
          error: "attachment_key_unavailable",
          error_code: "attachment_key_unavailable"
        };
      }
      var response = await this._requestJSON("POST", endpoint, {
        zotero_attachment_key: attachmentKey,
        zotero_item_key: itemKey || null,
        save_to_file: saveToFile
      });
      if (!response.ok) {
        return {
          status: "FAILED",
          zotero_attachment_key: attachmentKey,
          zotero_item_key: itemKey,
          markdown: "",
          metadata: null,
          endpoint: response.endpoint,
          http_status: response.http_status,
          client: response.client,
          error: response.error_code || response.error,
          error_code: response.error_code || null,
          error_message: response.error_message || response.error,
          response_body: response.response_body || null
        };
      }
      return Object.assign({}, response.body, {
        client: response.client,
        endpoint: response.endpoint,
        http_status: response.http_status,
        error: null
      });
    }

    _buildOutboundUpsertPayload(note) {
      var outbound = {};
      for (var field of UPSERT_REQUEST_FIELDS) {
        if (field !== "sync_status") {
          outbound[field] = note[field] === undefined ? null : note[field];
        }
      }
      if (typeof ns.normalizeBboxForBackend === "function") {
        outbound.bbox = ns.normalizeBboxForBackend(note.bbox, note);
      }
      outbound.sync_status = "local_pending";
      return outbound;
    }

    async _requestJSON(method, endpoint, payload) {
      var client = this._selectHTTPClient();
      if (client === "none") {
        return {
          ok: false,
          client: client,
          endpoint: endpoint,
          http_status: null,
          error: "no_supported_http_client: endpoint=" + endpoint + " client=none"
        };
      }

      try {
        var body = payload === undefined ? null : JSON.stringify(payload);
        if (client === "zotero_http") {
          var options = {
            headers: { "Content-Type": "application/json" },
            timeout: REQUEST_TIMEOUT_MS
          };
          if (body !== null) {
            options.body = body;
          }
          var xhr = await this.zotero.HTTP.request(method, endpoint, options);
          return this._decodeJSONResponse(client, endpoint, xhr.status, xhr.responseText);
        }

        var fetchOptions = {
          method: method,
          headers: { "Content-Type": "application/json" }
        };
        if (body !== null) {
          fetchOptions.body = body;
        }
        var fetchResponse = await this.fetchImpl(endpoint, fetchOptions);
        var responseText = await fetchResponse.text();
        return this._decodeJSONResponse(
          client,
          endpoint,
          fetchResponse.status,
          responseText
        );
      } catch (error) {
        var failedResponse = error &&
          (error.xmlhttp || error.xhr || error.response || error);
        if (failedResponse && failedResponse.status !== undefined &&
            Number(failedResponse.status) > 0) {
          return this._decodeJSONResponse(
            client,
            endpoint,
            failedResponse.status,
            failedResponse.responseText || failedResponse.response || ""
          );
        }
        return {
          ok: false,
          client: client,
          endpoint: endpoint,
          http_status: null,
          error: "request failed: endpoint=" + endpoint +
            " client=" + client +
            " likely backend unavailable or blocked localhost request: " +
            String(error),
          error_code: "backend_unavailable",
          error_message: String(error)
        };
      }
    }

    _selectHTTPClient() {
      if (this.zotero && this.zotero.HTTP &&
          typeof this.zotero.HTTP.request === "function") {
        return "zotero_http";
      }
      if (this.fetchImpl) {
        return "fetch";
      }
      return "none";
    }

    _decodeJSONResponse(client, endpoint, status, responseText) {
      var httpStatus = Number(status);
      if (!(httpStatus >= 200 && httpStatus < 300)) {
        var parsedError = this._decodeErrorBody(responseText);
        return {
          ok: false,
          client: client,
          endpoint: endpoint,
          http_status: isNaN(httpStatus) ? null : httpStatus,
          error: "loopback endpoint returned HTTP " + String(status) +
            " endpoint=" + endpoint + " client=" + client +
            " responseText=" + String(responseText || "").slice(0, 500),
          error_code: parsedError.error_code || null,
          error_message: parsedError.error_message || null,
          response_body: parsedError.body || null
        };
      }
      try {
        return {
          ok: true,
          client: client,
          endpoint: endpoint,
          http_status: httpStatus,
          body: JSON.parse(responseText)
        };
      } catch (error) {
        return {
          ok: false,
          client: client,
          endpoint: endpoint,
          http_status: httpStatus,
          error: "loopback endpoint returned invalid JSON endpoint=" + endpoint +
            " client=" + client + ": " + String(error)
        };
      }
    }

    _decodeErrorBody(responseText) {
      try {
        var body = JSON.parse(String(responseText || ""));
        var detail = body && body.detail;
        if (detail && typeof detail === "object") {
          return {
            body: body,
            error_code: detail.error || body.error || null,
            error_message: detail.message || body.message || null
          };
        }
        if (typeof detail === "string") {
          return {
            body: body,
            error_code: detail,
            error_message: detail
          };
        }
        return {
          body: body,
          error_code: body && body.error || null,
          error_message: body && body.message || null
        };
      } catch (error) {
        return {
          body: null,
          error_code: null,
          error_message: null
        };
      }
    }

    _failed(note, error, diagnostics) {
      diagnostics = diagnostics || {};
      var message = String(error || "sync failed");
      if (this.store) {
        this.store.markFailed(note.client_note_id, message);
      }
      return {
        status: "FAILED",
        client_note_id: note.client_note_id,
        client: diagnostics.client || "none",
        endpoint: diagnostics.endpoint || this.endpoint,
        http_status: diagnostics.http_status === undefined
          ? null
          : diagnostics.http_status,
        sync_status: "sync_failed",
        error: message
      };
    }
  }

  ns.DEFAULT_SYNC_ENDPOINT = DEFAULT_ENDPOINT;
  ns.DEFAULT_SYNC_STATUS_ENDPOINT = DEFAULT_STATUS_ENDPOINT;
  ns.DEFAULT_LIST_ENDPOINT_PREFIX = DEFAULT_LIST_ENDPOINT_PREFIX;
  ns.DEFAULT_MARKDOWN_EXPORT_ENDPOINT = DEFAULT_MARKDOWN_EXPORT_ENDPOINT;
  ns.isAllowedLoopbackEndpoint = isAllowedLoopbackEndpoint;
  ns.InspirationSyncClient = InspirationSyncClient;
})(NotebookAIInspiration);
