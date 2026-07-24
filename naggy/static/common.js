// Shared helpers. Kept tiny — the UI is HTMX-driven, so there's little to do here.
window.NG = {
  // ms epoch -> local short date + time
  stamp(ms) {
    const d = new Date(ms);
    return d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  },
};
