// Applies the saved theme (or the system preference on a first visit) before the first paint, so the
// page never flashes the wrong theme. A same-origin file because the server's CSP forbids inline
// scripts. Keep the key and logic in sync with src/theme.ts.
(function () {
  var theme = null;
  try {
    theme = window.localStorage.getItem("netscope-theme");
  } catch (e) {
    theme = null;
  }
  if (theme !== "light" && theme !== "dark") {
    var prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    theme = prefersLight ? "light" : "dark";
  }
  document.documentElement.setAttribute("data-theme", theme);
})();
