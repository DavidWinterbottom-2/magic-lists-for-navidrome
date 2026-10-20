/* =========================================================================
   winterbottom.xyz — shared theme toggle
   Canonical source: docker-infra/design-system/winterbottom-theme.js
   Vendored into each app. Load in <head> (NOT deferred) so the saved theme
   is applied before first paint and there's no flash.

   Behaviour: with no saved choice the page follows the device (prefers-color-
   scheme, handled entirely in CSS). A toggle stores an explicit 'light'/'dark'
   under localStorage 'wb-theme', which wins via :root[data-theme=...].
   Wire a button with  data-wb-theme-toggle  to flip it.

   Text size: buttons with  data-wb-text-size="small|normal|large"  (inside a
   <details class="wb-textsize">) set html[data-text-size], saved per device
   under localStorage 'wb-text-size' and applied here before first paint.
   ========================================================================= */
(function () {
  var KEY = "wb-theme";
  var root = document.documentElement;

  function systemDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function current() {
    return root.getAttribute("data-theme") || (systemDark() ? "dark" : "light");
  }
  function apply(theme) {
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
    sync();
  }
  function sync() {
    var dark = current() === "dark";
    var els = document.querySelectorAll("[data-wb-theme-toggle]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      el.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      var icon = el.querySelector("[data-wb-theme-icon]");
      if (icon) icon.textContent = dark ? "☾" : "☀"; /* ☾ / ☀ */
    }
  }

  /* Apply saved choice as early as possible. */
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  } catch (e) {}

  /* Text size, also before first paint. 'text-size' is the chores app's old key. */
  var TS_KEY = "wb-text-size";
  function savedTextSize() {
    try { return localStorage.getItem(TS_KEY) || localStorage.getItem("text-size"); } catch (e) { return null; }
  }
  function applyTextSize(size) {
    if (size === "small" || size === "large") root.setAttribute("data-text-size", size);
    else root.removeAttribute("data-text-size");
  }
  applyTextSize(savedTextSize());
  function syncTextSize() {
    var current = root.getAttribute("data-text-size") || "normal";
    var opts = document.querySelectorAll("[data-wb-text-size]");
    for (var i = 0; i < opts.length; i++) {
      opts[i].setAttribute("aria-pressed", String(opts[i].getAttribute("data-wb-text-size") === current));
    }
  }
  function setTextSize(size) {
    applyTextSize(size);
    try { localStorage.setItem(TS_KEY, size); localStorage.removeItem("text-size"); } catch (e) {}
    syncTextSize();
  }

  function toggle() {
    var next = current() === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch (e) {}
    apply(next);
  }

  function init() {
    sync();
    syncTextSize();
    document.addEventListener("click", function (ev) {
      var t = ev.target.closest ? ev.target.closest("[data-wb-theme-toggle]") : null;
      if (t) { ev.preventDefault(); toggle(); return; }
      var ts = ev.target.closest ? ev.target.closest("[data-wb-text-size]") : null;
      if (ts) {
        setTextSize(ts.getAttribute("data-wb-text-size"));
        var menu = ts.closest("details");
        if (menu) menu.open = false;
        return;
      }
      /* A click anywhere else closes an open text-size menu. */
      var open = document.querySelectorAll("details.wb-textsize[open]");
      for (var i = 0; i < open.length; i++) if (!open[i].contains(ev.target)) open[i].open = false;
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.wbTheme = { toggle: toggle, apply: apply, current: current, setTextSize: setTextSize, syncTextSize: syncTextSize };
})();
