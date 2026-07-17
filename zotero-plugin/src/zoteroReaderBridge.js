(function (ns) {
  "use strict";

  var ACTION_LABEL = "\u8bb0\u4e0b\u7075\u611f";
  var PLUGIN_ID = "notebook-ai-inspiration@notebook-ai.local";

  function createLogger(zotero) {
    return {
      log: function (message) {
        if (zotero && typeof zotero.debug === "function") {
          zotero.debug("[Search Inspiration] " + message);
        }
      },
      warn: function (message) {
        if (zotero && typeof zotero.debug === "function") {
          zotero.debug("[Search Inspiration warning] " + message);
        }
      }
    };
  }

  function captureContextTemplate() {
    return {
      zotero_item_key: null,
      zotero_attachment_key: null,
      zotero_annotation_key: null,
      pdf_page: null,
      page_label: null,
      selected_text: "",
      context_before: null,
      context_after: null,
      bbox: null,
      anchor_status: "manual_anchor",
      capture_method: "manual_fallback",
      warnings: []
    };
  }

  function parsePosition(position) {
    if (!position) {
      return null;
    }
    if (typeof position === "string") {
      try {
        return JSON.parse(position);
      } catch (error) {
        return null;
      }
    }
    return position;
  }

  function firstDefined() {
    for (var index = 0; index < arguments.length; index += 1) {
      if (arguments[index] !== undefined && arguments[index] !== null) {
        return arguments[index];
      }
    }
    return null;
  }

  class ZoteroReaderBridge {
    constructor(options) {
      options = options || {};
      this.zotero = options.zotero || null;
      this.services = options.services ||
        (typeof Services !== "undefined" ? Services : null);
      this.adapter = options.adapter || null;
      this.logger = options.logger || createLogger(this.zotero);
      this.disposers = [];
      this.readerListeners = [];
      this.latestReaderContext = null;
      this.lastNavigationReader = null;
    }

    registerEntryPoints(actions) {
      actions = actions || {};
      return {
        reader: this._registerReaderActions(actions),
        toolbar: this._register(
          "registerToolbarAction",
          [ACTION_LABEL, actions.openQuickNote]
        ),
        selection_context: this._register(
          "registerSelectionAction",
          [ACTION_LABEL, actions.openQuickNote]
        ),
        sidebar: this._register("mountSidebar", [actions.mountSidebar])
      };
    }

    getActiveReaderContext(candidate) {
      if (candidate) {
        this.latestReaderContext = candidate;
        return candidate;
      }
      if (this.adapter && typeof this.adapter.getActiveReaderContext === "function") {
        try {
          var adapted = this.adapter.getActiveReaderContext();
          if (adapted) {
            this.latestReaderContext = adapted;
            return adapted;
          }
        } catch (error) {
          this.logger.warn("Adapter Reader context unavailable: " + String(error));
        }
      }
      if (this.latestReaderContext) {
        return this.latestReaderContext;
      }
      var reader = this._resolveActiveReader();
      return {
        reader: reader,
        params: {},
        document: null,
        event_type: reader ? "active_reader_probe" : "manual_fallback",
        capture_method_hint: null
      };
    }

    getCurrentItemKey(readerContext) {
      try {
        var params = readerContext.params || {};
        var attachment = this._getAttachmentItem(readerContext);
        var parent = attachment && typeof attachment.parentItem === "object"
          ? attachment.parentItem
          : null;
        return firstDefined(
          params.zotero_item_key,
          params.itemKey,
          parent && parent.key,
          attachment && attachment.parentItemKey,
          this._parentItemKeyFromAttachment(attachment)
        );
      } catch (error) {
        this.logger.warn("Item key capture failed: " + String(error));
        return null;
      }
    }

    getCurrentAttachmentKey(readerContext) {
      try {
        var params = readerContext.params || {};
        var attachment = this._getAttachmentItem(readerContext);
        return firstDefined(
          params.zotero_attachment_key,
          params.attachmentKey,
          attachment && attachment.key
        );
      } catch (error) {
        this.logger.warn("Attachment key capture failed: " + String(error));
        return null;
      }
    }

    getSelectedText(readerContext) {
      try {
        var params = readerContext.params || {};
        var annotation = this._getAnnotation(readerContext);
        var value = firstDefined(
          params.selected_text,
          params.selectedText,
          annotation && annotation.text,
          annotation && annotation.annotationText
        );
        if (value !== null) {
          return String(value);
        }
        var frameWindow = readerContext.reader && readerContext.reader._iframeWindow;
        if (frameWindow && typeof frameWindow.getSelection === "function") {
          return String(frameWindow.getSelection().toString());
        }
      } catch (error) {
        this.logger.warn("Selected text capture failed: " + String(error));
      }
      return "";
    }

    getCurrentAnnotationKey(readerContext) {
      try {
        var params = readerContext.params || {};
        var annotation = this._getAnnotation(readerContext);
        return firstDefined(
          params.zotero_annotation_key,
          params.annotationKey,
          annotation && annotation.key,
          annotation && annotation.annotationKey
        );
      } catch (error) {
        this.logger.warn("Annotation key capture failed: " + String(error));
        return null;
      }
    }

    getCurrentPageInfo(readerContext) {
      try {
        var params = readerContext.params || {};
        var annotation = this._getAnnotation(readerContext);
        var position = parsePosition(firstDefined(
          params.position,
          annotation && annotation.position,
          annotation && annotation.annotationPosition
        )) || {};
        var pageIndex = firstDefined(params.pageIndex, position.pageIndex);
        var pdfPage = firstDefined(params.pdf_page, params.pdfPage);
        if (pdfPage === null && pageIndex !== null) {
          pdfPage = Number(pageIndex) + 1;
        }
        return {
          pdf_page: pdfPage,
          page_label: firstDefined(
            params.page_label,
            params.pageLabel,
            annotation && annotation.pageLabel,
            annotation && annotation.annotationPageLabel
          )
        };
      } catch (error) {
        this.logger.warn("Page capture failed: " + String(error));
        return { pdf_page: null, page_label: null };
      }
    }

    getCurrentBBoxOrPosition(readerContext) {
      try {
        var params = readerContext.params || {};
        var annotation = this._getAnnotation(readerContext);
        var position = parsePosition(firstDefined(
          params.position,
          annotation && annotation.position,
          annotation && annotation.annotationPosition
        ));
        return firstDefined(
          params.bbox,
          params.rects,
          position && position.rects,
          position && position.bbox
        );
      } catch (error) {
        this.logger.warn("Position capture failed: " + String(error));
        return null;
      }
    }

    buildCaptureContext(readerContext) {
      readerContext = this.getActiveReaderContext(readerContext);
      var context = captureContextTemplate();
      var warnings = context.warnings;
      context.zotero_item_key = this.getCurrentItemKey(readerContext);
      context.zotero_attachment_key = this.getCurrentAttachmentKey(readerContext);
      context.zotero_annotation_key = this.getCurrentAnnotationKey(readerContext);
      context.selected_text = this.getSelectedText(readerContext);
      var pageInfo = this.getCurrentPageInfo(readerContext);
      context.pdf_page = pageInfo.pdf_page;
      context.page_label = pageInfo.page_label;
      context.bbox = this.getCurrentBBoxOrPosition(readerContext);

      if (!context.zotero_attachment_key) {
        warnings.push("attachment_key_unavailable");
      }
      if (!context.zotero_item_key) {
        warnings.push("item_key_unavailable");
      }
      if (context.selected_text) {
        context.capture_method = readerContext.capture_method_hint || "reader_selection";
        context.anchor_status = context.capture_method === "reader_annotation"
          ? "annotation_anchor"
          : "selection_anchor";
      } else if (context.zotero_annotation_key) {
        context.capture_method = "reader_annotation";
        context.anchor_status = "annotation_anchor";
        warnings.push("annotation_text_unavailable");
      } else if (context.pdf_page !== null) {
        context.capture_method = "reader_page";
        context.anchor_status = "page_anchor";
        warnings.push("selected_text_unavailable");
      } else {
        context.capture_method = "manual_fallback";
        context.anchor_status = "manual_anchor";
        warnings.push("selected_text_unavailable_manual_note_allowed");
      }
      if (!context.bbox) {
        warnings.push("bbox_unavailable");
      }
      context.selection_type = context.selected_text ? "paragraph" : "manual";
      this.logger.log("capture capability result " + JSON.stringify({
        capture_method: context.capture_method,
        anchor_status: context.anchor_status,
        has_item_key: Boolean(context.zotero_item_key),
        has_attachment_key: Boolean(context.zotero_attachment_key),
        has_annotation_key: Boolean(context.zotero_annotation_key),
        has_selected_text: Boolean(context.selected_text),
        has_bbox: Boolean(context.bbox),
        warnings: warnings
      }));
      return context;
    }

    captureFromCurrentSelection() {
      return this.buildCaptureContext(this.getActiveReaderContext());
    }

    captureFromCurrentAnnotation(readerContext) {
      readerContext = this.getActiveReaderContext(readerContext);
      readerContext.capture_method_hint = "reader_annotation";
      return this.buildCaptureContext(readerContext);
    }

    captureManualFallback() {
      var context = captureContextTemplate();
      context.selection_type = "manual";
      context.warnings.push("manual_capture_requested");
      return context;
    }

    getPopupDocument(preferredDocument) {
      if (preferredDocument && preferredDocument.body) {
        return preferredDocument;
      }
      if (this.adapter && typeof this.adapter.getPopupDocument === "function") {
        try {
          var adapted = this.adapter.getPopupDocument();
          if (adapted && adapted.body) {
            return adapted;
          }
        } catch (error) {
          this.logger.warn("Popup host unavailable: " + String(error));
        }
      }
      try {
        var mainWindow = this.zotero && typeof this.zotero.getMainWindow === "function"
          ? this.zotero.getMainWindow()
          : null;
        return mainWindow && mainWindow.document ? mainWindow.document : null;
      } catch (error) {
        this.logger.warn("Main window popup fallback unavailable: " + String(error));
        return null;
      }
    }

    async getSidebarHost(preferredDocument) {
      if (this.adapter && typeof this.adapter.getSidebarHost === "function") {
        try {
          var adapted = await this.adapter.getSidebarHost();
          if (adapted) {
            return adapted;
          }
        } catch (error) {
          this.logger.warn("Sidebar adapter host unavailable: " + String(error));
        }
      }
      var mainDocument = this.getPopupDocument(preferredDocument);
      var windowFallbackHost = mainDocument && (mainDocument.body || mainDocument.documentElement)
        ? {
          document: mainDocument,
          container: mainDocument.body || mainDocument.documentElement,
          ui_mode: "window_fallback"
        }
        : null;
      var dialogHost = await this._openSidebarDialogFallback(windowFallbackHost);
      if (dialogHost) {
        return dialogHost;
      }
      if (windowFallbackHost) {
        this.logger.warn("Standalone sidebar dialog unavailable; using main window root");
        return windowFallbackHost;
      }
      return null;
    }

    async _openSidebarDialogFallback() {
      var fallbackHost = arguments[0] || null;
      var mainWindow = this._getMainWindow();
      var dialog = null;
      try {
        if (mainWindow && typeof mainWindow.openDialog === "function") {
          dialog = mainWindow.openDialog(
            "about:blank",
            "notebook-ai-inspiration-notes",
            "chrome,dialog=no,resizable,centerscreen,width=460,height=720"
          );
        } else if (this.services && this.services.ww &&
            typeof this.services.ww.openWindow === "function") {
          dialog = this.services.ww.openWindow(
            mainWindow || null,
            "about:blank",
            "notebook-ai-inspiration-notes",
            "chrome,dialog=no,resizable,centerscreen,width=460,height=720",
            null
          );
        }
        var document = await this._waitForSidebarWindowDocument(dialog);
        if (!document) {
          if (dialog && typeof dialog.close === "function") {
            dialog.close();
          }
          return null;
        }
        this.logger.log("Sidebar dialog fallback opened");
        return {
          document: document,
          container: document.body || document.documentElement,
          ownerWindow: dialog,
          closeOwnerWindow: true,
          fallbackHost: fallbackHost || null,
          ui_mode: "dialog_fallback"
        };
      } catch (error) {
        this.logger.warn("Sidebar dialog fallback unavailable: " + String(error));
        return null;
      }
    }

    async _waitForSidebarWindowDocument(windowObject) {
      if (!windowObject) {
        return null;
      }
      for (var attempt = 0; attempt < 20; attempt += 1) {
        var document = windowObject.document;
        if (document && (document.body || document.documentElement) &&
            document.readyState !== "loading") {
          return document;
        }
        await new Promise(function (resolve) {
          var settled = false;
          var finish = function () {
            if (!settled) {
              settled = true;
              resolve();
            }
          };
          try {
            if (document && typeof document.addEventListener === "function") {
              document.addEventListener("DOMContentLoaded", finish, { once: true });
            }
            windowObject.addEventListener("load", finish, { once: true });
          } catch (error) {
            finish();
            return;
          }
          setTimeout(finish, 50);
        });
      }
      return null;
    }

    openQuickNoteFromCurrentSelection(quickNote) {
      var context = this.captureFromCurrentSelection();
      return quickNote.open(context, this.getPopupDocument());
    }

    openQuickNoteForContext(quickNote, context, document) {
      return quickNote.open(context, this.getPopupDocument(document));
    }

    async jumpToPage(pdfPage, note) {
      var noteId = note && (note.client_note_id || note.server_note_id) || null;
      var warnings = [];
      var pageNumber = Number(firstDefined(pdfPage, note && note.page_label));
      if (!Number.isFinite(pageNumber) || pageNumber < 1) {
        return {
          ok: false,
          reason: "pdf_page_unavailable",
          note_id: noteId,
          pdf_page: null,
          warnings: warnings
        };
      }
      var pageIndex = Math.floor(pageNumber) - 1;
      var resolved = await this._resolveReaderForNote(note, pageIndex);
      var reader = resolved.reader;
      warnings = warnings.concat(resolved.warnings);
      if (!reader) {
        return {
          ok: false,
          reason: "reader_navigation_unavailable",
          note_id: noteId,
          pdf_page: pageNumber,
          page_label: note && note.page_label || null,
          warnings: warnings
        };
      }
      var attempts = [
        { target: reader, method: "navigate", args: [{ pageIndex: pageIndex }] },
        { target: this.adapter, method: "navigateToPage", args: [pageNumber, note] },
        { target: reader, method: "navigateToPage", args: [pageNumber] },
        { target: reader, method: "setPage", args: [pageNumber] },
        { target: reader, method: "openPage", args: [pageNumber] }
      ];
      for (var attempt of attempts) {
        if (!attempt.target || typeof attempt.target[attempt.method] !== "function") {
          continue;
        }
        try {
          var result = await attempt.target[attempt.method].apply(
            attempt.target,
            attempt.args
          );
          if (result && result.ok === false) {
            warnings.push(attempt.method + "_reported_failure");
            continue;
          }
          this.logger.log("Reader page navigation used " + attempt.method);
          this.lastNavigationReader = reader;
          return {
            ok: true,
            status: "page_navigated",
            navigation_method: attempt.method,
            reader_source: resolved.source,
            note_id: noteId,
            pdf_page: pageNumber,
            page_index: pageIndex,
            page_label: note && note.page_label || null,
            warnings: warnings
          };
        } catch (error) {
          warnings.push(attempt.method + "_failed");
          this.logger.warn("Page navigation failed: " + String(error));
        }
      }
      return {
        ok: false,
        reason: "reader_navigation_unavailable",
        note_id: noteId,
        pdf_page: pageNumber,
        page_index: pageIndex,
        page_label: note && note.page_label || null,
        warnings: warnings
      };
    }

    async jumpToBBox(note) {
      var noteId = note && (note.client_note_id || note.server_note_id) || null;
      var bbox = note && note.bbox;
      if (!bbox || typeof bbox !== "object" ||
          !Array.isArray(bbox.rects) || !bbox.rects.length) {
        return {
          ok: false,
          highlighted: false,
          reason: "bbox_unavailable",
          note_id: noteId,
          warning: "bbox_highlight_unavailable"
        };
      }
      var reader = this.lastNavigationReader ||
        this._findOpenReaderForAttachment(note && note.zotero_attachment_key) ||
        this._resolveActiveReader();
      var attempts = [
        { target: this.adapter, method: "highlightBBox", args: [note] },
        { target: reader, method: "highlightRects", args: [bbox.rects, note.pdf_page] },
        { target: reader, method: "setTemporaryHighlight", args: [bbox, note] }
      ];
      for (var attempt of attempts) {
        if (!attempt.target || typeof attempt.target[attempt.method] !== "function") {
          continue;
        }
        try {
          var result = await attempt.target[attempt.method].apply(
            attempt.target,
            attempt.args
          );
          if (result && result.ok === false) {
            continue;
          }
          this.logger.log("Reader bbox highlight used " + attempt.method);
          return {
            ok: true,
            highlighted: true,
            highlight_method: attempt.method,
            note_id: noteId
          };
        } catch (error) {
          this.logger.warn("BBox highlight failed: " + String(error));
        }
      }
      return {
        ok: false,
        highlighted: false,
        reason: "reader_highlight_unavailable",
        note_id: noteId,
        warning: "bbox_highlight_unavailable"
      };
    }

    async attemptHighlightNoteBBox(note) {
      return this.jumpToBBox(note);
    }

    async jumpToNote(note) {
      note = note || {};
      var navigation = await this.jumpToPage(note.pdf_page, note);
      var highlight = await this.attemptHighlightNoteBBox(note);
      var warnings = (navigation.warnings || []).slice();
      if (!highlight.highlighted && highlight.warning) {
        warnings.push(highlight.warning);
      }
      if (!navigation.ok) {
        return {
          ok: false,
          reason: navigation.reason || "reader_navigation_unavailable",
          note_id: navigation.note_id,
          pdf_page: navigation.pdf_page,
          highlight: highlight,
          warnings: warnings
        };
      }
      return {
        ok: true,
        status: "page_navigated",
        navigation_method: navigation.navigation_method,
        note_id: navigation.note_id,
        pdf_page: navigation.pdf_page,
        highlighted: highlight.highlighted,
        highlight: highlight,
        warnings: warnings
      };
    }

    async navigateToNote(note) {
      return this.jumpToNote(note);
    }

    async _resolveReaderForNote(note, pageIndex) {
      note = note || {};
      var attachmentKey = note.zotero_attachment_key || null;
      var warnings = [];
      var candidates = [
        {
          reader: this.latestReaderContext && this.latestReaderContext.reader,
          source: "active_reader"
        },
        { reader: this._resolveSelectedTabReader(), source: "selected_tab_reader" },
        {
          reader: this._findOpenReaderForAttachment(attachmentKey),
          source: "attachment_reader"
        }
      ];
      var seen = [];
      for (var candidate of candidates) {
        if (!candidate.reader || seen.indexOf(candidate.reader) !== -1) {
          continue;
        }
        seen.push(candidate.reader);
        if (!attachmentKey || this._readerMatchesAttachment(candidate.reader, attachmentKey)) {
          return {
            reader: candidate.reader,
            source: candidate.source,
            warnings: warnings
          };
        }
        warnings.push(candidate.source + "_attachment_mismatch");
      }
      var opened = await this._openAttachmentReader(attachmentKey, pageIndex);
      if (opened.reader) {
        return {
          reader: opened.reader,
          source: opened.source,
          warnings: warnings.concat(opened.warnings)
        };
      }
      return {
        reader: null,
        source: null,
        warnings: warnings.concat(opened.warnings)
      };
    }

    async _openAttachmentReader(attachmentKey, pageIndex) {
      var warnings = [];
      var attachment = this._findAttachmentItem(attachmentKey);
      if (!attachment) {
        warnings.push("attachment_item_unavailable");
        return { reader: null, source: null, warnings: warnings };
      }
      if (!this.zotero || !this.zotero.Reader ||
          typeof this.zotero.Reader.open !== "function") {
        warnings.push("reader_open_unavailable");
        return { reader: null, source: null, warnings: warnings };
      }
      var itemID = firstDefined(attachment.id, attachment.itemID);
      if (itemID === null) {
        warnings.push("attachment_item_id_unavailable");
        return { reader: null, source: null, warnings: warnings };
      }
      try {
        await this.zotero.Reader.open(itemID);
        await new Promise(function (resolve) {
          setTimeout(resolve, 250);
        });
        var reader = this._findOpenReaderForAttachment(attachmentKey) ||
          this._resolveSelectedTabReader();
        if (reader && this._readerMatchesAttachment(reader, attachmentKey)) {
          this.logger.log(
            "Reader opened for attachment before navigation pageIndex=" +
            String(pageIndex)
          );
          return {
            reader: reader,
            source: "opened_attachment_reader",
            warnings: warnings
          };
        }
        warnings.push("opened_reader_not_resolved");
      } catch (error) {
        warnings.push("reader_open_failed");
        this.logger.warn("Reader open fallback failed: " + String(error));
      }
      return { reader: null, source: null, warnings: warnings };
    }

    shutdown() {
      for (var registration of this.readerListeners) {
        try {
          this.zotero.Reader.unregisterEventListener(
            registration.type,
            registration.handler
          );
        } catch (error) {
          this.logger.warn("Reader listener cleanup failed: " + String(error));
        }
      }
      this.readerListeners = [];
      for (var dispose of this.disposers.reverse()) {
        try {
          dispose();
        } catch (error) {
          this.logger.warn("Reader disposer failed: " + String(error));
        }
      }
      this.disposers = [];
      this.latestReaderContext = null;
      this.lastNavigationReader = null;
    }

    _registerReaderActions(actions) {
      if (!this.zotero || !this.zotero.Reader ||
          typeof this.zotero.Reader.registerEventListener !== "function") {
        this.logger.warn("Reader action registration unknown: Zotero.Reader API unavailable");
        return { status: "unknown", registered: false, reason: "reader_api_unavailable" };
      }
      var self = this;
      try {
        var selectionHandler = function (event) {
          try {
            var readerContext = self._contextFromEvent(
              event,
              "renderTextSelectionPopup",
              "reader_selection"
            );
            self.latestReaderContext = readerContext;
            var button = event.doc.createElement("button");
            button.textContent = ACTION_LABEL;
            button.setAttribute("type", "button");
            button.addEventListener("click", function () {
              actions.openQuickNote(
                self.buildCaptureContext(readerContext),
                self.getPopupDocument()
              );
            });
            event.append(button);
          } catch (error) {
            self.logger.warn("Selection popup action failed: " + String(error));
          }
        };
        var annotationHandler = function (event) {
          try {
            var readerContext = self._contextFromEvent(
              event,
              "createAnnotationContextMenu",
              "reader_annotation"
            );
            self.latestReaderContext = readerContext;
            event.append({
              label: ACTION_LABEL,
              onCommand: function () {
                actions.openQuickNote(
                  self.captureFromCurrentAnnotation(readerContext),
                  self.getPopupDocument()
                );
              }
            });
          } catch (error) {
            self.logger.warn("Annotation context action failed: " + String(error));
          }
        };
        this.zotero.Reader.registerEventListener(
          "renderTextSelectionPopup",
          selectionHandler,
          PLUGIN_ID
        );
        this.readerListeners.push({
          type: "renderTextSelectionPopup",
          handler: selectionHandler
        });
        this.zotero.Reader.registerEventListener(
          "createAnnotationContextMenu",
          annotationHandler,
          PLUGIN_ID
        );
        this.readerListeners.push({
          type: "createAnnotationContextMenu",
          handler: annotationHandler
        });
        this.logger.log("Reader capture actions registered");
        return { status: "registered", registered: true };
      } catch (error) {
        this.logger.warn("Reader action registration failed: " + String(error));
        return { status: "unknown", registered: false, reason: String(error) };
      }
    }

    _contextFromEvent(event, eventType, captureMethod) {
      return {
        reader: event.reader || null,
        params: event.params || {},
        document: event.doc || null,
        event_type: eventType,
        capture_method_hint: captureMethod
      };
    }

    _resolveActiveReader() {
      try {
        if (this.latestReaderContext && this.latestReaderContext.reader) {
          return this.latestReaderContext.reader;
        }
        if (this.adapter && typeof this.adapter.getActiveReader === "function") {
          return this.adapter.getActiveReader();
        }
        return this._resolveSelectedTabReader();
      } catch (error) {
        this.logger.warn("Active Reader probe failed: " + String(error));
      }
      return null;
    }

    _getMainWindow() {
      if (this.zotero && typeof this.zotero.getMainWindow === "function") {
        return this.zotero.getMainWindow();
      }
      if (this.services && this.services.wm &&
          typeof this.services.wm.getMostRecentWindow === "function") {
        return this.services.wm.getMostRecentWindow("navigator:browser") ||
          this.services.wm.getMostRecentWindow(null);
      }
      return null;
    }

    _resolveSelectedTabReader() {
      try {
        var mainWindow = this._getMainWindow();
        var tabs = typeof Zotero_Tabs !== "undefined"
          ? Zotero_Tabs
          : mainWindow && mainWindow.Zotero_Tabs;
        if (this.zotero && this.zotero.Reader &&
            typeof this.zotero.Reader.getByTabID === "function" &&
            tabs && tabs.selectedID) {
          return this.zotero.Reader.getByTabID(tabs.selectedID);
        }
      } catch (error) {
        this.logger.warn("Selected tab Reader probe failed: " + String(error));
      }
      return null;
    }

    _findOpenReaderForAttachment(attachmentKey) {
      if (!attachmentKey || !this.zotero || !this.zotero.Reader) {
        return null;
      }
      var readers = this.zotero.Reader._readers;
      if (!readers) {
        return null;
      }
      var values;
      if (Array.isArray(readers)) {
        values = readers;
      } else if (typeof readers.values === "function") {
        values = Array.from(readers.values());
      } else {
        values = Object.keys(readers).map(function (key) {
          return readers[key];
        });
      }
      for (var reader of values) {
        if (this._readerMatchesAttachment(reader, attachmentKey)) {
          return reader;
        }
      }
      return null;
    }

    _readerMatchesAttachment(reader, attachmentKey) {
      if (!reader || !attachmentKey) {
        return false;
      }
      var item = reader._item || null;
      var itemID = firstDefined(reader.itemID, reader._itemID);
      if (!item && itemID !== null && this.zotero && this.zotero.Items &&
          typeof this.zotero.Items.get === "function") {
        item = this.zotero.Items.get(itemID);
      }
      return Boolean(item && item.key === attachmentKey);
    }

    _findAttachmentItem(attachmentKey) {
      if (!attachmentKey) {
        return null;
      }
      try {
        var contexts = [
          this.latestReaderContext,
          { reader: this._resolveSelectedTabReader(), params: {} }
        ];
        for (var context of contexts) {
          if (!context) {
            continue;
          }
          var attachment = this._getAttachmentItem(context);
          if (attachment && attachment.key === attachmentKey) {
            return attachment;
          }
        }
        if (this.adapter && typeof this.adapter.getAttachmentByKey === "function") {
          return this.adapter.getAttachmentByKey(attachmentKey);
        }
      } catch (error) {
        this.logger.warn("Attachment lookup for Reader open failed: " + String(error));
      }
      return null;
    }

    _getAnnotation(readerContext) {
      var params = readerContext.params || {};
      if (params.annotation) {
        return params.annotation;
      }
      if (params.annotations && params.annotations.length) {
        return params.annotations[0];
      }
      if (params.ids && params.ids.length && this.zotero && this.zotero.Items &&
          typeof this.zotero.Items.get === "function") {
        return this.zotero.Items.get(params.ids[0]);
      }
      return null;
    }

    _getAttachmentItem(readerContext) {
      var params = readerContext.params || {};
      var reader = readerContext.reader || {};
      if (params.attachmentItem) {
        return params.attachmentItem;
      }
      if (reader._item) {
        return reader._item;
      }
      var itemID = firstDefined(params.attachmentItemID, reader.itemID, reader._itemID);
      if (itemID !== null && this.zotero && this.zotero.Items &&
          typeof this.zotero.Items.get === "function") {
        return this.zotero.Items.get(itemID);
      }
      return null;
    }

    _parentItemKeyFromAttachment(attachment) {
      if (!attachment) {
        return null;
      }
      if (attachment.parentItem && attachment.parentItem.key) {
        return attachment.parentItem.key;
      }
      if (attachment.parentItemKey) {
        return attachment.parentItemKey;
      }
      if (!attachment.parentID || !this.zotero || !this.zotero.Items ||
          typeof this.zotero.Items.get !== "function") {
        return null;
      }
      var parent = this.zotero.Items.get(attachment.parentID);
      return parent && parent.key ? parent.key : null;
    }

    _register(method, argumentsList) {
      if (!this.adapter || typeof this.adapter[method] !== "function") {
        return {
          status: "unknown",
          registered: false,
          reason: "verified_reader_adapter_required"
        };
      }
      try {
        var dispose = this.adapter[method].apply(this.adapter, argumentsList);
        if (typeof dispose === "function") {
          this.disposers.push(dispose);
        }
        return { status: "adapter_registered", registered: true };
      } catch (error) {
        this.logger.warn(method + " failed: " + String(error));
        return {
          status: "adapter_failed",
          registered: false,
          reason: String(error)
        };
      }
    }
  }

  class PluginController {
    constructor(options) {
      options = options || {};
      this.zotero = options.zotero || null;
      this.services = options.services ||
        (typeof Services !== "undefined" ? Services : null);
      this.adapter = options.adapter || null;
      this.logger = createLogger(this.zotero);
      this.bridge = null;
      this.quickNote = null;
      this.sidebar = null;
      this.store = null;
      this.syncClient = null;
      this.registrations = null;
    }

    async start() {
      this.store = new ns.InspirationStore({
        storage: ns.createLocalPendingStorage(this.zotero, this.logger)
      });
      this.syncClient = new ns.InspirationSyncClient({
        store: this.store,
        dryRun: false,
        logger: this.logger,
        zotero: this.zotero
      });
      this.bridge = new ZoteroReaderBridge({
        zotero: this.zotero,
        services: this.services,
        adapter: this.adapter,
        logger: this.logger
      });
      this.quickNote = new ns.InspirationQuickNote({
        store: this.store,
        syncClient: this.syncClient,
        documentProvider: this.bridge.getPopupDocument.bind(this.bridge),
        onSaved: this._onSaved.bind(this)
      });
      this.sidebar = new ns.InspirationSidebar({
        store: this.store,
        fetchNotes: this.syncClient.listRemoteNotesByAttachment.bind(this.syncClient),
        captureSelection: this.captureSelectionWithPromptFallback.bind(this),
        navigate: this.bridge.jumpToNote.bind(this.bridge),
        navigateByClientId: this.jumpToNoteByClientId.bind(this),
        logger: this.logger
      });

      var self = this;
      this.registrations = this.bridge.registerEntryPoints({
        openQuickNote: function (context, document) {
          return self.openQuickNoteForContext(
            context || self.bridge.captureFromCurrentSelection(),
            document
          );
        },
        mountSidebar: function (container) {
          var capture = self.bridge.captureFromCurrentSelection();
          return self.sidebar.mount(container, capture.zotero_attachment_key);
        }
      });
      this.logger.log("capture controller started; inspect capability result for Reader fallbacks");
      return this.registrations;
    }

    async stop() {
      if (this.quickNote) {
        this.quickNote.close();
      }
      if (this.sidebar) {
        this.sidebar.close();
      }
      if (this.bridge) {
        this.bridge.shutdown();
      }
    }

    captureCurrentSelection() {
      return this.bridge.captureFromCurrentSelection();
    }

    async openQuickNoteForCurrentSelection() {
      var context = this.captureCurrentSelection();
      return this.openQuickNoteForContext(context);
    }

    async openQuickNoteForContext(context, document) {
      var opened = this.bridge.openQuickNoteForContext(this.quickNote, context, document);
      if (opened.opened || opened.reason !== "popup_host_unavailable") {
        return opened;
      }
      this.logger.warn("Quick note popup host unavailable; using prompt fallback");
      return this.openQuickNotePromptFallback(context);
    }

    async captureSelectionWithPromptFallback() {
      return this.captureSelectionAndPromptNote();
    }

    async captureSelectionAndPromptNote() {
      return this.openQuickNotePromptFallback(this.captureCurrentSelection());
    }

    async openQuickNotePromptFallback(captureContext) {
      var promptService = this.services && this.services.prompt;
      if (!promptService || typeof promptService.prompt !== "function") {
        this.logger.warn("Quick note prompt fallback unavailable");
        return {
          capture: captureContext,
          opened: false,
          reason: "prompt_fallback_unavailable",
          ui_mode: "prompt_fallback"
        };
      }
      var noteInput = { value: "" };
      var noteAccepted = promptService.prompt(
        null,
        "Search Inspiration",
        "Enter inspiration note text:",
        noteInput,
        null,
        {}
      );
      if (!noteAccepted) {
        return {
          capture: captureContext,
          opened: false,
          reason: "prompt_cancelled",
          ui_mode: "prompt_fallback"
        };
      }
      var tagsInput = { value: "__kl_real_capture_test__, \u7075\u611f" };
      var tagsAccepted = promptService.prompt(
        null,
        "Search Inspiration",
        "Enter tags separated by commas:",
        tagsInput,
        null,
        {}
      );
      if (!tagsAccepted) {
        return {
          capture: captureContext,
          opened: false,
          reason: "prompt_cancelled",
          ui_mode: "prompt_fallback"
        };
      }
      return this.saveCapturedInspirationNote(captureContext, {
        note_text: noteInput.value,
        user_tags: String(tagsInput.value).split(","),
        selection_type: captureContext.selected_text ? "paragraph" : "manual",
        ui_mode: "prompt_fallback"
      });
    }

    async saveCapturedInspirationNote(captureContext, noteInput) {
      noteInput = noteInput || {};
      var payload = ns.buildInspirationNotePayload(captureContext, {
        note_text: noteInput.note_text,
        user_tags: noteInput.user_tags,
        selection_type: noteInput.selection_type
      });
      var saved = this.store.upsertNote(payload);
      var result = await this.syncClient.upsertNote(saved);
      this._onSaved(saved, result);
      return {
        capture: captureContext,
        payload: payload,
        local_note: saved,
        sync: result,
        ui_mode: noteInput.ui_mode || "direct_save"
      };
    }

    listLocalNotesForCurrentAttachment() {
      var context = this.captureCurrentSelection();
      var attachmentKey = context.zotero_attachment_key;
      return {
        attachment_key: attachmentKey,
        anchor_status: context.anchor_status,
        capture_method: context.capture_method,
        warnings: context.warnings,
        notes: attachmentKey ? this.store.listNotesByAttachment(attachmentKey) : [],
        pending_notes: this.store.listPendingNotes(),
        persistence_status: this.store.storage.persistenceStatus
      };
    }

    async listRemoteNotesForCurrentAttachment() {
      var context = this.captureCurrentSelection();
      var attachmentKey = context.zotero_attachment_key;
      var result = await this.syncClient.listRemoteNotesByAttachment(attachmentKey);
      this.logger.log("remote current attachment notes: " + JSON.stringify({
        attachment_key: attachmentKey,
        count: result.count,
        status: result.status,
        client: result.client
      }));
      return result;
    }

    async exportSelectedLibraryPdfMarkdown(options) {
      options = options || {};
      var selection = this.resolveSelectedLibraryPdfAttachment();
      if (selection.status !== "OK") {
        return Object.assign({
          status: "FAILED",
          markdown: "",
          metadata: null,
          counts: null
        }, selection);
      }
      return this._exportMarkdownForResolvedAttachment(
        {
          zotero_attachment_key: selection.zotero_attachment_key,
          zotero_item_key: selection.zotero_item_key,
          source: "library_selection",
          selected_item_key: selection.selected_item_key,
          selected_item_type: selection.selected_item_type,
          pdf_attachment_count: selection.pdf_attachment_count,
          warnings: selection.warnings || []
        },
        {
          saveToFile: options.saveToFile !== false,
          resultKey: "library_selection"
        }
      );
    }

    async saveSelectedLibraryPdfMarkdown() {
      return this.exportSelectedLibraryPdfMarkdown({ saveToFile: true });
    }

    async saveMarkdownForSelectedLibraryPdf() {
      return this.saveSelectedLibraryPdfMarkdown();
    }

    resolveSelectedLibraryPdfAttachment() {
      var items = this._getSelectedLibraryItems();
      if (!items.length) {
        return this._librarySelectionFailure(
          "selected_item_unavailable",
          "No Zotero library item is selected."
        );
      }
      if (items.length > 1) {
        return this._librarySelectionFailure(
          "multiple_selected_items_found",
          "Select exactly one Zotero item or PDF attachment before exporting.",
          { selected_count: items.length }
        );
      }
      var selectedItem = items[0];
      var selectedKey = this._itemKey(selectedItem);
      if (this._itemIsPdfAttachment(selectedItem)) {
        return {
          status: "OK",
          zotero_item_key: this._parentItemKeyFromAttachment(selectedItem),
          zotero_attachment_key: selectedKey,
          selected_item_key: selectedKey,
          selected_item_type: "pdf_attachment",
          pdf_attachment_count: 1,
          warnings: []
        };
      }
      if (this._itemIsAttachment(selectedItem)) {
        return this._librarySelectionFailure(
          "pdf_attachment_not_found",
          "The selected attachment is not a PDF attachment.",
          {
            selected_item_key: selectedKey,
            selected_item_type: "non_pdf_attachment"
          }
        );
      }

      var pdfAttachments = this._pdfAttachmentsForParentItem(selectedItem);
      if (!pdfAttachments.length) {
        return this._librarySelectionFailure(
          "pdf_attachment_not_found",
          "No PDF attachment was found for the selected Zotero item.",
          {
            selected_item_key: selectedKey,
            selected_item_type: "parent_item"
          }
        );
      }
      if (pdfAttachments.length > 1) {
        return this._librarySelectionFailure(
          "multiple_pdf_attachments_found",
          "Multiple PDF attachments were found for the selected Zotero item.",
          {
            selected_item_key: selectedKey,
            selected_item_type: "parent_item",
            pdf_attachment_count: pdfAttachments.length,
            pdf_attachment_keys: pdfAttachments.map(this._itemKey.bind(this))
          }
        );
      }
      return {
        status: "OK",
        zotero_item_key: selectedKey,
        zotero_attachment_key: this._itemKey(pdfAttachments[0]),
        selected_item_key: selectedKey,
        selected_item_type: "parent_item",
        pdf_attachment_count: 1,
        warnings: []
      };
    }

    async exportCurrentPdfMarkdown(options) {
      options = options || {};
      var context = this.captureCurrentSelection();
      var attachmentKey = context.zotero_attachment_key;
      if (!attachmentKey) {
        return {
          status: "FAILED",
          error: "attachment_key_unavailable",
          capture: context,
          markdown: "",
          metadata: null
        };
      }
      return this._exportMarkdownForResolvedAttachment(context, {
        saveToFile: Boolean(options.saveToFile),
        resultKey: "capture"
      });
    }

    async _exportMarkdownForResolvedAttachment(context, options) {
      options = options || {};
      var attachmentKey = context.zotero_attachment_key;
      var result = await this.syncClient.exportMarkdownForAttachment({
        attachmentKey: attachmentKey,
        itemKey: context.zotero_item_key,
        saveToFile: Boolean(options.saveToFile)
      });
      this.logger.log("Markdown export result: " + JSON.stringify({
        status: result.status,
        attachment_key: attachmentKey,
        item_key: context.zotero_item_key || null,
        chunks: result.counts && result.counts.chunks,
        notes: result.counts && result.counts.notes,
        markdown_chars: result.markdown_chars,
        large_export_warning: result.large_export_warning,
        copy_not_recommended: result.copy_not_recommended,
        output_path: result.output_path || null,
        error: result.error_code || result.error || null
      }));
      var contextKey = options.resultKey || "capture";
      var wrapped = {};
      wrapped[contextKey] = context;
      return Object.assign(wrapped, result);
    }

    async saveCurrentPdfMarkdown() {
      return this.exportCurrentPdfMarkdown({ saveToFile: true });
    }

    async copyCurrentPdfMarkdown() {
      var result = await this.exportCurrentPdfMarkdown({ saveToFile: false });
      if (result.status !== "OK") {
        return result;
      }
      if (result.copy_not_recommended) {
        var largeSaved = await this.saveCurrentPdfMarkdown();
        if (largeSaved.status !== "OK") {
          return Object.assign({}, largeSaved, {
            copy_status: "large_export_save_failed"
          });
        }
        return Object.assign({}, largeSaved, {
          status: "large_export_saved_to_file",
          export_status: largeSaved.status,
          copy_status: "large_export_saved_to_file"
        });
      }
      try {
        await this._copyTextToClipboard(result.markdown || "");
        return Object.assign({}, result, {
          copy_status: "copied"
        });
      } catch (error) {
        var saved = await this.saveCurrentPdfMarkdown();
        if (saved.status !== "OK") {
          return Object.assign({}, saved, {
            copy_status: "clipboard_unavailable_save_failed",
            clipboard_error: String(error)
          });
        }
        return Object.assign({}, saved, {
          status: "clipboard_unavailable_saved_to_file",
          export_status: saved.status,
          copy_status: "clipboard_unavailable_saved_to_file",
          clipboard_error: String(error)
        });
      }
    }

    _getSelectedLibraryItems() {
      try {
        var mainWindow = this._getMainWindow();
        var pane = null;
        if (this.zotero && typeof this.zotero.getActiveZoteroPane === "function") {
          pane = this.zotero.getActiveZoteroPane();
        }
        pane = pane ||
          mainWindow && mainWindow.ZoteroPane ||
          mainWindow && mainWindow.Zotero && mainWindow.Zotero.ZoteroPane ||
          null;
        var items = null;
        if (pane && typeof pane.getSelectedItems === "function") {
          items = pane.getSelectedItems();
        } else if (pane && typeof pane.getSelectedItem === "function") {
          items = pane.getSelectedItem();
        }
        if (!items) {
          return [];
        }
        if (!Array.isArray(items)) {
          items = [items];
        }
        return items.filter(function (item) {
          return Boolean(item);
        });
      } catch (error) {
        this.logger.warn("Library selected item lookup failed: " + String(error));
        return [];
      }
    }

    _pdfAttachmentsForParentItem(item) {
      var children = this._attachmentChildrenForItem(item);
      return children.filter(this._itemIsPdfAttachment.bind(this));
    }

    _attachmentChildrenForItem(item) {
      var childItems = [];
      var attachmentIds = [];
      try {
        if (item && typeof item.getAttachments === "function") {
          attachmentIds = item.getAttachments() || [];
        } else if (item && Array.isArray(item.attachments)) {
          attachmentIds = item.attachments;
        }
        for (var index = 0; index < attachmentIds.length; index += 1) {
          var child = attachmentIds[index];
          if (child && typeof child === "object") {
            childItems.push(child);
          } else if (this.zotero && this.zotero.Items &&
              typeof this.zotero.Items.get === "function") {
            var resolved = this.zotero.Items.get(child);
            if (resolved) {
              childItems.push(resolved);
            }
          }
        }
      } catch (error) {
        this.logger.warn("PDF attachment child lookup failed: " + String(error));
      }
      return childItems;
    }

    _itemIsAttachment(item) {
      try {
        if (!item) {
          return false;
        }
        if (typeof item.isAttachment === "function") {
          return Boolean(item.isAttachment());
        }
        return item.itemType === "attachment" || item.itemTypeID === 14;
      } catch (error) {
        return false;
      }
    }

    _itemIsPdfAttachment(item) {
      try {
        if (!item || !this._itemIsAttachment(item)) {
          return false;
        }
        if (typeof item.isPDFAttachment === "function" && item.isPDFAttachment()) {
          return true;
        }
        var contentType = String(firstDefined(
          item.attachmentContentType,
          item.contentType,
          typeof item.getField === "function" ? item.getField("contentType") : null
        ) || "").toLowerCase();
        if (contentType === "application/pdf") {
          return true;
        }
        var path = String(firstDefined(
          item.attachmentPath,
          typeof item.getFilePath === "function" ? item.getFilePath() : null
        ) || "").toLowerCase();
        return path.slice(-4) === ".pdf";
      } catch (error) {
        return false;
      }
    }

    _itemKey(item) {
      return item && item.key ? item.key : null;
    }

    _librarySelectionFailure(code, message, extra) {
      return Object.assign({
        status: "FAILED",
        error: code,
        error_code: code,
        error_message: message,
        zotero_item_key: null,
        zotero_attachment_key: null,
        warnings: []
      }, extra || {});
    }

    async openInspirationSidebar() {
      var context = this.captureCurrentSelection();
      var host = await this.bridge.getSidebarHost();
      var result = await this.sidebar.openInspirationSidebar(
        host,
        context.zotero_attachment_key
      );
      this.logger.log("sidebar open result: " + JSON.stringify({
        opened: result.opened,
        ui_mode: result.ui_mode || null,
        attachment_key: context.zotero_attachment_key,
        reason: result.reason || null
      }));
      return result;
    }

    async refreshInspirationSidebar() {
      var context = this.captureCurrentSelection();
      var result = await this.sidebar.refreshInspirationSidebar(
        context.zotero_attachment_key
      );
      this.logger.log("sidebar refresh result: " + JSON.stringify({
        attachment_key: context.zotero_attachment_key,
        count: result.count,
        status: result.status
      }));
      return result;
    }

    closeInspirationSidebar() {
      return this.sidebar.closeInspirationSidebar();
    }

    async jumpToNote(note) {
      var result = await this.bridge.jumpToNote(note);
      this.logger.log("jump to note result: " + JSON.stringify(result));
      return result;
    }

    async jumpToNoteByClientId(clientNoteId) {
      var notesResult = await this.listRemoteNotesForCurrentAttachment();
      var note = (notesResult.items || []).find(function (candidate) {
        return candidate.client_note_id === clientNoteId;
      });
      if (!note) {
        return {
          ok: false,
          reason: "note_not_found_for_current_attachment",
          note_id: clientNoteId
        };
      }
      return this.jumpToNote(note);
    }

    async syncPendingNotes() {
      var results = await this.store.syncPendingNotes(this.syncClient);
      this.logger.log("pending notes sync result " + JSON.stringify(results));
      return results;
    }

    async runManualSmokeSync() {
      var payload = ns.buildPluginSmokePayload();
      var saved = this.store.upsertNote(payload);
      var liveClient = new ns.InspirationSyncClient({
        store: this.store,
        dryRun: false,
        logger: this.logger,
        zotero: this.zotero
      });
      this.logger.log("plugin smoke payload " + JSON.stringify(saved));
      var result = await liveClient.upsertNote(saved);
      this.logger.log("plugin smoke sync result " + JSON.stringify(result));
      return { payload: saved, sync: result };
    }

    async checkBackendStatus() {
      var liveClient = new ns.InspirationSyncClient({
        dryRun: false,
        logger: this.logger,
        zotero: this.zotero
      });
      var result = await liveClient.checkBackendStatus();
      this.logger.log("backend status result " + JSON.stringify(result));
      return result;
    }

    async _copyTextToClipboard(text) {
      if (this.zotero && this.zotero.Utilities &&
          this.zotero.Utilities.Internal &&
          typeof this.zotero.Utilities.Internal.copyTextToClipboard === "function") {
        this.zotero.Utilities.Internal.copyTextToClipboard(text);
        return;
      }
      if (this.services && this.services.clipboardHelper &&
          typeof this.services.clipboardHelper.copyString === "function") {
        this.services.clipboardHelper.copyString(text);
        return;
      }
      if (typeof navigator !== "undefined" && navigator.clipboard &&
          typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(text);
        return;
      }
      if (typeof Components !== "undefined" && Components.classes &&
          Components.interfaces && Components.classes["@mozilla.org/widget/clipboardhelper;1"]) {
        var helper = Components.classes["@mozilla.org/widget/clipboardhelper;1"]
          .getService(Components.interfaces.nsIClipboardHelper);
        helper.copyString(text);
        return;
      }
      throw new Error("clipboard_api_unavailable");
    }

    _onSaved(saved, result) {
      this.logger.log("inspiration note save result " + JSON.stringify({
        client_note_id: saved.client_note_id,
        sync_status: result.sync_status,
        client: result.client || "none"
      }));
      if (this.sidebar && this.sidebar.isOpen()) {
        this.sidebar.refresh();
      }
    }
  }

  ns.ZoteroReaderBridge = ZoteroReaderBridge;
  ns.PluginController = PluginController;
})(NotebookAIInspiration);
