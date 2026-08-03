/* =========================================================================
   winterbottom.xyz — shared theme toggle
   Canonical source: docker-infra/design-system/winterbottom-theme.js
   Vendored into each app. Load in <head> (NOT deferred) so the saved theme
   is applied before first paint and there's no flash.

   Behaviour: with no saved choice the page follows the device (prefers-color-
   scheme, handled entirely in CSS). A toggle stores an explicit 'light'/'dark'
   under localStorage 'wb-theme', which wins via :root[data-theme=...].
   Wire a button with  data-wb-theme-toggle  to flip it.
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

  function toggle() {
    var next = current() === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch (e) {}
    apply(next);
  }

  function init() {
    sync();
    document.addEventListener("click", function (ev) {
      var t = ev.target.closest ? ev.target.closest("[data-wb-theme-toggle]") : null;
      if (t) { ev.preventDefault(); toggle(); }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.wbTheme = { toggle: toggle, apply: apply, current: current };
})();
