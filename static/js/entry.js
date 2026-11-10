// Stat entry: checking "Did not play" disables the stat inputs, so a DNP line
// is saved with no stats. Without this script the form still works: the
// server ignores stats on a DNP line, and "Show all categories" is a plain
// <details> element.
(function () {
  "use strict";
  var dnp = document.getElementById("id_did_not_play");
  var stats = document.querySelector(".stat-fields");
  if (!dnp || !stats) {
    return;
  }
  function sync() {
    stats.disabled = dnp.checked;
  }
  dnp.addEventListener("change", sync);
  sync();
})();
