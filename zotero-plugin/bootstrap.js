/* global Zotero, Services */
"use strict";

var NotebookAIInspiration = {};
var NOTEBOOK_AI_INSPIRATION_PLUGIN = null;
var NOTEBOOK_AI_INSPIRATION_SCOPE = this;
var NOTEBOOK_AI_PUBLIC_API = null;
var NOTEBOOK_AI_TOOLS_MENU_ITEMS = [];
var NOTEBOOK_AI_LIBRARY_MENU_ITEMS = [];

var PLUGIN_VERSION = "0.1.0";
var CAPTURE_MENU_ITEM_ID = "notebook-ai-inspiration-capture-selection";
var CAPTURE_MENU_LABEL = "Notebook AI Inspiration: Capture Current Selection";
var PROMPT_CAPTURE_MENU_ITEM_ID = "notebook-ai-inspiration-capture-selection-prompt";
var PROMPT_CAPTURE_MENU_LABEL = "Notebook AI Inspiration: Capture Selection with Prompt";
var LIST_MENU_ITEM_ID = "notebook-ai-inspiration-list-current-notes";
var LIST_MENU_LABEL = "Notebook AI Inspiration: List Current Attachment Notes";
var SIDEBAR_MENU_ITEM_ID = "notebook-ai-inspiration-open-notes-sidebar";
var SIDEBAR_MENU_LABEL = "Notebook AI Inspiration: Open Notes Sidebar";
var REFRESH_MENU_ITEM_ID = "notebook-ai-inspiration-refresh-notes";
var REFRESH_MENU_LABEL = "Notebook AI Inspiration: Refresh Notes";
var COPY_MARKDOWN_MENU_ITEM_ID = "notebook-ai-inspiration-copy-current-pdf-markdown";
var COPY_MARKDOWN_MENU_LABEL = "Notebook AI Inspiration: Copy Current PDF Markdown";
var SAVE_MARKDOWN_MENU_ITEM_ID = "notebook-ai-inspiration-save-current-pdf-markdown";
var SAVE_MARKDOWN_MENU_LABEL = "NOTEBOOK_AI: Save Current PDF Markdown";
var LIBRARY_EXPORT_MARKDOWN_MENU_ITEM_ID = "notebook-ai-export-markdown-selected-pdf";
var LIBRARY_EXPORT_MARKDOWN_MENU_LABEL = "NOTEBOOK_AI: Export Markdown for Selected PDF";
var TOOLS_MENU_ITEM_ID = "notebook-ai-inspiration-run-smoke-sync";
var TOOLS_MENU_LABEL = "Notebook AI Inspiration: Run Smoke Sync";

var MODULE_PATHS = [
  "src/inspirationStore.js",
  "src/syncClient.js",
  "src/inspirationQuickNote.js",
  "src/inspirationSidebar.js",
  "src/zoteroReaderBridge.js"
];

function install(data, reason) {
  log("install reason=" + String(reason));
}

async function startup(data, reason) {
  log("startup");
  if (typeof Zotero === "undefined" || typeof Services === "undefined") {
    throw new Error("Zotero bootstrap services are not available.");
  }

  if (Zotero.initializationPromise) {
    await Zotero.initializationPromise;
  }

  for (var path of MODULE_PATHS) {
    Services.scriptloader.loadSubScript(data.rootURI + path, NOTEBOOK_AI_INSPIRATION_SCOPE);
  }

  NOTEBOOK_AI_INSPIRATION_PLUGIN = new NotebookAIInspiration.PluginController({
    zotero: Zotero,
    services: Services
  });
  await NOTEBOOK_AI_INSPIRATION_PLUGIN.start();
  registerSmokeAPI();
  registerToolsMenuAction();
  registerLibraryContextMenuAction();
}

async function shutdown(data, reason) {
  log("shutdown reason=" + String(reason));
  removeLibraryContextMenuAction();
  removeToolsMenuAction();
  unregisterSmokeAPI();
  if (NOTEBOOK_AI_INSPIRATION_PLUGIN) {
    await NOTEBOOK_AI_INSPIRATION_PLUGIN.stop();
    NOTEBOOK_AI_INSPIRATION_PLUGIN = null;
  }
  NotebookAIInspiration = {};
}

function uninstall(data, reason) {
  log("uninstall reason=" + String(reason));
}

function registerSmokeAPI() {
  var controller = NOTEBOOK_AI_INSPIRATION_PLUGIN;
  NOTEBOOK_AI_PUBLIC_API = {
    runManualSmokeSync: controller.runManualSmokeSync.bind(controller),
    checkBackendStatus: controller.checkBackendStatus.bind(controller),
    captureCurrentSelection: controller.captureCurrentSelection.bind(controller),
    openQuickNoteForCurrentSelection:
      controller.openQuickNoteForCurrentSelection.bind(controller),
    captureSelectionWithPromptFallback:
      controller.captureSelectionWithPromptFallback.bind(controller),
    listLocalNotesForCurrentAttachment:
      controller.listLocalNotesForCurrentAttachment.bind(controller),
    listRemoteNotesForCurrentAttachment:
      controller.listRemoteNotesForCurrentAttachment.bind(controller),
    exportCurrentPdfMarkdown:
      controller.exportCurrentPdfMarkdown.bind(controller),
    copyCurrentPdfMarkdown:
      controller.copyCurrentPdfMarkdown.bind(controller),
    saveCurrentPdfMarkdown:
      controller.saveCurrentPdfMarkdown.bind(controller),
    exportSelectedLibraryPdfMarkdown:
      controller.exportSelectedLibraryPdfMarkdown.bind(controller),
    saveSelectedLibraryPdfMarkdown:
      controller.saveSelectedLibraryPdfMarkdown.bind(controller),
    saveMarkdownForSelectedLibraryPdf:
      controller.saveMarkdownForSelectedLibraryPdf.bind(controller),
    openInspirationSidebar: controller.openInspirationSidebar.bind(controller),
    refreshInspirationSidebar: controller.refreshInspirationSidebar.bind(controller),
    closeInspirationSidebar: controller.closeInspirationSidebar.bind(controller),
    jumpToNoteByClientId: controller.jumpToNoteByClientId.bind(controller),
    jumpToNote: controller.jumpToNote.bind(controller),
    syncPendingNotes: controller.syncPendingNotes.bind(controller),
    buildPluginSmokePayload: function (options) {
      return NotebookAIInspiration.buildPluginSmokePayload(options);
    },
    getStatus: function () {
      return {
        plugin_loaded: true,
        smoke_api_registered: true,
        sync_endpoint: NotebookAIInspiration.DEFAULT_SYNC_ENDPOINT,
        markdown_export_endpoint: NotebookAIInspiration.DEFAULT_MARKDOWN_EXPORT_ENDPOINT,
        library_export_menu_label: LIBRARY_EXPORT_MARKDOWN_MENU_LABEL,
        version: PLUGIN_VERSION
      };
    }
  };

  Zotero.NotebookAIInspirationPlugin = NOTEBOOK_AI_PUBLIC_API;
  try {
    if (typeof globalThis !== "undefined") {
      globalThis.NOTEBOOK_AI_INSPIRATION_PLUGIN = NOTEBOOK_AI_PUBLIC_API;
    }
    if (typeof window !== "undefined") {
      window.NOTEBOOK_AI_INSPIRATION_PLUGIN = NOTEBOOK_AI_PUBLIC_API;
    }
  } catch (error) {
    log("compatibility global exposure unavailable: " + String(error));
  }
  log("smoke API registered");
}

function unregisterSmokeAPI() {
  try {
    if (typeof Zotero !== "undefined" &&
        Zotero.NotebookAIInspirationPlugin === NOTEBOOK_AI_PUBLIC_API) {
      delete Zotero.NotebookAIInspirationPlugin;
    }
    if (typeof globalThis !== "undefined" &&
        globalThis.NOTEBOOK_AI_INSPIRATION_PLUGIN === NOTEBOOK_AI_PUBLIC_API) {
      delete globalThis.NOTEBOOK_AI_INSPIRATION_PLUGIN;
    }
    if (typeof window !== "undefined" &&
        window.NOTEBOOK_AI_INSPIRATION_PLUGIN === NOTEBOOK_AI_PUBLIC_API) {
      delete window.NOTEBOOK_AI_INSPIRATION_PLUGIN;
    }
  } catch (error) {
    log("smoke API cleanup failed: " + String(error));
  }
  NOTEBOOK_AI_PUBLIC_API = null;
}

function registerToolsMenuAction() {
  try {
    var mainWindow = getMainWindow();
    var document = mainWindow && mainWindow.document;
    if (!document) {
      throw new Error("main window document unavailable");
    }

    var popup = document.getElementById("menu_ToolsPopup") ||
      document.getElementById("menu_toolsPopup");
    if (!popup) {
      throw new Error("Tools menu popup unavailable");
    }

    addToolsMenuItem(
      document,
      popup,
      CAPTURE_MENU_ITEM_ID,
      CAPTURE_MENU_LABEL,
      onToolsMenuCaptureCommand
    );
    addToolsMenuItem(
      document,
      popup,
      PROMPT_CAPTURE_MENU_ITEM_ID,
      PROMPT_CAPTURE_MENU_LABEL,
      onToolsMenuPromptCaptureCommand
    );
    addToolsMenuItem(
      document,
      popup,
      LIST_MENU_ITEM_ID,
      LIST_MENU_LABEL,
      onToolsMenuListCommand
    );
    addToolsMenuItem(
      document,
      popup,
      SIDEBAR_MENU_ITEM_ID,
      SIDEBAR_MENU_LABEL,
      onToolsMenuSidebarCommand
    );
    addToolsMenuItem(
      document,
      popup,
      REFRESH_MENU_ITEM_ID,
      REFRESH_MENU_LABEL,
      onToolsMenuRefreshCommand
    );
    addToolsMenuItem(
      document,
      popup,
      COPY_MARKDOWN_MENU_ITEM_ID,
      COPY_MARKDOWN_MENU_LABEL,
      onToolsMenuCopyMarkdownCommand
    );
    addToolsMenuItem(
      document,
      popup,
      SAVE_MARKDOWN_MENU_ITEM_ID,
      SAVE_MARKDOWN_MENU_LABEL,
      onToolsMenuSaveMarkdownCommand
    );
    addToolsMenuItem(
      document,
      popup,
      TOOLS_MENU_ITEM_ID,
      TOOLS_MENU_LABEL,
      onToolsMenuSmokeCommand
    );
    log("tools menu registered");
  } catch (error) {
    log("tools menu registration failed: " + String(error));
  }
}

function registerLibraryContextMenuAction() {
  try {
    var mainWindow = getMainWindow();
    var document = mainWindow && mainWindow.document;
    if (!document) {
      throw new Error("main window document unavailable");
    }

    var popup = firstExistingElement(document, [
      "zotero-itemmenu",
      "zotero-itemmenu-popup",
      "zotero-items-menu",
      "zotero-item-context-menu"
    ]);
    if (!popup) {
      throw new Error("Zotero item context menu popup unavailable");
    }

    addMenuItem(
      document,
      popup,
      LIBRARY_EXPORT_MARKDOWN_MENU_ITEM_ID,
      LIBRARY_EXPORT_MARKDOWN_MENU_LABEL,
      onLibraryMenuExportMarkdownCommand,
      NOTEBOOK_AI_LIBRARY_MENU_ITEMS
    );
    log("library context menu registered");
  } catch (error) {
    log("library context menu registration failed: " + String(error));
  }
}

function addToolsMenuItem(document, popup, id, label, handler) {
  addMenuItem(document, popup, id, label, handler, NOTEBOOK_AI_TOOLS_MENU_ITEMS);
}

function addMenuItem(document, popup, id, label, handler, registrations) {
  var previous = document.getElementById(id);
  if (previous && previous.parentNode) {
    previous.parentNode.removeChild(previous);
  }
  var menuItem = typeof document.createXULElement === "function"
    ? document.createXULElement("menuitem")
    : document.createElement("menuitem");
  menuItem.setAttribute("id", id);
  menuItem.setAttribute("label", label);
  menuItem.addEventListener("command", handler);
  popup.appendChild(menuItem);
  registrations.push({ element: menuItem, handler: handler });
}

async function onToolsMenuCaptureCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.openQuickNoteForCurrentSelection();
    log("tools menu capture result: " + JSON.stringify(result.sync || result));
  } catch (error) {
    log("tools menu capture failed: " + String(error));
  }
}

async function onToolsMenuPromptCaptureCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.captureSelectionWithPromptFallback();
    log("tools menu prompt capture result: " + JSON.stringify(result.sync || result));
  } catch (error) {
    log("tools menu prompt capture failed: " + String(error));
  }
}

async function onToolsMenuListCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.listLocalNotesForCurrentAttachment();
    log("tools menu current attachment notes: " + JSON.stringify(result));
  } catch (error) {
    log("tools menu current attachment notes failed: " + String(error));
  }
}

async function onToolsMenuSidebarCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.openInspirationSidebar();
    log("tools menu sidebar result: " + JSON.stringify(result));
  } catch (error) {
    log("tools menu sidebar failed: " + String(error));
  }
}

async function onToolsMenuRefreshCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.refreshInspirationSidebar();
    log("tools menu refresh notes result: " + JSON.stringify(result));
  } catch (error) {
    log("tools menu refresh notes failed: " + String(error));
  }
}

async function onToolsMenuCopyMarkdownCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.copyCurrentPdfMarkdown();
    showMarkdownExportResult("NOTEBOOK_AI Markdown Copy", result);
    log("tools menu copy Markdown result: " + JSON.stringify({
      status: result.status,
      copy_status: result.copy_status || null,
      output_path: result.output_path || null,
      markdown_chars: result.markdown_chars || null
    }));
  } catch (error) {
    log("tools menu copy Markdown failed: " + String(error));
  }
}

async function onToolsMenuSaveMarkdownCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.saveCurrentPdfMarkdown();
    showMarkdownExportResult("NOTEBOOK_AI Current PDF Markdown", result);
    log("tools menu save Markdown result: " + JSON.stringify({
      status: result.status,
      output_path: result.output_path || null,
      markdown_chars: result.markdown_chars || null
    }));
  } catch (error) {
    log("tools menu save Markdown failed: " + String(error));
  }
}

async function onLibraryMenuExportMarkdownCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.saveMarkdownForSelectedLibraryPdf();
    showMarkdownExportResult("NOTEBOOK_AI Selected PDF Markdown", result);
    log("library menu export Markdown result: " + JSON.stringify({
      status: result.status,
      error: result.error_code || result.error || null,
      output_path: result.output_path || null,
      chunks: result.counts && result.counts.chunks,
      notes: result.counts && result.counts.notes,
      large_export_warning: result.large_export_warning || false
    }));
  } catch (error) {
    log("library menu export Markdown failed: " + String(error));
    showMarkdownExportResult("NOTEBOOK_AI Selected PDF Markdown", {
      status: "FAILED",
      error: "menu_export_failed",
      error_message: String(error)
    });
  }
}

async function onToolsMenuSmokeCommand() {
  try {
    var result = await Zotero.NotebookAIInspirationPlugin.runManualSmokeSync();
    log("tools menu smoke sync succeeded: " + JSON.stringify(result.sync || result));
  } catch (error) {
    log("tools menu smoke sync failed: " + String(error));
  }
}

function removeToolsMenuAction() {
  removeMenuRegistrations(NOTEBOOK_AI_TOOLS_MENU_ITEMS, "tools menu");
  NOTEBOOK_AI_TOOLS_MENU_ITEMS = [];
}

function removeLibraryContextMenuAction() {
  removeMenuRegistrations(NOTEBOOK_AI_LIBRARY_MENU_ITEMS, "library context menu");
  NOTEBOOK_AI_LIBRARY_MENU_ITEMS = [];
}

function removeMenuRegistrations(registrations, label) {
  for (var registration of registrations) {
    try {
      registration.element.removeEventListener("command", registration.handler);
      if (registration.element.parentNode) {
        registration.element.parentNode.removeChild(registration.element);
      }
    } catch (error) {
      log(label + " cleanup failed: " + String(error));
    }
  }
}

function showMarkdownExportResult(title, result) {
  result = result || {};
  var counts = result.counts || {};
  var error = result.error_code || result.error || "";
  var lines = [
    "status: " + String(result.status || "FAILED"),
    "output_path: " + String(result.output_path || ""),
    "chunks_count: " + String(counts.chunks === undefined ? "" : counts.chunks),
    "notes_count: " + String(counts.notes === undefined ? "" : counts.notes),
    "large_export_warning: " + String(Boolean(result.large_export_warning))
  ];
  if (error) {
    lines.push("error: " + String(error));
  }
  if (result.error_message) {
    lines.push("message: " + String(result.error_message));
  }
  try {
    if (Services.prompt && typeof Services.prompt.alert === "function") {
      Services.prompt.alert(null, title, lines.join("\n"));
      return;
    }
    var mainWindow = getMainWindow();
    if (mainWindow && typeof mainWindow.alert === "function") {
      mainWindow.alert(title + "\n\n" + lines.join("\n"));
    }
  } catch (notifyError) {
    log("Markdown export notification failed: " + String(notifyError));
  }
}

function getMainWindow() {
  var mainWindow = typeof Zotero.getMainWindow === "function"
    ? Zotero.getMainWindow()
    : null;
  if (!mainWindow && Services.wm &&
      typeof Services.wm.getMostRecentWindow === "function") {
    mainWindow = Services.wm.getMostRecentWindow("navigator:browser") ||
      Services.wm.getMostRecentWindow(null);
  }
  return mainWindow;
}

function firstExistingElement(document, ids) {
  for (var index = 0; index < ids.length; index += 1) {
    var element = document.getElementById(ids[index]);
    if (element) {
      return element;
    }
  }
  return null;
}

function log(message) {
  if (typeof Zotero !== "undefined" && typeof Zotero.debug === "function") {
    Zotero.debug("[NOTEBOOK_AI Inspiration] " + message);
  }
}
