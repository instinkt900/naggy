// Phone UI glue. The board and form updates are all HTMX; the only thing left to
// wire by hand is swapping the interval lead word to match the selected kind
// ("Every N weeks" for a repeating chore vs "In N weeks" for a one-time task).
(function () {
  "use strict";

  function syncLead() {
    const form = document.getElementById("add-form");
    if (!form) return;
    const lead = document.getElementById("interval-lead");
    const kind = form.querySelector('input[name="kind"]:checked');
    if (lead && kind) lead.textContent = kind.value === "oneshot" ? "In" : "Every";
  }

  document.addEventListener("change", (e) => {
    if (e.target && e.target.name === "kind") syncLead();
  });

  // Run once on load, and again after any HTMX swap re-renders the form.
  window.addEventListener("load", syncLead);
  document.body.addEventListener("htmx:afterSwap", syncLead);
})();
