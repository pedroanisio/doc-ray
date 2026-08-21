/* Transient confirmation, announced rather than only shown.
 *
 * A toast exists for one case: an action whose result is not visible on the
 * page it happened on. Anything the user has to re-read, act on, or copy stays
 * in the document as a `.notice` / `.errbox` — a message that disappears on a
 * timer cannot be a message that matters.
 *
 * Announcement is the reason this is a module rather than an appended div. The
 * live region is declared in base.html and is already in the accessibility tree
 * before any text lands in it; a region created at the same moment as its
 * content is frequently not announced at all. Text is therefore inserted into a
 * region that already exists, and only the text node changes.
 *
 * An error toast does not auto-dismiss. Timing out a failure message is the one
 * case where the pattern actively loses information.
 *
 * Exposes `window.docrayToast(message, kind)` with kind in
 * {"info","ok","error"}. Returns the toast element, or null if the region is
 * absent (a page that does not extend base.html).
 */
(function () {
  "use strict";

  var DISMISS_MS = 5200;
  var MARK = { info: "i-check-circle", ok: "i-check-circle", error: "i-alert-triangle" };

  function build(message, kind) {
    var el = document.createElement("div");
    el.className = "toast toast--" + kind;

    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ico");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    // Same-document reference into the inlined sprite, so the mark inherits the
    // toast's own colour like every other icon on the page.
    use.setAttribute("href", "#" + (MARK[kind] || MARK.info));
    svg.appendChild(use);
    el.appendChild(svg);

    var text = document.createElement("span");
    text.className = "toast__text";
    // textContent, never innerHTML: a toast routinely carries a filename or a
    // server error string, and neither is trusted markup.
    text.textContent = message;
    el.appendChild(text);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast__close";
    close.setAttribute("aria-label", "Dismiss");
    var csvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    csvg.setAttribute("class", "ico ico--sm");
    csvg.setAttribute("viewBox", "0 0 24 24");
    csvg.setAttribute("aria-hidden", "true");
    csvg.setAttribute("focusable", "false");
    var cuse = document.createElementNS("http://www.w3.org/2000/svg", "use");
    cuse.setAttribute("href", "#i-x");
    csvg.appendChild(cuse);
    close.appendChild(csvg);
    el.appendChild(close);

    return el;
  }

  function dismiss(el) {
    if (!el || !el.parentNode) return;
    el.classList.add("toast--leaving");
    var drop = function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    };
    // Fall back to a timer: an element whose animation is suppressed by
    // prefers-reduced-motion never fires animationend, and would otherwise stay
    // on screen forever.
    var done = false;
    el.addEventListener("animationend", function () {
      done = true;
      drop();
    });
    window.setTimeout(function () {
      if (!done) drop();
    }, 400);
  }

  window.docrayToast = function (message, kind) {
    var region = document.getElementById("toast-region");
    if (!region || !message) return null;
    kind = kind === "ok" || kind === "error" ? kind : "info";

    var el = build(String(message), kind);
    el.querySelector(".toast__close").addEventListener("click", function () {
      dismiss(el);
    });
    region.appendChild(el);

    if (kind !== "error") {
      window.setTimeout(function () {
        dismiss(el);
      }, DISMISS_MS);
    }
    return el;
  };
})();
