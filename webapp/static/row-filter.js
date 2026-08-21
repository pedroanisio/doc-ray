/* Narrow the rows already on screen.
 *
 * This is deliberately NOT search, and the distinction is the whole design.
 * The corpus is paginated, so a client-side filter can only ever see one page
 * of it. A control that looked like search but silently searched 1/Nth of the
 * corpus would be worse than no control at all — it would return "no matches"
 * for documents that exist. So: the placeholder says "this page", the live
 * count states how many *loaded* rows matched, and the real corpus search is a
 * separate destination in the nav.
 *
 * Progressive enhancement: with JS off the input simply does nothing and every
 * row stays visible, which is the correct fallback for a filter.
 *
 * Markup contract:
 *   [data-row-filter]        the text input
 *   [data-row-filter-count]  a live region for the match count (optional)
 *   [data-row]               each filterable row, anywhere in the same <form>
 *                            or, failing that, the document
 */
(function () {
  "use strict";

  function textOf(row) {
    // Cached per row: the row's text does not change, and re-reading
    // textContent for every keystroke walks the whole subtree each time.
    if (row.__filterText === undefined) {
      row.__filterText = (row.textContent || "").toLowerCase().replace(/\s+/g, " ");
    }
    return row.__filterText;
  }

  function wire(input) {
    var scope = input.closest("form") || document;
    var rows = Array.prototype.slice.call(scope.querySelectorAll("[data-row]"));
    var count = scope.querySelector("[data-row-filter-count]");
    if (!rows.length) return;

    var apply = function () {
      var q = input.value.trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (row) {
        var match = !q || textOf(row).indexOf(q) !== -1;
        // `hidden` rather than display:none so the row leaves the
        // accessibility tree too — a filtered-out row is not "visually
        // hidden", it is out of the list.
        row.hidden = !match;
        if (match) shown += 1;
      });
      if (count) {
        count.textContent = q
          ? shown + " of " + rows.length + " on this page"
          : rows.length + " on this page";
      }
    };

    input.addEventListener("input", apply);
    // Escape clears, which is the expected exit from a filter box.
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && input.value) {
        event.stopPropagation();
        input.value = "";
        apply();
      }
    });
    apply();
  }

  function init() {
    var inputs = document.querySelectorAll("[data-row-filter]");
    Array.prototype.forEach.call(inputs, wire);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
