// Stat entry: checking "Did not play" disables that player's stat inputs, so a
// DNP line is saved with no stats. Works for the single-player entry page and
// for each player's section on the add-game page. Without this script the
// forms still work: the server ignores stats on a DNP line, and "Show all
// categories" is a plain <details> element.
(function () {
  "use strict";
  var scopes = document.querySelectorAll("[data-dnp-scope]");
  Array.prototype.forEach.call(scopes, function (scope) {
    var dnp = scope.querySelector('input[name$="did_not_play"]');
    var stats = scope.querySelector(".stat-fields");
    if (!dnp || !stats) {
      return;
    }
    function sync() {
      stats.disabled = dnp.checked;
    }
    dnp.addEventListener("change", sync);
    sync();
  });
})();
