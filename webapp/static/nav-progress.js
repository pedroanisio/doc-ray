/* Navigation feedback for the server-rendered webapp.
 *
 * A full-page navigation blocks on the server before the browser paints the
 * next page, so a slow response reads as a frozen UI. This gives an INSTANT
 * response on the current page — a top progress bar plus a busy state on the
 * activated control — so the wait is acknowledged, never silent.
 *
 * No dependencies. Respects modified clicks, new-tab, downloads, same-page
 * anchors, and the back/forward cache. Paired with the CSS in design-system.css.
 */
(function () {
  "use strict";

  var bar = document.getElementById("nav-progress");
  if (!bar) return;

  var timer = null;
  var width = 0;
  var active = false;

  function set(w) {
    width = w;
    bar.style.width = w + "%";
  }

  function start() {
    if (active) return;
    active = true;
    bar.classList.add("on");
    set(8);
    // Trickle toward 90% and stall; the next page load replaces this document.
    timer = setInterval(function () {
      var step = width < 55 ? 7 : width < 80 ? 2 : 0.4;
      set(Math.min(90, width + step));
    }, 240);
  }

  function reset() {
    active = false;
    if (timer) clearInterval(timer);
    bar.classList.remove("on");
    set(0);
  }

  function markBusy(el) {
    if (!el) return;
    el.setAttribute("aria-busy", "true");
    el.classList.add("is-loading");
  }

  // Plain left-click on a same-origin, in-place navigation link.
  document.addEventListener(
    "click",
    function (e) {
      if (e.defaultPrevented || e.button !== 0) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var a = e.target.closest ? e.target.closest("a[href]") : null;
      if (!a) return;
      if (a.target === "_blank" || a.hasAttribute("download")) return;
      var raw = a.getAttribute("href") || "";
      if (raw === "" || raw.charAt(0) === "#" || raw.indexOf("javascript:") === 0) return;
      var url;
      try {
        url = new URL(a.href, location.href);
      } catch (_) {
        return;
      }
      if (url.origin !== location.origin) return;
      if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
      markBusy(a);
      start();
    },
    true
  );

  // Any in-place form submit (upload, sign in, delete, seed, …).
  document.addEventListener(
    "submit",
    function (e) {
      if (e.defaultPrevented) return;
      var f = e.target;
      if (f.getAttribute && f.getAttribute("target") === "_blank") return;
      var btn = f.querySelector("button[type=submit], input[type=submit], button:not([type])");
      markBusy(btn || f);
      start();
    },
    true
  );

  // Hide/reset when this page loads or is restored from the bfcache (Back button).
  window.addEventListener("pageshow", reset);
})();
