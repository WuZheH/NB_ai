(function (ns) {
  "use strict";

  var SELECTION_TYPES = [
    "sentence",
    "paragraph",
    "section_title",
    "chapter_title",
    "manual"
  ];
  var REQUIRED_INSPIRATION_NOTE_FIELDS = [
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
  var SHA256_INITIAL = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
  ];
  var SHA256_CONSTANTS = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];

  function rotateRight(value, distance) {
    return (value >>> distance) | (value << (32 - distance));
  }

  function utf8Bytes(value) {
    var encoded = unescape(encodeURIComponent(value));
    var result = [];
    for (var index = 0; index < encoded.length; index += 1) {
      result.push(encoded.charCodeAt(index));
    }
    return result;
  }

  function sha256(value) {
    var bytes = utf8Bytes(value);
    var bitLength = bytes.length * 8;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) {
      bytes.push(0);
    }
    var highLength = Math.floor(bitLength / 0x100000000);
    var lowLength = bitLength >>> 0;
    for (var highShift = 24; highShift >= 0; highShift -= 8) {
      bytes.push((highLength >>> highShift) & 0xff);
    }
    for (var lowShift = 24; lowShift >= 0; lowShift -= 8) {
      bytes.push((lowLength >>> lowShift) & 0xff);
    }

    var hash = SHA256_INITIAL.slice();
    for (var offset = 0; offset < bytes.length; offset += 64) {
      var words = new Array(64);
      for (var word = 0; word < 16; word += 1) {
        var byteOffset = offset + (word * 4);
        words[word] =
          (bytes[byteOffset] << 24) |
          (bytes[byteOffset + 1] << 16) |
          (bytes[byteOffset + 2] << 8) |
          bytes[byteOffset + 3];
      }
      for (var expanded = 16; expanded < 64; expanded += 1) {
        var w15 = words[expanded - 15];
        var w2 = words[expanded - 2];
        var sigma0 = rotateRight(w15, 7) ^ rotateRight(w15, 18) ^ (w15 >>> 3);
        var sigma1 = rotateRight(w2, 17) ^ rotateRight(w2, 19) ^ (w2 >>> 10);
        words[expanded] = (
          words[expanded - 16] + sigma0 + words[expanded - 7] + sigma1
        ) | 0;
      }

      var a = hash[0];
      var b = hash[1];
      var c = hash[2];
      var d = hash[3];
      var e = hash[4];
      var f = hash[5];
      var g = hash[6];
      var h = hash[7];
      for (var round = 0; round < 64; round += 1) {
        var sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
        var choose = (e & f) ^ ((~e) & g);
        var temp1 = (h + sum1 + choose + SHA256_CONSTANTS[round] + words[round]) | 0;
        var sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
        var majority = (a & b) ^ (a & c) ^ (b & c);
        var temp2 = (sum0 + majority) | 0;
        h = g;
        g = f;
        f = e;
        e = (d + temp1) | 0;
        d = c;
        c = b;
        b = a;
        a = (temp1 + temp2) | 0;
      }
      hash[0] = (hash[0] + a) | 0;
      hash[1] = (hash[1] + b) | 0;
      hash[2] = (hash[2] + c) | 0;
      hash[3] = (hash[3] + d) | 0;
      hash[4] = (hash[4] + e) | 0;
      hash[5] = (hash[5] + f) | 0;
      hash[6] = (hash[6] + g) | 0;
      hash[7] = (hash[7] + h) | 0;
    }
    return hash.map(function (word) {
      return ("00000000" + (word >>> 0).toString(16)).slice(-8);
    }).join("");
  }

  function normalizeSelectedTextForHash(selectedText) {
    var value = String(selectedText === null || selectedText === undefined
      ? ""
      : selectedText);
    if (typeof value.normalize === "function") {
      value = value.normalize("NFC");
    }
    return value.replace(/\r\n?/g, "\n").replace(/[ \t]+/g, " ").trim();
  }

  function stableSelectedTextHash(selectedText) {
    return "sha256:" + sha256(normalizeSelectedTextForHash(selectedText));
  }

  function preserveTags(tags) {
    if (Array.isArray(tags)) {
      return tags.map(function (tag) {
        return String(tag);
      });
    }
    if (tags === null || tags === undefined || tags === "") {
      return [];
    }
    return String(tags).split(/\r?\n/);
  }

  function createClientNoteId(now) {
    if (typeof crypto !== "undefined" &&
        typeof crypto.randomUUID === "function") {
      return "zinsp_client_" + crypto.randomUUID();
    }
    return "zinsp_client_" + String(now.getTime()) + "_" +
      Math.random().toString(16).slice(2);
  }

  function nullableValue(value) {
    return value === undefined ? null : value;
  }

  function normalizeBboxForBackend(bbox, context) {
    context = context || {};
    if (bbox === null || bbox === undefined) {
      return null;
    }
    var metadata = {
      format: "zotero_reader_rects_v1",
      source: "zotero_reader_selection",
      pdf_page: nullableValue(context.pdf_page),
      page_label: nullableValue(context.page_label)
    };
    if (Array.isArray(bbox)) {
      metadata.rects = bbox;
      return metadata;
    }
    if (typeof bbox === "object") {
      var normalized = Object.assign({}, bbox);
      for (var key of Object.keys(metadata)) {
        if (normalized[key] === undefined || normalized[key] === null) {
          normalized[key] = metadata[key];
        }
      }
      return normalized;
    }
    return null;
  }

  function buildInspirationNotePayload(context, fields, options) {
    context = context || {};
    fields = fields || {};
    options = options || {};
    var now = options.now || new Date();
    var timestamp = now.toISOString();
    var selectedText = String(context.selected_text === null ||
      context.selected_text === undefined ? "" : context.selected_text);
    var selectionType = SELECTION_TYPES.indexOf(fields.selection_type) !== -1
      ? fields.selection_type
      : "manual";
    if (!selectedText) {
      selectionType = "manual";
    }

    return {
      client_note_id: options.clientNoteId || createClientNoteId(now),
      source: "zotero_plugin",
      zotero_item_key: nullableValue(context.zotero_item_key),
      zotero_attachment_key: nullableValue(context.zotero_attachment_key),
      zotero_annotation_key: nullableValue(context.zotero_annotation_key),
      pdf_page: nullableValue(context.pdf_page),
      page_label: nullableValue(context.page_label),
      selected_text: selectedText,
      selected_text_hash: stableSelectedTextHash(selectedText),
      note_text: String(fields.note_text === null || fields.note_text === undefined
        ? ""
        : fields.note_text),
      user_tags: preserveTags(
        fields.user_tags === undefined ? fields.tags_text : fields.user_tags
      ),
      selection_type: selectionType,
      context_before: nullableValue(context.context_before),
      context_after: nullableValue(context.context_after),
      bbox: normalizeBboxForBackend(context.bbox, context),
      created_at: context.created_at || timestamp,
      updated_at: timestamp,
      sync_status: "local_pending"
    };
  }

  function buildPluginSmokePayload(options) {
    options = options || {};
    var now = options.now || new Date();
    var timestamp = now.toISOString();
    var selectedText = "Phase110K-K plugin smoke selected text";
    return {
      client_note_id: options.clientNoteId ||
        "phase110k_k_plugin_smoke_" + String(now.getTime()),
      source: "zotero_plugin",
      zotero_item_key: "PLUGIN_SMOKE_ITEM",
      zotero_attachment_key: "PLUGIN_SMOKE_ATTACHMENT",
      zotero_annotation_key: null,
      pdf_page: null,
      page_label: null,
      selected_text: selectedText,
      selected_text_hash: stableSelectedTextHash(selectedText),
      note_text: "Phase110K-K plugin smoke note text",
      user_tags: ["__plugin_smoke_test__"],
      selection_type: "manual",
      context_before: null,
      context_after: null,
      bbox: null,
      created_at: timestamp,
      updated_at: timestamp,
      sync_status: "local_pending"
    };
  }

  function createElement(doc, tag, className, text) {
    var element = doc.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (text !== undefined) {
      element.textContent = text;
    }
    return element;
  }

  class InspirationQuickNote {
    constructor(options) {
      options = options || {};
      this.store = options.store;
      this.syncClient = options.syncClient;
      this.onSaved = options.onSaved || null;
      this.documentProvider = options.documentProvider || function () {
        return null;
      };
      this.root = null;
      this.context = null;
      this.status = null;
    }

    open(context, hostDocument) {
      this.close();
      this.context = Object.assign({}, context || {});
      var doc = hostDocument || this.documentProvider();
      if (!doc || !doc.body) {
        return { opened: false, reason: "popup_host_unavailable" };
      }

      var root = createElement(doc, "section", "notebook-ai-inspiration-quick-note");
      root.setAttribute("role", "dialog");
      root.setAttribute("aria-label", "\u8bb0\u4e0b\u7075\u611f");
      root.setAttribute("data-capture-method", this.context.capture_method || "manual_fallback");
      root.style.cssText = [
        "position: fixed",
        "top: 64px",
        "right: 24px",
        "z-index: 2147483647",
        "width: 320px",
        "padding: 12px",
        "background: var(--material-background, #fff)",
        "border: 1px solid #a0a0a0",
        "box-shadow: 0 4px 18px rgba(0, 0, 0, 0.25)",
        "display: flex",
        "flex-direction: column",
        "gap: 6px"
      ].join("; ");

      root.appendChild(createElement(doc, "h3", "", "\u8bb0\u4e0b\u7075\u611f"));
      root.appendChild(createElement(doc, "label", "", "Selected text (read only)"));
      var preview = createElement(doc, "blockquote", "selected-text-preview",
        this.context.selected_text || "");
      root.appendChild(preview);

      root.appendChild(createElement(doc, "label", "", "Inspiration note"));
      var noteText = createElement(doc, "textarea", "note-text");
      noteText.setAttribute("name", "note_text");
      root.appendChild(noteText);

      root.appendChild(createElement(doc, "label", "", "Tags (one per line)"));
      var tagsText = createElement(doc, "textarea", "tags-text");
      tagsText.setAttribute("name", "tags_text");
      tagsText.value = "\u7075\u611f";
      root.appendChild(tagsText);

      root.appendChild(createElement(doc, "label", "", "Selection type"));
      var selectionType = createElement(doc, "select", "selection-type");
      selectionType.setAttribute("name", "selection_type");
      for (var value of SELECTION_TYPES) {
        var option = createElement(doc, "option", "", value);
        option.value = value;
        selectionType.appendChild(option);
      }
      selectionType.value = this.context.selection_type ||
        (this.context.selected_text ? "paragraph" : "manual");
      root.appendChild(selectionType);

      this.status = createElement(doc, "output", "sync-status", "local_pending");
      root.appendChild(this.status);
      var saveButton = createElement(doc, "button", "save-note", "Save");
      var cancelButton = createElement(doc, "button", "cancel-note", "Cancel");
      var self = this;
      saveButton.addEventListener("click", async function () {
        await self.save({
          note_text: noteText.value,
          tags_text: tagsText.value,
          selection_type: selectionType.value
        });
      });
      cancelButton.addEventListener("click", function () {
        self.close();
      });
      root.appendChild(saveButton);
      root.appendChild(cancelButton);

      doc.body.appendChild(root);
      this.root = root;
      return { opened: true, element: root };
    }

    async save(fields) {
      var payload = buildInspirationNotePayload(this.context, fields);
      var saved = this.store.upsertNote(payload);
      this._showStatus(saved.sync_status);
      var result = await this.syncClient.upsertNote(saved);
      this._showStatus(result.sync_status);
      if (this.onSaved) {
        this.onSaved(saved, result);
      }
      return { note: saved, sync: result };
    }

    close() {
      if (this.root && this.root.parentNode) {
        this.root.parentNode.removeChild(this.root);
      }
      this.root = null;
      this.status = null;
    }

    _showStatus(value) {
      if (this.status) {
        this.status.textContent = value;
      }
    }
  }

  ns.SELECTION_TYPES = SELECTION_TYPES;
  ns.REQUIRED_INSPIRATION_NOTE_FIELDS = REQUIRED_INSPIRATION_NOTE_FIELDS;
  ns.normalizeSelectedTextForHash = normalizeSelectedTextForHash;
  ns.stableSelectedTextHash = stableSelectedTextHash;
  ns.normalizeBboxForBackend = normalizeBboxForBackend;
  ns.buildInspirationNotePayload = buildInspirationNotePayload;
  ns.buildPluginSmokePayload = buildPluginSmokePayload;
  ns.InspirationQuickNote = InspirationQuickNote;
})(NotebookAIInspiration);
