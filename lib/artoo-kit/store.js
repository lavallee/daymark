/* ArtooStore protocol 2: compare-and-swap disk saves, explicit local import.
 * A durable save is a filesystem acknowledgement, never a Git commit/push.
 * Await load(name) before enabling editing; surface status and caught errors.
 */
(function (global) {
  "use strict";
  var script = global.document && global.document.currentScript;
  var scriptRoot = script && /\/lib\/artoo-kit\/store\.js(?:[?#].*)?$/.test(script.src)
    ? script.src.replace(/lib\/artoo-kit\/store\.js(?:[?#].*)?$/, "") : null;
  var meta = global.document && global.document.querySelector('meta[name="artoo-state-base"]');
  var valid = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  function nameOK(name) {
    if (!valid.test(name)) throw new Error("Invalid state document or collection name");
  }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  async function request(method, url, body) {
    var opts = { method: method, cache: "no-store", credentials: "same-origin",
      headers: { Accept: "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    var res = await global.fetch(url, opts);
    var payload = res.status === 204 ? null : await res.json().catch(function () { return null; });
    if (!res.ok) {
      var err = new Error(res.status === 409 || res.status === 412
        ? "Shared data changed. Your pending edit is preserved; compare before retrying."
        : "State service request failed (" + res.status + "). Your edit is not confirmed saved.");
      err.status = res.status; err.payload = payload; throw err;
    }
    return payload;
  }
  function Store(collection, opts) {
    nameOK(collection); opts = opts || {};
    this.collection = collection;
    this.localOnly = opts.localOnly === true || global.location.protocol === "file:";
    var raw = opts.base || (meta && meta.content) ||
      (scriptRoot ? new URL("_artoo/state", scriptRoot).href : "./_artoo/state");
    var url = new URL(raw, global.location.href);
    if (!this.localOnly && url.origin !== global.location.origin)
      throw new Error("The state endpoint must be same-origin");
    this.base = url.href.replace(/\/$/, "");
    this.durable = false; this.status = "unloaded";
    this.revisions = Object.create(null); this._busy = Object.create(null);
    this._probed = null;
  }
  Store.prototype._url = function (name) {
    if (name !== undefined) nameOK(name);
    return this.base + "/" + encodeURIComponent(this.collection) +
      (name === undefined ? "" : "/" + encodeURIComponent(name));
  };
  Store.prototype._localKey = function (name) {
    nameOK(name); return "artoo:state:" + this.collection + ":" + name;
  };
  Store.prototype._pendingKey = function (name) {
    nameOK(name); return "artoo:pending:v2:" + this.base + ":" + this.collection + ":" + name;
  };
  Store.prototype.inspectLocal = function (name) {
    var raw = global.localStorage.getItem(this._localKey(name));
    return raw === null ? null : { document: JSON.parse(raw), source: "browser", durable: false };
  };
  Store.prototype.listPending = function (name) {
    var prefix = this._pendingKey(name), out = [];
    for (var i = 0; i < global.localStorage.length; i++) {
      var key = global.localStorage.key(i);
      if (key === prefix || (key && key.indexOf(prefix + ":") === 0)) {
        var raw = global.localStorage.getItem(key);
        if (raw !== null) {
          var value = JSON.parse(raw);
          value.pending_id = key.slice(prefix.length).replace(/^:/, "");
          out.push(value);
        }
      }
    }
    return out.sort(function (a, b) { return a.created.localeCompare(b.created); });
  };
  Store.prototype.inspectPending = function (name, id) {
    var candidates = this.listPending(name);
    return (id === undefined ? candidates[0] : candidates.find(function (p) {
      return p.pending_id === id;
    })) || null;
  };
  Store.prototype.discardPending = function (name, id) {
    if (this._busy[name]) throw new Error("Wait for the current save to finish");
    var pending = this.inspectPending(name, id);
    if (pending) global.localStorage.removeItem(this._pendingKey(name) +
      (pending.pending_id ? ":" + pending.pending_id : ""));
  };
  Store.prototype.probe = function () {
    var self = this;
    if (this.localOnly) return Promise.resolve(false);
    if (!this._probed) this._probed = request("GET", this._url()).then(function (r) {
      if (!r || r.protocol !== 2 || r.durable !== true)
        throw new Error("State service protocol 2 is required; no browser fallback was used");
      self.durable = true; return true;
    }).catch(function (err) {
      self._probed = null; self.status = "unavailable"; throw err;
    });
    return this._probed;
  };
  Store.prototype.list = async function () {
    if (await this.probe()) {
      var response = await request("GET", this._url());
      return response.entries || response.documents || [];
    }
    var prefix = "artoo:state:" + this.collection + ":", out = [];
    for (var i = 0; i < global.localStorage.length; i++) {
      var key = global.localStorage.key(i);
      if (key && key.indexOf(prefix) === 0)
        out.push({ name: key.slice(prefix.length), durable: false });
    }
    return out;
  };
  Store.prototype.load = async function (name) {
    nameOK(name);
    if (this._busy[name]) throw new Error("Wait for the current save to finish");
    if (!(await this.probe())) {
      var local = this.inspectLocal(name); this.revisions[name] = null;
      this.status = "browser-only"; return local ? local.document : null;
    }
    try {
      var r = await request("GET", this._url(name));
      if (!r || typeof r.revision !== "string") throw new Error("Missing state revision");
      this.revisions[name] = r.revision; this.status = "loaded";
      return r.document;
    } catch (err) {
      if (err.status !== 404) { this.status = "unavailable"; throw err; }
      this.revisions[name] = null; this.status = "loaded"; return null;
    }
  };
  Store.prototype._write = async function (name, pending, recovery) {
    nameOK(name);
    if (this._busy[name]) throw new Error("Wait for the current save to finish");
    if (!Object.prototype.hasOwnProperty.call(this.revisions, name))
      throw new Error("Load shared state before editing or saving");
    if (!recovery && this.inspectPending(name))
      throw new Error("A pending edit exists. Compare and recover or explicitly discard it first");
    this._busy[name] = true;
    if (pending.pending_id === undefined) pending.pending_id =
      global.crypto && global.crypto.randomUUID ? global.crypto.randomUUID() :
        Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    var journalKey = this._pendingKey(name) + (pending.pending_id ? ":" + pending.pending_id : "");
    try {
      // Refuse a new write if its recovery copy cannot be retained locally.
      global.localStorage.setItem(journalKey, JSON.stringify(pending));
      this.status = "saving";
      await this.probe();
      var r = await request(pending.method, this._url(name), pending.body);
      if (pending.method !== "DELETE" && (!r || typeof r.revision !== "string"))
        throw new Error("Save response omitted revision; verify shared data before recovery");
      this.revisions[name] = pending.method === "DELETE" ? null : r.revision;
      // Another tab can create its own journal while this request is in flight.
      // Only remove the exact operation acknowledged here.
      if (global.localStorage.getItem(journalKey) === JSON.stringify(pending))
        global.localStorage.removeItem(journalKey);
      this.status = "saved";
      return Object.assign({}, r || {}, { name: name, durable: true, git_committed: false });
    } catch (err) {
      this.status = err.status === 409 || err.status === 412 ? "conflict" : "unsaved";
      throw err;
    } finally { delete this._busy[name]; }
  };
  Store.prototype.save = async function (name, document_) {
    nameOK(name);
    if (!Object.prototype.hasOwnProperty.call(this.revisions, name))
      throw new Error("Load shared state before editing or saving");
    if (this.localOnly) {
      global.localStorage.setItem(this._localKey(name), JSON.stringify(document_));
      this.status = "browser-only"; return { name: name, durable: false, git_committed: false };
    }
    return this._write(name, { method: "PUT", created: new Date().toISOString(),
      body: { document: clone(document_), expected_revision: this.revisions[name] } }, false);
  };
  Store.prototype.remove = async function (name) {
    nameOK(name);
    if (this.localOnly) {
      global.localStorage.removeItem(this._localKey(name)); this.status = "browser-only";
      return { name: name, durable: false, git_committed: false };
    }
    return this._write(name, { method: "DELETE", created: new Date().toISOString(),
      body: { expected_revision: this.revisions[name] } }, false);
  };
  Store.prototype.importLocal = async function (name, options) {
    if (this.localOnly) throw new Error("Browser import requires the shared state service");
    var local = this.inspectLocal(name);
    if (!local) throw new Error("No browser copy exists to import");
    if (!options || options.confirm !== true || !Object.prototype.hasOwnProperty.call(options, "document"))
      throw new Error("Compare browser and shared copies, then supply confirmed merged document");
    // Save uses the revision from load; the legacy browser source remains intact.
    return this.save(name, options.document);
  };
  Store.prototype.resolvePending = async function (name, options) {
    if (this.localOnly) throw new Error("Pending resolution requires the shared state service");
    var previous = this.inspectPending(name, options && options.pending_id);
    if (!previous) throw new Error("No pending edit exists");
    if (!options || options.confirm !== true || !Object.prototype.hasOwnProperty.call(options, "document"))
      throw new Error("Compare pending and shared copies, then supply confirmed merged document");
    // Keep the original until the new, independently journaled merge is acknowledged.
    var previousKey = this._pendingKey(name) + (previous.pending_id ? ":" + previous.pending_id : "");
    var previousRaw = global.localStorage.getItem(previousKey);
    var result = await this._write(name, { method: "PUT", created: new Date().toISOString(),
      body: { document: clone(options.document), expected_revision: this.revisions[name] } }, true);
    if (global.localStorage.getItem(previousKey) === previousRaw)
      global.localStorage.removeItem(previousKey);
    return result;
  };
  Store.prototype.recoverPending = async function (name, id) {
    var pending = this.inspectPending(name, id);
    if (!pending) throw new Error("No pending edit exists");
    if (this.localOnly) throw new Error("Recovery requires the shared state service");
    if (pending.method !== "PUT" && pending.method !== "DELETE") throw new Error("Invalid pending edit");
    return this._write(name, pending, true);
  };
  Store.prototype.describe = function () {
    if (this.status === "saved") return "Saved to this artifact on disk. Not committed or pushed to Git.";
    if (this.localOnly) return "Browser only. This data has not been saved to the shared artifact.";
    if (this.status === "saving") return "Saving to the shared artifact…";
    if (this.status === "conflict") return "Shared data changed. Your pending edit is preserved for comparison.";
    if (this.status === "unsaved" || this.status === "unavailable")
      return "Save not confirmed. Check the connection and inspect pending edits before retrying.";
    return "Shared state service; no save has been confirmed in this session.";
  };
  global.ArtooStore = { open: function (collection, opts) { return new Store(collection, opts); }, Store: Store };
})(typeof window !== "undefined" ? window : this);
