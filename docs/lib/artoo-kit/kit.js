/* artoo-kit helpers: theme toggle, mobile nav, legacy mermaid.js boot.
   No dependencies; safe to include on every page. */
(function () {
  "use strict";

  // Theme: honor a saved choice; the toggle cycles dark -> light -> dark.
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem("artoo-theme"); } catch (e) { /* file:// */ }
  if (!root.hasAttribute("data-theme") && (saved === "light" || saved === "dark")) {
    root.setAttribute("data-theme", saved);
  }

  function currentTheme() {
    var explicit = root.getAttribute("data-theme");
    if (explicit) return explicit;
    return "light";
  }

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-theme-toggle]");
    if (toggle) {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("artoo-theme", next); } catch (e) { /* ignore */ }
      return;
    }
    var navToggle = event.target.closest(".nav-toggle");
    if (navToggle) {
      var links = document.querySelector(".site-nav .nav-links");
      if (links) {
        var open = links.classList.toggle("open");
        navToggle.setAttribute("aria-expanded", open ? "true" : "false");
      }
    }
  });

  // Loops (.article-loop): silent, autoplaying figures. Readers who ask for less
  // motion get them paused with controls; a click toggles play for everyone.
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  Array.prototype.forEach.call(document.querySelectorAll("video.article-loop"), function (v) {
    if (reduce) { v.removeAttribute("autoplay"); v.autoplay = false; v.pause(); v.controls = true; }
    v.addEventListener("click", function () { if (v.controls) return; v.paused ? v.play() : v.pause(); });
  });

  // Mermaid: artifacts that opted into mermaid.js (artoo-mermaid) still render
  // .mermaid blocks here. artoo-diagrams renders the same blocks with Pele;
  // when both are loaded, Pele owns them so nothing is drawn twice.
  if (window.mermaid && !window.pele) {
    window.mermaid.initialize({
      startOnLoad: true,
      theme: currentTheme() === "dark" ? "dark" : "neutral",
      securityLevel: "loose",
    });
  }

  // Mark the current page in the nav.
  var here = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".site-nav .nav-links a").forEach(function (a) {
    var target = a.getAttribute("href").split("/").pop();
    if (target === here) a.setAttribute("aria-current", "page");
  });
})();
