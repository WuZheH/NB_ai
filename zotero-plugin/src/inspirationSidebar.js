(function (ns) {
  "use strict";

  var SIDEBAR_TITLE = "Notebook AI Inspirations";
  var HIGHLIGHT_UNAVAILABLE_WARNING = "highlight unavailable";
  var DIALOG_HOST_WAIT_ATTEMPTS = 20;
  var DIALOG_HOST_WAIT_INTERVAL_MS = 50;
  var PANEL_COLORS = {
    background: "#171a20",
    surface: "#20252d",
    surfaceRaised: "#282f39",
    border: "#343c48",
    text: "#eef2f6",
    muted: "#aab4c0",
    accent: "#62a5ff",
    warning: "#e9b949",
    error: "#ff8d8d"
  };

  function anchorStatus(note) {
    if (note.zotero_annotation_key) {
      return "annotation_anchor";
    }
    if (note.pdf_page !== null && note.pdf_page !== undefined) {
      return "page_anchor";
    }
    if (note.selection_type === "manual") {
      return "manual_anchor";
    }
    return "unmatched";
  }

  function groupKeyForNote(note) {
    if (note.pdf_page !== null && note.pdf_page !== undefined) {
      return {
        key: "pdf_page:" + String(note.pdf_page),
        label: note.page_label !== null && note.page_label !== undefined &&
          String(note.page_label) !== ""
          ? "Page " + String(note.page_label) + " / PDF " + String(note.pdf_page)
          : "PDF " + String(note.pdf_page)
      };
    }
    if (note.page_label !== null && note.page_label !== undefined &&
        String(note.page_label) !== "") {
      return {
        key: "page_label:" + String(note.page_label),
        label: "Page " + String(note.page_label)
      };
    }
    return {
      key: "unmatched",
      label: "Unmatched / Manual notes"
    };
  }

  function numericPage(note) {
    var value = Number(note && note.pdf_page);
    return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
  }

  function timestampValue(note) {
    var value = Date.parse(note.created_at || note.updated_at || "");
    return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
  }

  function stableSortNotes(notes) {
    return (notes || []).map(function (note, index) {
      return { note: note, index: index };
    }).sort(function (left, right) {
      var pageOrder = numericPage(left.note) - numericPage(right.note);
      if (pageOrder !== 0) {
        return pageOrder;
      }
      var timestampOrder = timestampValue(left.note) - timestampValue(right.note);
      return timestampOrder !== 0 ? timestampOrder : left.index - right.index;
    }).map(function (entry) {
      return entry.note;
    });
  }

  function groupNotesByPage(notes) {
    var groups = [];
    var indexByKey = {};
    for (var note of stableSortNotes(notes)) {
      var descriptor = groupKeyForNote(note);
      if (indexByKey[descriptor.key] === undefined) {
        indexByKey[descriptor.key] = groups.length;
        groups.push({
          key: descriptor.key,
          title: descriptor.label,
          notes: []
        });
      }
      groups[indexByKey[descriptor.key]].notes.push(note);
    }
    return groups;
  }

  function snippet(value, limit) {
    var text = String(value || "");
    return text.length > limit ? text.slice(0, limit) + "..." : text;
  }

  function element(doc, tag, className, text) {
    var node = doc.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function statusValue(note, field) {
    var value = note[field];
    return value === null || value === undefined || value === ""
      ? "unknown"
      : String(value);
  }

  function displayTags(tags) {
    return (Array.isArray(tags) ? tags : []).map(function (tag) {
      return String(tag).trim();
    }).filter(function (tag) {
      return Boolean(tag);
    });
  }

  function applyStyle(node, rules) {
    node.style.cssText = rules.join("; ");
    return node;
  }

  class InspirationSidebar {
    constructor(options) {
      options = options || {};
      this.store = options.store || null;
      this.fetchNotes = options.fetchNotes || null;
      this.captureSelection = options.captureSelection || null;
      this.navigate = options.navigate || function (note) {
        return Promise.resolve({
          ok: false,
          reason: "reader_navigation_unavailable",
          note_id: note && note.client_note_id
        });
      };
      this.navigateByClientId = options.navigateByClientId || null;
      this.logger = options.logger || null;
      this.root = null;
      this.ownsRoot = false;
      this.ownerWindow = null;
      this.closeOwnerWindow = false;
      this.notesHost = null;
      this.countHost = null;
      this.attachmentHost = null;
      this.stateHost = null;
      this.attachmentKey = null;
      this.lastResponse = null;
      this.selectedClientNoteId = null;
    }

    async open(host, attachmentKey) {
      var descriptor = host && host.document
        ? host
        : { document: host, container: host && host.body, ui_mode: "sidebar" };
      var requestedUiMode = descriptor.ui_mode || "sidebar";
      if (descriptor.ui_mode === "dialog_fallback") {
        descriptor = await this._resolveDialogFallbackHost(descriptor);
        if (!descriptor) {
          descriptor = this._dialogFallbackHost(host);
        }
      }
      var result = await this._openResolvedDescriptor(
        descriptor,
        attachmentKey,
        requestedUiMode,
        null
      );
      if (result.opened && result.ui_mode === "dialog_fallback") {
        var verification = this._verifyDialogMount(descriptor, result.response);
        result.diagnostics = verification;
        if (!verification.dialog_mount_verified) {
          verification.final_ui_mode = "window_fallback";
          var fallbackDescriptor = this._dialogFallbackHost(host);
          this.close();
          if (fallbackDescriptor && fallbackDescriptor.document &&
              fallbackDescriptor.container) {
            return this._openResolvedDescriptor(
              fallbackDescriptor,
              attachmentKey,
              requestedUiMode,
              verification.fallback_reason || "dialog_mount_verification_failed"
            );
          }
          return {
            opened: false,
            reason: "dialog_mount_verification_failed",
            ui_mode: "window_fallback",
            attachment_key: attachmentKey || null,
            count: 0,
            response: result.response || null,
            diagnostics: verification
          };
        }
      }
      return result;
    }

    async _openResolvedDescriptor(descriptor, attachmentKey, requestedUiMode, fallbackReason) {
      descriptor = descriptor || {};
      var hostDocument = descriptor.document;
      var container = descriptor.container ||
        (hostDocument && (hostDocument.body || hostDocument.documentElement));
      if (!hostDocument || !container) {
        return {
          opened: false,
          reason: "sidebar_host_unavailable",
          attachment_key: attachmentKey || null,
          diagnostics: this._buildMountDiagnostics(
            descriptor,
            requestedUiMode,
            null,
            fallbackReason || "sidebar_host_unavailable"
          )
        };
      }
      this.close();
      this.attachmentKey = attachmentKey || null;
      this.root = element(hostDocument, "aside", "notebook-ai-inspiration-sidebar");
      this.ownsRoot = true;
      this.ownerWindow = descriptor.ownerWindow || null;
      this.closeOwnerWindow = Boolean(descriptor.closeOwnerWindow);
      this.root.setAttribute("role", "complementary");
      this.root.setAttribute("aria-label", SIDEBAR_TITLE);
      var dialogMode = descriptor.ui_mode === "dialog_fallback";
      if (dialogMode) {
        this._prepareDialogDocument(hostDocument);
      }
      this.root.style.cssText = [
        "position: fixed",
        dialogMode ? "inset: 0" : "top: 48px",
        dialogMode ? "width: auto" : "right: 0",
        dialogMode ? "height: 100%" : "",
        dialogMode ? "" : "bottom: 0",
        "z-index: 2147483646",
        dialogMode ? "" : "width: 420px",
        dialogMode ? "" : "max-width: calc(100vw - 24px)",
        dialogMode ? "" : "min-width: 360px",
        dialogMode ? "" : "height: calc(100vh - 48px)",
        "overflow-y: auto",
        "box-sizing: border-box",
        "padding: 16px",
        "font-family: system-ui, -apple-system, 'Segoe UI', sans-serif",
        "font-size: 13px",
        "line-height: 1.45",
        "color: " + PANEL_COLORS.text,
        "background: " + PANEL_COLORS.background,
        "border-left: 1px solid " + PANEL_COLORS.border,
        "box-shadow: -2px 0 14px rgba(0, 0, 0, 0.32)"
      ].join("; ");
      this.root.style.zIndex = "2147483646";
      this._renderShell(hostDocument);
      container.appendChild(this.root);
      var result = await this.refresh(this.attachmentKey);
      var diagnostics = this._buildMountDiagnostics(
        descriptor,
        requestedUiMode,
        result,
        fallbackReason
      );
      return {
        opened: true,
        ui_mode: descriptor.ui_mode || "sidebar",
        attachment_key: this.attachmentKey,
        count: result.count || 0,
        response: result,
        diagnostics: diagnostics
      };
    }

    async openInspirationSidebar(hostDocument, attachmentKey) {
      return this.open(hostDocument, attachmentKey);
    }

    close() {
      if (this.ownsRoot && this.root && this.root.parentNode) {
        this.root.parentNode.removeChild(this.root);
      }
      var ownerWindow = this.ownerWindow;
      var closeOwnerWindow = this.closeOwnerWindow;
      this.root = null;
      this.ownsRoot = false;
      this.ownerWindow = null;
      this.closeOwnerWindow = false;
      this.notesHost = null;
      this.countHost = null;
      this.attachmentHost = null;
      this.stateHost = null;
      if (closeOwnerWindow && ownerWindow && !ownerWindow.closed &&
          typeof ownerWindow.close === "function") {
        ownerWindow.close();
      }
    }

    closeInspirationSidebar() {
      this.close();
      return { closed: true };
    }

    isOpen() {
      return Boolean(this.root);
    }

    mount(container, attachmentKey) {
      this.close();
      this.root = container;
      this.ownsRoot = false;
      this.attachmentKey = attachmentKey || null;
      this._renderShell(container.ownerDocument);
      this.refresh(this.attachmentKey);
      return this;
    }

    async refresh(attachmentKey) {
      if (attachmentKey !== undefined) {
        this.attachmentKey = attachmentKey;
      }
      if (this.attachmentHost) {
        this.attachmentHost.textContent = this.attachmentKey || "unknown";
      }
      this._showPanelState("loading", "Loading inspirations...");
      var result;
      if (!this.fetchNotes) {
        result = {
          status: "FAILED",
          attachment_key: this.attachmentKey,
          count: 0,
          items: [],
          error: "remote_notes_client_unavailable"
        };
      } else {
        try {
          result = await this.fetchNotes(this.attachmentKey);
        } catch (error) {
          result = {
            status: "FAILED",
            attachment_key: this.attachmentKey,
            count: 0,
            items: [],
            error: String(error)
          };
        }
      }
      this.lastResponse = result;
      if (this.countHost) {
        this.countHost.textContent = String(result.count || 0);
      }
      if (result.status === "FAILED" || result.error) {
        this.renderNotesGroupedByPage([]);
        this._showPanelState(
          "error",
          "Unable to load inspirations: " + String(result.error || "request failed")
        );
      } else if (!result.count || !(result.items || []).length) {
        this.renderNotesGroupedByPage([]);
        this._showPanelState("empty", "No inspirations for this PDF yet.");
      } else {
        this._showPanelState("ready", "");
        this.renderNotesGroupedByPage(result.items || []);
      }
      return result;
    }

    async refreshInspirationSidebar(attachmentKey) {
      return this.refresh(attachmentKey);
    }

    renderNotesGroupedByPage(notes) {
      var groups = groupNotesByPage(notes);
      if (!this.notesHost) {
        return groups;
      }
      while (this.notesHost.firstChild) {
        this.notesHost.removeChild(this.notesHost.firstChild);
      }
      var doc = this.notesHost.ownerDocument;
      for (var group of groups) {
        var section = element(doc, "section", "inspiration-page-group");
        applyStyle(section, [
          "margin: 14px 0 0",
          "padding: 0"
        ]);
        var heading = element(doc, "h3", "page-group-title", group.title);
        applyStyle(heading, [
          "margin: 0 0 8px",
          "font-size: 12px",
          "font-weight: 600",
          "letter-spacing: 0.04em",
          "text-transform: uppercase",
          "color: " + PANEL_COLORS.muted
        ]);
        section.appendChild(heading);
        for (var note of group.notes) {
          section.appendChild(this.renderNoteCard(note));
        }
        this.notesHost.appendChild(section);
      }
      return groups;
    }

    renderNoteCard(note) {
      var doc = this.notesHost.ownerDocument;
      var card = element(doc, "article", "inspiration-note-card");
      var clientNoteId = note.client_note_id || note.server_note_id || "";
      card.setAttribute("data-client-note-id", clientNoteId);
      applyStyle(card, [
        "box-sizing: border-box",
        "margin: 0 0 10px",
        "padding: 12px",
        "border: 1px solid " + PANEL_COLORS.border,
        "border-radius: 8px",
        "background: " + PANEL_COLORS.surface,
        "cursor: pointer"
      ]);
      var page = element(doc, "p", "note-page", this._locationLabel(note));
      applyStyle(page, [
        "margin: 0 0 6px",
        "font-size: 11px",
        "color: " + PANEL_COLORS.muted
      ]);
      card.appendChild(page);
      var noteText = element(doc, "p", "note-text", String(note.note_text || ""));
      applyStyle(noteText, [
        "margin: 0 0 10px",
        "font-size: 15px",
        "line-height: 1.5",
        "font-weight: 550",
        "white-space: pre-wrap",
        "color: " + PANEL_COLORS.text
      ]);
      card.appendChild(noteText);
      var selectedText = element(doc, "blockquote", "selected-text",
        snippet(note.selected_text, 320));
      applyStyle(selectedText, [
        "max-height: 76px",
        "overflow: hidden",
        "margin: 0 0 10px",
        "padding: 7px 9px",
        "border-left: 2px solid " + PANEL_COLORS.accent,
        "border-radius: 0 5px 5px 0",
        "background: " + PANEL_COLORS.surfaceRaised,
        "font-size: 12px",
        "color: " + PANEL_COLORS.muted
      ]);
      card.appendChild(selectedText);
      var tags = element(doc, "p", "tags", displayTags(note.user_tags).map(function (tag) {
        return "#" + tag;
      }).join("  "));
      applyStyle(tags, [
        "margin: 0 0 8px",
        "font-size: 12px",
        "color: " + PANEL_COLORS.accent
      ]);
      card.appendChild(tags);
      var statuses = [
        "sync: " + statusValue(note, "sync_status"),
        "mechanism: " + statusValue(note, "mechanism_status")
      ];
      if (note.match_status !== undefined && note.match_status !== null) {
        statuses.push("match: " + statusValue(note, "match_status"));
      }
      if (note.review_status !== undefined && note.review_status !== null) {
        statuses.push("review: " + statusValue(note, "review_status"));
      }
      var statusLine = element(doc, "p", "note-statuses", statuses.join(" | "));
      applyStyle(statusLine, [
        "margin: 0 0 10px",
        "font-size: 11px",
        "color: " + PANEL_COLORS.muted
      ]);
      card.appendChild(statusLine);
      card.appendChild(element(doc, "span", "anchor-status", anchorStatus(note)));

      var jumpStatus = element(doc, "output", "jump-status", "");
      var jumpButton = element(doc, "button", "jump-to-note", "Jump to source");
      jumpButton.setAttribute("type", "button");
      applyStyle(jumpButton, [
        "margin-top: 10px",
        "padding: 6px 10px",
        "border: 1px solid " + PANEL_COLORS.accent,
        "border-radius: 6px",
        "color: " + PANEL_COLORS.text,
        "background: #244a78",
        "cursor: pointer"
      ]);
      applyStyle(jumpStatus, [
        "display: block",
        "margin-top: 8px",
        "font-size: 12px",
        "color: " + PANEL_COLORS.muted
      ]);
      var self = this;
      var activateNote = async function () {
        self.selectedClientNoteId = clientNoteId;
        var result = await self.jumpToNoteByClientId(clientNoteId, note);
        var text = result.ok
          ? "Navigated to page " + String(result.pdf_page || note.pdf_page || "?")
          : String(result.reason || "jump unavailable");
        if (result.highlight && !result.highlight.highlighted) {
          text += "; " + HIGHLIGHT_UNAVAILABLE_WARNING;
          jumpStatus.style.color = PANEL_COLORS.warning;
        } else {
          jumpStatus.style.color = result.ok ? PANEL_COLORS.accent : PANEL_COLORS.error;
        }
        jumpStatus.textContent = text;
        card.setAttribute("data-navigation-status", text);
      };
      jumpButton.addEventListener("click", async function (event) {
        event.stopPropagation();
        await activateNote();
      });
      card.addEventListener("click", async function () {
        await activateNote();
      });
      card.appendChild(jumpButton);
      card.appendChild(jumpStatus);
      return card;
    }

    async jumpToNote(note) {
      return this.navigate(note);
    }

    async jumpToNoteByClientId(clientNoteId, note) {
      if (this.navigateByClientId && clientNoteId) {
        return this.navigateByClientId(clientNoteId);
      }
      return this.jumpToNote(note);
    }

    render() {
      var notes = this.store && this.attachmentKey
        ? this.store.listNotesByAttachment(this.attachmentKey)
        : [];
      return this.renderNotesGroupedByPage(notes);
    }

    _renderShell(doc) {
      while (this.root.firstChild) {
        this.root.removeChild(this.root.firstChild);
      }
      var title = element(doc, "h2", "", SIDEBAR_TITLE);
      applyStyle(title, [
        "margin: 0 0 12px",
        "font-size: 20px",
        "font-weight: 600",
        "color: " + PANEL_COLORS.text
      ]);
      this.root.appendChild(title);
      var metadata = element(doc, "p", "sidebar-metadata");
      metadata.appendChild(element(doc, "span", "", "Attachment: "));
      this.attachmentHost = element(doc, "code", "attachment-key",
        this.attachmentKey || "unknown");
      metadata.appendChild(this.attachmentHost);
      metadata.appendChild(element(doc, "span", "", " | Notes: "));
      this.countHost = element(doc, "strong", "notes-count", "0");
      metadata.appendChild(this.countHost);
      applyStyle(metadata, [
        "margin: 0 0 12px",
        "padding: 8px 10px",
        "border-radius: 6px",
        "background: " + PANEL_COLORS.surface,
        "color: " + PANEL_COLORS.muted
      ]);
      this.root.appendChild(metadata);

      var actions = element(doc, "div", "sidebar-actions");
      var refresh = element(doc, "button", "refresh-notes", "Refresh");
      refresh.setAttribute("type", "button");
      var capture = element(
        doc,
        "button",
        "capture-selection",
        "\u8bb0\u4e0b\u5f53\u524d\u9009\u533a"
      );
      capture.setAttribute("type", "button");
      var close = element(doc, "button", "close-sidebar", "Close");
      close.setAttribute("type", "button");
      var self = this;
      refresh.addEventListener("click", function () {
        self.refresh();
      });
      capture.addEventListener("click", async function () {
        if (self.captureSelection) {
          await self.captureSelection();
          await self.refresh();
        }
      });
      close.addEventListener("click", function () {
        self.close();
      });
      actions.appendChild(refresh);
      actions.appendChild(capture);
      actions.appendChild(close);
      applyStyle(actions, [
        "display: flex",
        "gap: 8px",
        "margin-bottom: 10px"
      ]);
      for (var button of [refresh, capture, close]) {
        applyStyle(button, [
          "padding: 6px 9px",
          "border: 1px solid " + PANEL_COLORS.border,
          "border-radius: 6px",
          "color: " + PANEL_COLORS.text,
          "background: " + PANEL_COLORS.surfaceRaised,
          "cursor: pointer"
        ]);
      }
      this.root.appendChild(actions);
      this.stateHost = element(doc, "p", "sidebar-state", "");
      applyStyle(this.stateHost, [
        "display: none",
        "margin: 10px 0",
        "padding: 10px",
        "border-radius: 6px",
        "background: " + PANEL_COLORS.surface,
        "color: " + PANEL_COLORS.muted
      ]);
      this.root.appendChild(this.stateHost);
      this.notesHost = element(doc, "div", "inspiration-notes-list");
      this.root.appendChild(this.notesHost);
    }

    _buildMountDiagnostics(descriptor, requestedUiMode, response, fallbackReason) {
      descriptor = descriptor || {};
      var rootRect = this._getRootRect();
      var rootText = this._getNodeText(this.root);
      var hostDocument = descriptor.document || null;
      var ownerMatches = Boolean(this.root && hostDocument &&
        this.root.ownerDocument === hostDocument);
      return {
        requested_ui_mode: requestedUiMode || descriptor.ui_mode || "sidebar",
        final_ui_mode: descriptor.ui_mode || "sidebar",
        dialog_mount_verified: false,
        mounted_owner: ownerMatches ? "target_document" : "other_document",
        fallback_reason: fallbackReason || null,
        root_text_sample: rootText.slice(0, 240),
        root_rect: rootRect,
        host_available: Boolean(hostDocument &&
          (hostDocument.body || hostDocument.documentElement)),
        response_count: response && response.count ? response.count : 0
      };
    }

    _verifyDialogMount(descriptor, response) {
      var diagnostics = this._buildMountDiagnostics(
        descriptor,
        "dialog_fallback",
        response,
        null
      );
      var ownerWindow = descriptor && descriptor.ownerWindow;
      var hostDocument = descriptor && descriptor.document;
      var bodyOrRoot = hostDocument &&
        (hostDocument.body || hostDocument.documentElement);
      var documentText = this._getNodeText(bodyOrRoot);
      var ownerMatches = Boolean(this.root && hostDocument &&
        this.root.ownerDocument === hostDocument);
      var documentContainsRoot = this._documentContainsNode(bodyOrRoot, this.root);
      var titlePresent = documentText.indexOf(SIDEBAR_TITLE) !== -1;
      var keyElementPresent = this._hasDialogKeyElement(hostDocument);
      var rectVisible = diagnostics.root_rect.width > 0 &&
        diagnostics.root_rect.height > 0;
      var noteTextPresent = this._dialogNoteTextPresent(documentText, response);
      var windowAvailable = Boolean(ownerWindow && !ownerWindow.closed);
      diagnostics.mounted_owner = ownerMatches ? "dialog_document" : "other_document";
      diagnostics.root_text_sample = documentText.slice(0, 240);
      diagnostics.dialog_mount_verified = Boolean(
        windowAvailable &&
        ownerMatches &&
        documentContainsRoot &&
        titlePresent &&
        keyElementPresent &&
        rectVisible &&
        noteTextPresent
      );
      if (!diagnostics.dialog_mount_verified) {
        diagnostics.fallback_reason = this._dialogMountFailureReason({
          windowAvailable: windowAvailable,
          ownerMatches: ownerMatches,
          documentContainsRoot: documentContainsRoot,
          titlePresent: titlePresent,
          keyElementPresent: keyElementPresent,
          rectVisible: rectVisible,
          noteTextPresent: noteTextPresent
        });
      }
      return diagnostics;
    }

    _dialogMountFailureReason(checks) {
      if (!checks.windowAvailable) {
        return "dialog_window_unavailable";
      }
      if (!checks.ownerMatches) {
        return "dialog_owner_document_mismatch";
      }
      if (!checks.documentContainsRoot) {
        return "dialog_document_missing_root";
      }
      if (!checks.titlePresent) {
        return "dialog_document_missing_title";
      }
      if (!checks.keyElementPresent) {
        return "dialog_document_missing_key_elements";
      }
      if (!checks.rectVisible) {
        return "dialog_root_not_visible";
      }
      if (!checks.noteTextPresent) {
        return "dialog_document_missing_note_text";
      }
      return "dialog_mount_verification_failed";
    }

    _hasDialogKeyElement(doc) {
      if (!doc || typeof doc.querySelector !== "function") {
        return false;
      }
      return Boolean(
        doc.querySelector(".inspiration-note-card") ||
        doc.querySelector(".refresh-notes") ||
        doc.querySelector(".jump-to-note")
      );
    }

    _dialogNoteTextPresent(text, response) {
      var items = response && Array.isArray(response.items) ? response.items : [];
      if (!items.length) {
        return text.indexOf(SIDEBAR_TITLE) !== -1;
      }
      for (var note of items) {
        var noteText = String(note.note_text || "");
        var location = this._locationLabel(note);
        if ((noteText && text.indexOf(noteText) !== -1) ||
            (location && text.indexOf(location) !== -1)) {
          return true;
        }
      }
      return false;
    }

    _documentContainsNode(container, node) {
      try {
        return Boolean(container && node && typeof container.contains === "function" &&
          container.contains(node));
      } catch (error) {
        return false;
      }
    }

    _getNodeText(node) {
      if (!node) {
        return "";
      }
      return String(node.innerText || node.textContent || "");
    }

    _getRootRect() {
      var rect = { width: 0, height: 0 };
      if (!this.root) {
        return rect;
      }
      try {
        if (typeof this.root.getBoundingClientRect === "function") {
          var measured = this.root.getBoundingClientRect();
          rect.width = Number(measured && measured.width) || 0;
          rect.height = Number(measured && measured.height) || 0;
        }
      } catch (error) {
        rect.width = 0;
        rect.height = 0;
      }
      if (!rect.width && this.root.offsetWidth) {
        rect.width = Number(this.root.offsetWidth) || 0;
      }
      if (!rect.height && this.root.offsetHeight) {
        rect.height = Number(this.root.offsetHeight) || 0;
      }
      return rect;
    }

    _prepareDialogDocument(doc) {
      if (doc.documentElement) {
        doc.documentElement.style.cssText =
          "margin: 0; padding: 0; width: 100%; height: 100%; background: " +
          PANEL_COLORS.background;
      }
      if (doc.body) {
        doc.body.style.cssText =
          "margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: " +
          PANEL_COLORS.background;
      }
    }

    async _resolveDialogFallbackHost(descriptor) {
      var ownerWindow = descriptor.ownerWindow || null;
      var hostDocument = descriptor.document || (ownerWindow && ownerWindow.document);
      for (var attempt = 0; attempt < DIALOG_HOST_WAIT_ATTEMPTS; attempt += 1) {
        hostDocument = ownerWindow && ownerWindow.document
          ? ownerWindow.document
          : hostDocument;
        if (hostDocument && (hostDocument.body || hostDocument.documentElement)) {
          descriptor.document = hostDocument;
          descriptor.container = hostDocument.body || hostDocument.documentElement;
          return descriptor;
        }
        await this._waitForDialogDocumentReady(ownerWindow, hostDocument);
      }
      this._closeDialogFallbackWindow(descriptor);
      return null;
    }

    _dialogFallbackHost(host) {
      var fallbackHost = host && (host.fallbackHost || host.fallback_host);
      if (!fallbackHost) {
        return {
          document: null,
          container: null,
          ui_mode: "window_fallback"
        };
      }
      fallbackHost.ui_mode = "window_fallback";
      fallbackHost.closeOwnerWindow = false;
      return fallbackHost;
    }

    _waitForDialogDocumentReady(ownerWindow, doc) {
      return new Promise(function (resolve) {
        var settled = false;
        var finish = function () {
          if (!settled) {
            settled = true;
            resolve();
          }
        };
        try {
          if (doc && typeof doc.addEventListener === "function") {
            doc.addEventListener("DOMContentLoaded", finish, { once: true });
          }
          if (ownerWindow && typeof ownerWindow.addEventListener === "function") {
            ownerWindow.addEventListener("load", finish, { once: true });
          }
        } catch (error) {
          finish();
          return;
        }
        setTimeout(finish, DIALOG_HOST_WAIT_INTERVAL_MS);
      });
    }

    _closeDialogFallbackWindow(descriptor) {
      var ownerWindow = descriptor && descriptor.ownerWindow;
      if (ownerWindow && descriptor.closeOwnerWindow && !ownerWindow.closed &&
          typeof ownerWindow.close === "function") {
        ownerWindow.close();
      }
    }

    _showPanelState(state, message) {
      if (!this.stateHost) {
        return;
      }
      this.stateHost.setAttribute("data-state", state);
      this.stateHost.textContent = message;
      this.stateHost.style.display = message ? "block" : "none";
      this.stateHost.style.color = state === "error"
        ? PANEL_COLORS.error
        : PANEL_COLORS.muted;
    }

    _locationLabel(note) {
      if (note.page_label !== null && note.page_label !== undefined &&
          note.pdf_page !== null && note.pdf_page !== undefined) {
        return "Page " + String(note.page_label) + " / PDF " + String(note.pdf_page);
      }
      if (note.page_label !== null && note.page_label !== undefined) {
        return "Page " + String(note.page_label);
      }
      if (note.pdf_page !== null && note.pdf_page !== undefined) {
        return "PDF " + String(note.pdf_page);
      }
      return "Unknown page";
    }
  }

  ns.anchorStatus = anchorStatus;
  ns.groupNotes = groupNotesByPage;
  ns.groupNotesByPage = groupNotesByPage;
  ns.stableSortNotes = stableSortNotes;
  ns.displayTags = displayTags;
  ns.InspirationSidebar = InspirationSidebar;
})(NotebookAIInspiration);
