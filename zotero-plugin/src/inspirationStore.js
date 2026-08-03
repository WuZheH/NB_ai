(function (ns) {
  "use strict";

  var STORAGE_KEY = "extensions.notebook_ai.inspiration.pending_queue_v1";
  var CREATED_AT_BUCKET_MS = 5 * 60 * 1000;

  class MemoryStorage {
    constructor() {
      this.values = {};
    }

    getItem(key) {
      return Object.prototype.hasOwnProperty.call(this.values, key)
        ? this.values[key]
        : null;
    }

    setItem(key, value) {
      this.values[key] = String(value);
    }
  }

  class ZoteroPreferenceStorage {
    constructor(zotero) {
      this.zotero = zotero;
    }

    getItem(key) {
      var value = this.zotero.Prefs.get(key, true);
      return typeof value === "string" ? value : null;
    }

    setItem(key, value) {
      this.zotero.Prefs.set(key, String(value), true);
    }
  }

  class ResilientStorage {
    constructor(primary, logger) {
      this.primary = primary;
      this.memory = new MemoryStorage();
      this.logger = logger || null;
      this.persistenceStatus = primary ? "unverified_primary" : "memory_only";
    }

    getItem(key) {
      if (this.primary) {
        try {
          var value = this.primary.getItem(key);
          if (value !== null) {
            this.memory.setItem(key, value);
            this.persistenceStatus = "primary_available";
            return value;
          }
        } catch (error) {
          this.persistenceStatus = "memory_fallback";
          this._log(error);
        }
      }
      return this.memory.getItem(key);
    }

    setItem(key, value) {
      this.memory.setItem(key, value);
      if (!this.primary) {
        return;
      }
      try {
        this.primary.setItem(key, value);
        this.persistenceStatus = "primary_available";
      } catch (error) {
        this.persistenceStatus = "memory_fallback";
        this._log(error);
      }
    }

    _log(error) {
      if (this.logger && typeof this.logger.warn === "function") {
        this.logger.warn("Pending queue persistence fallback: " + String(error));
      }
    }
  }

  function createLocalPendingStorage(zotero, logger) {
    var primary = null;
    if (zotero && zotero.Prefs &&
        typeof zotero.Prefs.get === "function" &&
        typeof zotero.Prefs.set === "function") {
      primary = new ZoteroPreferenceStorage(zotero);
    }
    return new ResilientStorage(primary, logger);
  }

  function buildDedupKeys(note) {
    var keys = [];
    var attachmentKey = note.zotero_attachment_key || "";
    var selectedHash = note.selected_text_hash || "";

    if (attachmentKey && note.zotero_annotation_key) {
      keys.push("annotation:" + attachmentKey + ":" + note.zotero_annotation_key);
    }
    if (note.client_note_id) {
      keys.push("client:" + note.client_note_id);
    }
    if (attachmentKey && selectedHash && note.created_at) {
      var createdAt = Date.parse(note.created_at);
      if (Number.isFinite(createdAt)) {
        keys.push(
          "time_bucket:" + attachmentKey + ":" + selectedHash + ":" +
          String(Math.floor(createdAt / CREATED_AT_BUCKET_MS))
        );
      }
    }
    if (attachmentKey && selectedHash &&
        note.pdf_page !== null && note.pdf_page !== undefined) {
      keys.push(
        "page:" + attachmentKey + ":" + String(note.pdf_page) + ":" + selectedHash
      );
    }
    return keys;
  }

  class InspirationStore {
    constructor(options) {
      options = options || {};
      this.storage = options.storage || new ResilientStorage(null, null);
      this.storageKey = options.storageKey || STORAGE_KEY;
    }

    upsertNote(note) {
      if (!note || !note.client_note_id) {
        throw new Error("client_note_id is required for a local note.");
      }

      var notes = this._read();
      var match = this._findFirstDuplicate(notes, note);
      var saved = Object.assign({}, note);

      if (match && match.note.client_note_id === note.client_note_id) {
        notes[match.index] = saved;
      } else {
        if (match) {
          saved.client_diagnostics = {
            local_duplicate: true,
            conflict_with_client_note_id: match.note.client_note_id
          };
        }
        notes.push(saved);
      }

      this._write(notes);
      return Object.assign({}, saved);
    }

    listNotesByAttachment(attachmentKey) {
      return this._read()
        .filter(function (note) {
          return note.zotero_attachment_key === attachmentKey;
        })
        .map(function (note) {
          return Object.assign({}, note);
        });
    }

    listPendingNotes() {
      return this._read()
        .filter(function (note) {
          return note.sync_status === "local_pending" ||
            note.sync_status === "sync_failed" ||
            note.sync_status === "conflict";
        })
        .map(function (note) {
          return Object.assign({}, note);
        });
    }

    markSynced(clientNoteId, serverResponse) {
      return this._updateByClientId(clientNoteId, function (note) {
        note.sync_status = "synced";
        note.server_note_id = serverResponse && typeof serverResponse === "object"
          ? serverResponse.server_note_id
          : serverResponse;
        note.server_response = serverResponse && typeof serverResponse === "object"
          ? Object.assign({}, serverResponse)
          : null;
        note.sync_error = null;
        return note;
      });
    }

    markFailed(clientNoteId, error) {
      return this._updateByClientId(clientNoteId, function (note) {
        note.sync_status = "sync_failed";
        note.sync_error = String(error || "sync failed");
        return note;
      });
    }

    async syncPendingNotes(syncClient) {
      if (!syncClient || typeof syncClient.upsertNote !== "function") {
        throw new Error("A sync client is required to retry pending notes.");
      }

      var pending = this.listPendingNotes();
      var results = [];
      for (var note of pending) {
        results.push(await syncClient.upsertNote(note));
      }
      return results;
    }

    async retryPending(syncClient) {
      return this.syncPendingNotes(syncClient);
    }

    deduplicate() {
      var groups = {};
      for (var note of this._read()) {
        for (var key of buildDedupKeys(note)) {
          groups[key] = groups[key] || [];
          groups[key].push(note.client_note_id);
        }
      }
      return Object.keys(groups)
        .filter(function (key) {
          return groups[key].length > 1;
        })
        .map(function (key) {
          return { key: key, client_note_ids: groups[key].slice() };
        });
    }

    _findFirstDuplicate(notes, candidate) {
      var candidateKeys = buildDedupKeys(candidate);
      for (var key of candidateKeys) {
        for (var index = 0; index < notes.length; index += 1) {
          if (buildDedupKeys(notes[index]).indexOf(key) !== -1) {
            return { index: index, note: notes[index], key: key };
          }
        }
      }
      return null;
    }

    _updateByClientId(clientNoteId, updater) {
      var notes = this._read();
      var index = notes.findIndex(function (note) {
        return note.client_note_id === clientNoteId;
      });
      if (index === -1) {
        return null;
      }
      notes[index] = updater(Object.assign({}, notes[index]));
      this._write(notes);
      return Object.assign({}, notes[index]);
    }

    _read() {
      var raw = this.storage.getItem(this.storageKey);
      if (!raw) {
        return [];
      }
      try {
        var parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
      } catch (error) {
        return [];
      }
    }

    _write(notes) {
      this.storage.setItem(this.storageKey, JSON.stringify(notes));
    }
  }

  ns.InspirationStore = InspirationStore;
  ns.createLocalPendingStorage = createLocalPendingStorage;
  ns.buildDedupKeys = buildDedupKeys;
})(NotebookAIInspiration);
