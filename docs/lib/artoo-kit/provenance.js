/* artoo-kit provenance panel + claim anchors.

   Data-driven from flip's flip-render/1 projection (site/data/provenance.json,
   written by `artoo build` / `artoo provenance`). Self-contained, no CDN, and
   progressive: with no data the page renders exactly as authored — an empty
   panel simply hides itself.

   Data resolution, file://-safe first:
     1. window.__ARTOO_PROVENANCE__  (site/data/provenance.js, offline loader)
     2. inline  <script type="application/json" id="artoo-provenance-data">
     3. fetch(panel[data-artoo-provenance] || "data/provenance.json")  (http)

   A panel is any element carrying [data-artoo-provenance]. Claim/source ids in
   prose ([C7], [A3]) that exist in the data become stable anchors linking to
   their panel entry — only ids the data knows, and never inside code. */
(function () {
  "use strict";

  var GRADE_ORDER = ["A", "B", "C", "?"];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function inlineData() {
    if (window.__ARTOO_PROVENANCE__) return Promise.resolve(window.__ARTOO_PROVENANCE__);
    var tag = document.getElementById("artoo-provenance-data");
    if (tag && tag.textContent.trim()) {
      try { return Promise.resolve(JSON.parse(tag.textContent)); } catch (e) { /* fall through */ }
    }
    return null;
  }

  function loadData(panel) {
    var inline = inlineData();
    if (inline) return inline;
    var src = panel.getAttribute("data-artoo-provenance") || "data/provenance.json";
    if (typeof fetch !== "function") return Promise.reject();
    return fetch(src).then(function (r) {
      if (!r.ok) throw new Error("provenance " + r.status);
      return r.json();
    });
  }

  function gradeBadge(grade) {
    var b = el("span", "badge provenance__grade", (grade || "?"));
    b.setAttribute("data-grade", grade || "?");
    b.title = "source grade " + (grade || "unjudged");
    return b;
  }

  function statusBadge(status) {
    var cls = "badge provenance__status";
    if (status === "verified") cls += " badge--success";
    else if (status === "needs-2nd" || status === "asserted") cls += " badge--warn";
    var b = el("span", cls, status || "asserted");
    b.setAttribute("data-status", status || "asserted");
    return b;
  }

  function counts(data) {
    var sources = data.sources || [];
    var claims = data.claims || [];
    var byGrade = {};
    sources.forEach(function (s) {
      var g = s.grade || "?";
      byGrade[g] = (byGrade[g] || 0) + 1;
    });
    var gradeParts = GRADE_ORDER.filter(function (g) { return byGrade[g]; })
      .map(function (g) { return byGrade[g] + " " + g; });
    var loadBearing = claims.filter(function (c) { return c.load_bearing; }).length;
    var row = el("div", "provenance__counts");
    function stat(value, label) {
      var s = el("div", "provenance__stat");
      s.appendChild(el("span", "provenance__stat-value", String(value)));
      s.appendChild(el("span", "provenance__stat-label", label));
      return s;
    }
    row.appendChild(stat(sources.length, gradeParts.length ? gradeParts.join(" · ") + " sources" : "sources"));
    row.appendChild(stat(claims.length, "claims"));
    row.appendChild(stat(loadBearing, "load-bearing"));
    return row;
  }

  function sourceList(data) {
    var sources = data.sources || [];
    if (!sources.length) return null;
    var wrap = el("div", "provenance__section");
    wrap.appendChild(el("h3", "provenance__heading", "Sources"));
    var ul = el("ul", "provenance__list");
    sources.forEach(function (s) {
      var li = el("li", "provenance__item");
      li.id = "prov-source-" + s.id;
      li.appendChild(gradeBadge(s.grade));
      var idSpan = el("span", "provenance__id", s.id);
      li.appendChild(idSpan);
      var title = s.title || s.slug || s.id;
      li.appendChild(el("span", "provenance__title", title));
      var meta = [s.independence, s.freshness, s.kind].filter(Boolean).join(" · ");
      if (meta) li.appendChild(el("span", "provenance__meta", meta));
      ul.appendChild(li);
    });
    wrap.appendChild(ul);
    return wrap;
  }

  function claimList(data) {
    var claims = data.claims || [];
    if (!claims.length) return null;
    var wrap = el("div", "provenance__section");
    wrap.appendChild(el("h3", "provenance__heading", "Claims"));
    var ul = el("ul", "provenance__list");
    claims.forEach(function (c) {
      var li = el("li", "provenance__item provenance__claim");
      li.id = "prov-claim-" + c.id;
      if (c.load_bearing) li.setAttribute("data-load-bearing", "true");
      li.appendChild(el("span", "provenance__id", c.id));
      li.appendChild(el("span", "provenance__title", c.text || ""));
      li.appendChild(statusBadge(c.status));
      if (c.load_bearing) {
        var lb = el("span", "badge provenance__lb", "load-bearing");
        li.appendChild(lb);
      }
      (c.verifications || []).forEach(function (v) {
        var vb = el("span", "badge badge--accent provenance__verify", v.method);
        vb.title = "verified by " + (v.by || "?") + (v.date ? " on " + v.date : "");
        li.appendChild(vb);
      });
      ul.appendChild(li);
    });
    wrap.appendChild(ul);
    return wrap;
  }

  function vintageLine(data) {
    var nb = data.notebook || {};
    var bits = [];
    if (nb.slug || nb.title) bits.push("Rendered from notebook " + (nb.title || nb.slug));
    if (nb.uid) bits.push(nb.uid);
    if (nb.updated) bits.push("updated " + nb.updated);
    var line = bits.join(" · ");
    if (data.generated) line += (line ? " · " : "") + "projection generated " + data.generated;
    return el("p", "provenance__vintage", line);
  }

  function render(panel, data) {
    panel.textContent = "";
    panel.classList.remove("provenance--empty");
    var header = el("div", "provenance__header");
    header.appendChild(el("h2", "provenance__title-heading", "Provenance"));
    header.appendChild(vintageLine(data));
    panel.appendChild(header);
    panel.appendChild(counts(data));
    var s = sourceList(data);
    if (s) panel.appendChild(s);
    var c = claimList(data);
    if (c) panel.appendChild(c);
  }

  // -- claim anchors ---------------------------------------------------------

  var SKIP = { CODE: 1, PRE: 1, A: 1, SCRIPT: 1, STYLE: 1, TEXTAREA: 1 };
  // Any bracketed flip-style id ([C7], [A3], [F1], [Q2]…); membership in the
  // projection is what actually gates a rewrite, so the prefix stays permissive.
  var TOKEN = /\[([A-Z]{1,3}\d+)\]/g;

  function knownIds(data) {
    var ids = {};
    (data.claims || []).forEach(function (c) { ids[c.id] = { kind: "claim", text: c.text, status: c.status }; });
    (data.sources || []).forEach(function (s) { ids[s.id] = { kind: "source", text: s.title || s.slug || s.id }; });
    return ids;
  }

  function anchorProse(root, data) {
    var ids = knownIds(data);
    var seen = {};
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue || node.nodeValue.indexOf("[") === -1) return NodeFilter.FILTER_REJECT;
        var p = node.parentNode;
        while (p && p !== root) {
          if (SKIP[p.nodeName]) return NodeFilter.FILTER_REJECT;
          p = p.parentNode;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    var targets = [];
    var n;
    while ((n = walker.nextNode())) {
      TOKEN.lastIndex = 0;
      if (TOKEN.test(n.nodeValue)) targets.push(n);
    }
    targets.forEach(function (node) {
      var frag = document.createDocumentFragment();
      var text = node.nodeValue;
      var last = 0;
      var m;
      TOKEN.lastIndex = 0;
      while ((m = TOKEN.exec(text))) {
        var id = m[1];
        if (!ids[id]) continue;
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var a = document.createElement("a");
        a.className = "claim-ref";
        a.href = "#prov-" + ids[id].kind + "-" + id;
        a.textContent = m[0];
        a.title = ids[id].text ? ids[id].text + (ids[id].status ? " (" + ids[id].status + ")" : "") : id;
        if (!seen[id]) { a.id = "claim-" + id; seen[id] = true; }
        frag.appendChild(a);
        last = m.index + m[0].length;
      }
      if (last === 0) return; // no known ids in this node
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
  }

  // -- boot ------------------------------------------------------------------

  function boot() {
    var panels = document.querySelectorAll("[data-artoo-provenance]");
    if (!panels.length) return;
    Promise.resolve(loadData(panels[0])).then(function (data) {
      if (!data) return;
      panels.forEach(function (panel) { render(panel, data); });
      var scope = document.querySelector("[data-claim-anchors]") || document.querySelector("main") || document.body;
      try { anchorProse(scope, data); } catch (e) { /* enhancement only */ }
    }).catch(function () {
      panels.forEach(function (panel) {
        if (!panel.textContent.trim()) panel.classList.add("provenance--empty");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
