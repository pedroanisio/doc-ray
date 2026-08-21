/* Drag-and-drop, several files at once, and progress per file.
 *
 * The form underneath works without any of this: one file, a normal multipart
 * POST to /upload, a redirect to the job page. What a form post cannot do is
 * report progress or partial failure — a multipart submission is one navigation
 * that either lands or does not, so "file 3 of 5 was rejected, the other four
 * are ingesting" is unrepresentable. That is the whole reason this module and
 * the /upload/file JSON route exist.
 *
 * Design decisions worth keeping:
 *
 *   * Uploads run one at a time. Each request holds an API worker for the
 *     length of an ingest; firing five multipart bodies in parallel makes every
 *     one of them slower and makes the progress bars lie about which is moving.
 *   * XMLHttpRequest, not fetch. fetch() has no upload progress event, and
 *     progress is the point.
 *   * A typed title is only applied when exactly one file is queued. Applying
 *     one title to five documents would name them all the same, which is worse
 *     than leaving each document its own metadata.
 *   * A reference (the `ref` field) is never intercepted. There is no upload to
 *     report progress on, and the worker does the fetch, so it goes down the
 *     plain form path.
 *   * Navigation only happens on an unambiguous single success. With several
 *     files, leaving the page would throw away the only report of what
 *     happened.
 */
(function () {
  "use strict";

  var ENDPOINT = "/upload/file";

  function humanSize(bytes) {
    if (!bytes) return "0 B";
    var units = ["B", "KB", "MB", "GB"];
    var i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    var n = bytes / Math.pow(1024, i);
    return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + " " + units[i];
  }

  function iconSvg(symbol, extra) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ico" + (extra ? " " + extra : ""));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + symbol);
    svg.appendChild(use);
    return svg;
  }

  function Uploader(form) {
    this.form = form;
    this.zone = form.querySelector("[data-dropzone]");
    this.input = form.querySelector("[data-upload-input]");
    this.queue = form.querySelector("[data-upload-queue]");
    this.submit = form.querySelector("[data-upload-submit]");
    this.items = [];
    this.busy = false;

    if (!this.zone || !this.input || !this.queue) return;

    // Only now that JS is driving does more than one file make sense: the form
    // path posts a single `file` part.
    this.input.multiple = true;

    this.input.addEventListener("change", this.onPick.bind(this));
    ["dragenter", "dragover"].forEach(
      function (type) {
        this.zone.addEventListener(
          type,
          function (event) {
            event.preventDefault();
            this.zone.setAttribute("data-drag", "over");
          }.bind(this)
        );
      }.bind(this)
    );
    ["dragleave", "dragend", "drop"].forEach(
      function (type) {
        this.zone.addEventListener(
          type,
          function () {
            this.zone.removeAttribute("data-drag");
          }.bind(this)
        );
      }.bind(this)
    );
    this.zone.addEventListener("drop", this.onDrop.bind(this));
    this.form.addEventListener("submit", this.onSubmit.bind(this));
  }

  Uploader.prototype.onPick = function () {
    this.enqueue(this.input.files);
  };

  Uploader.prototype.onDrop = function (event) {
    event.preventDefault();
    if (event.dataTransfer && event.dataTransfer.files) {
      this.enqueue(event.dataTransfer.files);
    }
  };

  Uploader.prototype.enqueue = function (files) {
    Array.prototype.forEach.call(
      files,
      function (file) {
        this.items.push(this.render(file));
      }.bind(this)
    );
  };

  Uploader.prototype.render = function (file) {
    var li = document.createElement("li");
    li.className = "upload-item";

    var mark = iconSvg("i-file-text");
    li.appendChild(mark);

    var name = document.createElement("span");
    name.className = "upload-item__name";
    name.textContent = file.name;
    li.appendChild(name);

    var size = document.createElement("span");
    size.className = "upload-item__size";
    size.textContent = humanSize(file.size);
    li.appendChild(size);

    var drop = document.createElement("button");
    drop.type = "button";
    drop.className = "upload-item__drop";
    drop.setAttribute("aria-label", "Remove " + file.name + " from the queue");
    drop.appendChild(iconSvg("i-x", "ico--sm"));
    li.appendChild(drop);

    var item = { file: file, li: li, mark: mark, state: null, bar: null };
    drop.addEventListener(
      "click",
      function () {
        var at = this.items.indexOf(item);
        if (at !== -1) this.items.splice(at, 1);
        if (li.parentNode) li.parentNode.removeChild(li);
      }.bind(this)
    );

    this.queue.appendChild(li);
    return item;
  };

  Uploader.prototype.setState = function (item, text, kind) {
    if (!item.state) {
      item.state = document.createElement("span");
      item.li.insertBefore(item.state, item.li.querySelector(".upload-item__drop"));
    }
    item.state.className =
      "upload-item__state" + (kind ? " upload-item__state--" + kind : "");
    item.state.textContent = text;
  };

  Uploader.prototype.options = function () {
    var data = new FormData();
    var single = this.items.length === 1;
    var fields = ["csrf_token", "pipeline", "patent", "jurisdiction"];
    fields.forEach(
      function (field) {
        var el = this.form.elements[field];
        if (!el) return;
        if (el.type === "checkbox") {
          if (el.checked) data.append(field, "on");
        } else if (el.value) {
          data.append(field, el.value);
        }
      }.bind(this)
    );
    // One typed title cannot describe five documents; each keeps its own
    // extracted metadata instead.
    var title = this.form.elements.title;
    if (single && title && title.value) data.append("title", title.value);
    return data;
  };

  Uploader.prototype.send = function (item) {
    return new Promise(
      function (resolve) {
        var data = this.options();
        data.append("file", item.file, item.file.name);

        var bar = document.createElement("progress");
        bar.max = 100;
        bar.value = 0;
        item.li.appendChild(bar);
        item.bar = bar;
        this.setState(item, "uploading");

        var xhr = new XMLHttpRequest();
        xhr.open("POST", ENDPOINT, true);
        xhr.upload.addEventListener("progress", function (event) {
          if (event.lengthComputable) bar.value = (event.loaded / event.total) * 100;
        });
        xhr.addEventListener(
          "load",
          function () {
            var body = {};
            try {
              body = JSON.parse(xhr.responseText || "{}");
            } catch (err) {
              body = {};
            }
            if (xhr.status >= 200 && xhr.status < 300 && body.jobId) {
              bar.value = 100;
              this.succeed(item, body);
              resolve(true);
            } else {
              this.fail(item, body.error || "the server rejected this file");
              resolve(false);
            }
          }.bind(this)
        );
        xhr.addEventListener(
          "error",
          function () {
            this.fail(item, "the upload could not reach the server");
            resolve(false);
          }.bind(this)
        );
        xhr.send(data);
      }.bind(this)
    );
  };

  Uploader.prototype.succeed = function (item, body) {
    item.jobUrl = body.job;
    this.setState(item, "queued", "done");
    item.mark.replaceWith(iconSvg("i-check-circle"));
    if (item.bar) item.bar.remove();
    // The job page is where the pipeline is watched, so the row becomes a link
    // to it rather than a dead-end confirmation.
    if (body.job) {
      var link = document.createElement("a");
      link.href = body.job;
      link.className = "upload-item__name";
      link.textContent = item.file.name;
      var name = item.li.querySelector(".upload-item__name");
      if (name) name.replaceWith(link);
    }
  };

  Uploader.prototype.fail = function (item, message) {
    this.setState(item, "failed", "failed");
    item.mark.replaceWith(iconSvg("i-x-circle"));
    if (item.bar) item.bar.remove();
    item.error = message;
    if (window.docrayToast) window.docrayToast(item.file.name + ": " + message, "error");
  };

  Uploader.prototype.onSubmit = function (event) {
    var ref = this.form.elements.ref;
    // A reference has no upload to report on and the worker does the fetch, so
    // it takes the plain form path untouched.
    if (ref && ref.value.trim()) return;
    if (!this.items.length || this.busy) return;

    event.preventDefault();
    this.busy = true;
    if (this.submit) this.submit.disabled = true;

    var queued = this.items.slice();
    var results = [];
    var step = function (index) {
      if (index >= queued.length) return this.finish(queued, results);
      return this.send(queued[index]).then(
        function (ok) {
          results.push(ok);
          return step(index + 1);
        }.bind(this)
      );
    }.bind(this);
    step(0);
  };

  Uploader.prototype.finish = function (queued, results) {
    this.busy = false;
    if (this.submit) this.submit.disabled = false;
    var ok = results.filter(Boolean).length;
    var failed = results.length - ok;

    if (ok === 1 && failed === 0 && queued.length === 1 && queued[0].jobUrl) {
      // Same destination the no-JS path reaches, for the same single-file case.
      window.location.href = queued[0].jobUrl;
      return;
    }
    if (ok && window.docrayToast) {
      window.docrayToast(
        ok + (ok === 1 ? " document" : " documents") + " queued for ingest",
        "ok"
      );
    }
    // Anything that failed keeps its row and its message; the queue is the
    // report, so it is not cleared.
    this.items = this.items.filter(function (item) {
      return !item.jobUrl;
    });
  };

  function init() {
    var forms = document.querySelectorAll("[data-upload-form]");
    Array.prototype.forEach.call(forms, function (form) {
      new Uploader(form);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
