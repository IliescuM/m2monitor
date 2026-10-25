(function () {
  // Language: a first visit to the English page follows the browser's
  // language; a choice made in the switcher is remembered and wins.
  var LANGS = ["en", "tr", "ro", "de"];
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
