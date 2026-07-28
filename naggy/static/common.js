// Shared helpers. Kept tiny — the UI is HTMX-driven, so there's little to do here.
window.NG = {
  // ms epoch -> local short date. Date only, no time: reminders are due on a day,
  // so a clock time would imply precision the schedule doesn't have.
  stamp(ms) {
    const d = new Date(ms);
    return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  },
};
