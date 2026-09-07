// Multi-selection for tables wrapped in <form data-bulk>.
// - .bulk-all         : header checkbox, toggles every .bulk-item
// - .bulk-item        : per-row checkbox (its name/value carry the payload)
// - [data-bulk-action]: submit buttons, disabled while nothing is selected
// - .bulk-count       : element whose text shows the current selection count
// - [data-bulk-confirm]: on such a submit button, confirm() with %n replaced by the count
(function () {
  function wire(form) {
    var all = form.querySelector(".bulk-all");
    var actions = Array.prototype.slice.call(form.querySelectorAll("[data-bulk-action]"));
    var counter = form.querySelector(".bulk-count");

    function items() {
      return Array.prototype.slice.call(form.querySelectorAll(".bulk-item"));
    }
    function selected() {
      return items().filter(function (i) { return i.checked; });
    }
    function sync() {
      var n = selected().length;
      var total = items().length;
      actions.forEach(function (b) { b.disabled = n === 0; });
      if (counter) {
        counter.textContent = n
          ? n + " sélectionné" + (n > 1 ? "s" : "")
          : "Aucune sélection";
      }
      if (all) {
        all.checked = n > 0 && n === total;
        all.indeterminate = n > 0 && n < total;
      }
    }

    if (all) {
      all.addEventListener("change", function () {
        items().forEach(function (i) { i.checked = all.checked; });
        sync();
      });
    }
    form.addEventListener("change", function (e) {
      if (e.target && e.target.classList.contains("bulk-item")) sync();
    });
    form.addEventListener("submit", function (e) {
      var n = selected().length;
      if (n === 0) { e.preventDefault(); return; }
      var btn = e.submitter;
      if (btn && btn.hasAttribute("data-bulk-confirm")) {
        var msg = btn.getAttribute("data-bulk-confirm").replace(/%n/g, n);
        if (!window.confirm(msg)) e.preventDefault();
      }
    });

    sync();
  }

  document.querySelectorAll("form[data-bulk]").forEach(wire);
})();
