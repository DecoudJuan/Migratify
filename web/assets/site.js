/* Migratify landing page — three small jobs, no dependencies.
   1. copy-to-clipboard on the command rows
   2. reveal sections on scroll (and the scoring bars with them)
   3. drop the .no-js guard so CSS can own the initial hidden state    */

(function () {
  "use strict";

  document.documentElement.classList.remove("no-js");

  /* --- copy ------------------------------------------------------------- */

  document.querySelectorAll(".copy-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var row = btn.closest(".copy-row");
      var code = row && row.querySelector("code");
      if (!code) return;

      // the leading "$ " is a prompt, not part of the command
      var text = Array.prototype.filter
        .call(code.childNodes, function (node) {
          return !(node.nodeType === 1 && node.classList.contains("p"));
        })
        .map(function (node) { return node.textContent; })
        .join("")
        .trim();

      var done = function () {
        var label = btn.dataset.copied || "copied";
        var original = btn.dataset.label || btn.textContent;
        btn.dataset.label = original;
        btn.textContent = label;
        btn.dataset.done = "1";
        window.setTimeout(function () {
          btn.textContent = original;
          btn.dataset.done = "0";
        }, 1600);
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
        return;
      }

      // http:// or an old browser: the textarea fallback still works
      var scratch = document.createElement("textarea");
      scratch.value = text;
      scratch.setAttribute("readonly", "");
      scratch.style.position = "fixed";
      scratch.style.opacity = "0";
      document.body.appendChild(scratch);
      scratch.select();
      try { document.execCommand("copy"); done(); } catch (e) { /* nothing to do */ }
      document.body.removeChild(scratch);
    });
  });

  /* --- reveal ----------------------------------------------------------- */

  var targets = document.querySelectorAll(".rv");
  if (!targets.length) return;

  if (!("IntersectionObserver" in window)) {
    targets.forEach(function (el) { el.classList.add("is-in"); });
    return;
  }

  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -12% 0px", threshold: 0.08 }
  );

  targets.forEach(function (el) { observer.observe(el); });
})();
