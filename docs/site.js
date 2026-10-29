(function () {
  // Language: a first visit to the English page follows the browser's
  // language; a choice made in the switcher is remembered and wins.
  var LANGS = ["en", "tr", "ro", "de", "it", "pl", "pt", "es", "hu"];
  var KEY = "m2monitor-lang";
  var page = document.documentElement.lang;

  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function save(lang) {
    try { localStorage.setItem(KEY, lang); } catch (e) { /* private mode */ }
  }

  document.querySelectorAll("[data-lang]").forEach(function (link) {
    link.addEventListener("click", function () { save(link.dataset.lang); });
  });

  // Close the language menu on an outside click or Escape.
  var menu = document.querySelector(".lang-menu");
  if (menu) {
    document.addEventListener("click", function (e) {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); }
    });
  }

  if (page === "en" && !saved()) {
    var prefs = navigator.languages || [navigator.language || ""];
    for (var i = 0; i < prefs.length; i++) {
      var code = String(prefs[i]).slice(0, 2).toLowerCase();
      if (code === "en") break;
      if (LANGS.indexOf(code) > 0) {
        location.replace(code + "/");
        return;
      }
    }
  }

  // The example cards can be renamed like in the app: click the name or the
  // pencil, then save or cancel. Nothing is stored or sent anywhere.
  var mock = document.querySelector(".mock");
  var ICON_OK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  var ICON_NO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  if (mock) {
    mock.querySelectorAll(".who").forEach(function (who) {
      var row = who.querySelector(".mname-row");
      var name = who.querySelector(".mname");
      var pen = who.querySelector(".pen-btn");
      var info = who.querySelector("small");
      var card = who.closest(".client");

      function button(cls, icon, label, onClick) {
        var b = document.createElement("button");
        b.type = "button"; b.className = "icon-btn " + cls; b.innerHTML = icon;
        b.setAttribute("aria-label", label);
        b.addEventListener("click", onClick);
        return b;
      }

      function start() {
        if (row.querySelector("input")) return;
        var input = document.createElement("input");
        input.type = "text"; input.maxLength = 24; input.className = "mname-input";
        input.placeholder = "Empty = automatic";
        input.setAttribute("aria-label", pen.getAttribute("aria-label"));
        if (!name.dataset.auto) input.value = name.textContent;
        var ok = button("ok", ICON_OK, mock.dataset.save, function () { finish(true); });
        var no = button("no", ICON_NO, mock.dataset.cancel, function () { finish(false); });
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter") finish(true);
          if (e.key === "Escape") finish(false);
        });
        name.hidden = true; pen.hidden = true;
        row.append(input, ok, no);
        input.focus(); input.select();

        function finish(save) {
          var value = input.value.trim();
          input.remove(); ok.remove(); no.remove();
          name.hidden = false; pen.hidden = false; pen.focus();
          if (!save) return;
          var client = info.dataset.base.split(" · ")[0];
          if (value) {
            name.textContent = value; delete name.dataset.auto;
            info.textContent = client + " · named by you";
            card.classList.remove("reading");
          } else {
            name.textContent = "Reading name…"; name.dataset.auto = "1";
            info.textContent = info.dataset.base;
            card.classList.add("reading");
          }
        }
      }

      name.addEventListener("click", start);
      pen.addEventListener("click", start);
    });
  }

  // Let the example RTT readings drift a little, like the real app refreshing.
  var cells = document.querySelectorAll(".rtt");
  function level(ms) { return ms < 80 ? "good" : ms < 150 ? "warn" : "bad"; }
  function paint() {
    cells.forEach(function (cell) {
      var base = Number(cell.dataset.base);
      var ms = Math.max(20, Math.round(base + (Math.random() - 0.5) * base * 0.18));
      cell.textContent = ms + " ms";
      cell.dataset.level = level(ms);
    });
  }
  paint();
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) setInterval(paint, 1000);
})();
