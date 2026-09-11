// Inline folder/object browser for a prefix or object-key <input>, wrapped in
// <div data-pathpicker data-browse-endpoint=".." data-account-select="#id" data-bucket-select="#id"
//      data-perm="download|upload" data-allow-objects="0|1">. Lists what's actually on the
// bucket (Delimiter="/", one level at a time — same bounded-scope principle as the explorer's
// own folder navigation) instead of asking the user to type a path by hand.
(function () {
  function wire(root) {
    var input = root.querySelector('input[type="text"]');
    var btn = root.querySelector('[data-browse-btn]');
    var panel = root.querySelector('[data-browse-panel]');
    var breadcrumb = root.querySelector('[data-breadcrumb]');
    var list = root.querySelector('[data-list]');
    var useBtn = root.querySelector('[data-use-current]');
    var accountSel = document.getElementById(root.dataset.accountSelect);
    var bucketSel = document.getElementById(root.dataset.bucketSelect);
    var perm = root.dataset.perm;
    var allowObjects = root.dataset.allowObjects === "1";
    var endpoint = root.dataset.browseEndpoint;
    var current = "";
    if (!input || !btn || !panel) return;

    function accountId() { return accountSel ? accountSel.value : ""; }
    function bucketName() { return bucketSel ? bucketSel.value : ""; }

    function renderBreadcrumb() {
      breadcrumb.innerHTML = "";
      var rootA = document.createElement("a");
      rootA.href = "#";
      rootA.textContent = bucketName() || "/";
      rootA.addEventListener("click", function (e) { e.preventDefault(); load(""); });
      breadcrumb.appendChild(rootA);
      var acc = "";
      current.split("/").filter(Boolean).forEach(function (part) {
        acc += part + "/";
        breadcrumb.appendChild(document.createTextNode(" / "));
        var a = document.createElement("a");
        a.href = "#";
        a.textContent = part;
        (function (p) { a.addEventListener("click", function (e) { e.preventDefault(); load(p); }); })(acc);
        breadcrumb.appendChild(a);
      });
    }

    function render(folders, objects) {
      list.innerHTML = "";
      folders.forEach(function (p) {
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.href = "#";
        a.textContent = "📁 " + p.slice(current.length).replace(/\/$/, "");
        a.addEventListener("click", function (e) { e.preventDefault(); load(p); });
        li.appendChild(a);
        list.appendChild(li);
      });
      if (allowObjects) {
        objects.forEach(function (k) {
          var name = k.slice(current.length);
          if (!name) return;
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.href = "#";
          a.textContent = "📄 " + name;
          a.addEventListener("click", function (e) {
            e.preventDefault();
            input.value = k;
            panel.hidden = true;
          });
          li.appendChild(a);
          list.appendChild(li);
        });
      }
      if (!folders.length && !(allowObjects && objects.length)) {
        var empty = document.createElement("li");
        empty.className = "hint";
        empty.textContent = "(vide)";
        list.appendChild(empty);
      }
    }

    function load(prefix) {
      if (!accountId() || !bucketName()) {
        list.innerHTML = '<li class="hint">Choisissez un compte et un bucket d’abord</li>';
        breadcrumb.innerHTML = "";
        return;
      }
      current = prefix;
      renderBreadcrumb();
      list.innerHTML = '<li class="hint">Chargement…</li>';
      var url = endpoint + "?account_id=" + encodeURIComponent(accountId())
        + "&perm=" + encodeURIComponent(perm) + "&bucket=" + encodeURIComponent(bucketName())
        + "&prefix=" + encodeURIComponent(prefix);
      fetch(url, { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json(); })
        .then(function (data) { render(data.folders || [], data.objects || []); })
        .catch(function () { list.innerHTML = '<li class="hint">Erreur de chargement</li>'; });
    }

    btn.addEventListener("click", function () {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) {
        var val = input.value || "";
        var start = val.endsWith("/") ? val : val.slice(0, val.lastIndexOf("/") + 1);
        load(start);
      }
    });
    if (useBtn) {
      useBtn.addEventListener("click", function () {
        input.value = current;
        panel.hidden = true;
      });
    }
    // Changing account/bucket invalidates whatever level was being browsed.
    if (accountSel) accountSel.addEventListener("change", function () { panel.hidden = true; });
    if (bucketSel) bucketSel.addEventListener("change", function () { panel.hidden = true; });
  }

  document.querySelectorAll("[data-pathpicker]").forEach(wire);
})();
