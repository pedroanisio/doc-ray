/* Type-to-narrow over a list that is already in the page.
 *
 * Why not a styled <select>: the useful operation here is *filtering* a list
 * that is as long as the corpus, and a native select cannot filter. Why not a
 * bespoke div soup: because a picker that cannot be driven from the keyboard is
 * not a picker for everyone. This implements the ARIA combobox pattern — the
 * input carries aria-expanded and aria-activedescendant, the list is a real
 * listbox of options, and Up/Down/Home/End/Enter/Escape all work — over markup
 * that is a plain list of single-purpose forms.
 *
 * That last point is the load-bearing one: each option contains its own form
 * with its own submit button, so with JS off the same markup is a scrolling
 * list of "Add" buttons and every action still works. Enter on a highlighted
 * option submits that option's form; it does not synthesize a click on
 * something that only exists in JS.
 *
 * Markup contract:
 *   [data-combobox]        wrapper
 *   [data-combobox-input]  the input (role=combobox, aria-controls -> list id)
 *   [data-combobox-list]   the listbox (role=listbox)
 *   [data-combobox-opt]    each option (role=option, id required, data-label
 *                          carries the text to match, and it contains a <form>)
 *   [data-combobox-empty]  the "no match" row (optional)
 */
(function () {
  "use strict";

  function Combobox(root) {
    this.root = root;
    this.input = root.querySelector("[data-combobox-input]");
    this.list = root.querySelector("[data-combobox-list]");
    this.options = Array.prototype.slice.call(root.querySelectorAll("[data-combobox-opt]"));
    this.empty = root.querySelector("[data-combobox-empty]");
    this.active = -1;
    this.visible = this.options.slice();

    if (!this.input || !this.list || !this.options.length) return;

    // The static fallback class is what makes the no-JS list readable in flow.
    // Once JS owns the list it becomes a floating popup, closed until asked for.
    this.list.classList.remove("combobox__list--static");
    this.close();

    this.input.addEventListener("input", this.onInput.bind(this));
    this.input.addEventListener("keydown", this.onKeyDown.bind(this));
    this.input.addEventListener("focus", this.open.bind(this));
    // A blur that lands inside the list is a click on an option, not an exit.
    document.addEventListener(
      "focusin",
      function (event) {
        if (!this.root.contains(event.target)) this.close();
      }.bind(this)
    );
    this.options.forEach(
      function (opt, index) {
        opt.addEventListener(
          "mousemove",
          function () {
            this.highlight(this.visible.indexOf(opt) === -1 ? -1 : this.visible.indexOf(opt));
          }.bind(this)
        );
        // Clicking anywhere on the row submits that row's form, so the whole
        // option is the target rather than just the small button inside it.
        opt.addEventListener(
          "click",
          function (event) {
            if (event.target.closest("button")) return;
            this.submit(opt);
          }.bind(this)
        );
      }.bind(this)
    );
  }

  Combobox.prototype.labelOf = function (opt) {
    if (opt.__label === undefined) {
      opt.__label = (opt.getAttribute("data-label") || opt.textContent || "").toLowerCase();
    }
    return opt.__label;
  };

  Combobox.prototype.onInput = function () {
    var q = this.input.value.trim().toLowerCase();
    this.visible = [];
    this.options.forEach(
      function (opt) {
        var match = !q || this.labelOf(opt).indexOf(q) !== -1;
        opt.hidden = !match;
        if (match) this.visible.push(opt);
      }.bind(this)
    );
    if (this.empty) this.empty.hidden = this.visible.length !== 0;
    this.open();
    // Highlighting the first match makes Enter mean "the obvious one", which is
    // the whole point of typing three characters.
    this.highlight(this.visible.length ? 0 : -1);
  };

  Combobox.prototype.open = function () {
    this.list.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  };

  Combobox.prototype.close = function () {
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.highlight(-1);
  };

  Combobox.prototype.highlight = function (index) {
    if (this.active >= 0 && this.visible[this.active]) {
      this.visible[this.active].setAttribute("aria-selected", "false");
    }
    this.active = index;
    if (index < 0 || !this.visible[index]) {
      this.input.removeAttribute("aria-activedescendant");
      return;
    }
    var opt = this.visible[index];
    opt.setAttribute("aria-selected", "true");
    // aria-activedescendant, not focus: focus stays in the input so the user can
    // keep typing while moving through the list.
    this.input.setAttribute("aria-activedescendant", opt.id);
    if (opt.scrollIntoView) opt.scrollIntoView({ block: "nearest" });
  };

  Combobox.prototype.move = function (delta) {
    if (!this.visible.length) return;
    this.open();
    var next = this.active + delta;
    if (next < 0) next = this.visible.length - 1;
    if (next >= this.visible.length) next = 0;
    this.highlight(next);
  };

  Combobox.prototype.submit = function (opt) {
    var form = opt.querySelector("form");
    if (form) form.submit();
  };

  Combobox.prototype.onKeyDown = function (event) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        this.move(1);
        break;
      case "ArrowUp":
        event.preventDefault();
        this.move(-1);
        break;
      case "Home":
        if (this.visible.length) {
          event.preventDefault();
          this.open();
          this.highlight(0);
        }
        break;
      case "End":
        if (this.visible.length) {
          event.preventDefault();
          this.open();
          this.highlight(this.visible.length - 1);
        }
        break;
      case "Enter":
        if (this.active >= 0 && this.visible[this.active]) {
          event.preventDefault();
          this.submit(this.visible[this.active]);
        }
        break;
      case "Escape":
        // First Escape closes the list; a second clears the query. Closing and
        // clearing in one keystroke loses the typed text on an accidental press.
        event.stopPropagation();
        if (this.list.hidden) {
          this.input.value = "";
          this.onInput();
        } else {
          this.close();
        }
        break;
      default:
        break;
    }
  };

  function init() {
    var roots = document.querySelectorAll("[data-combobox]");
    Array.prototype.forEach.call(roots, function (root) {
      new Combobox(root);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
