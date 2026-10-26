// Shared helpers. Kept tiny — the UI is HTMX-driven, so there's little to do here.
window.NG = {
  // ms epoch -> local short date. Date only, no time: reminders are due on a day,
  // so a clock time would imply precision the schedule doesn't have.
  stamp(ms) {
    const d = new Date(ms);
    return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  },

  // Bring the icon badge and the pending notification into line with what the
  // server says is outstanding (see notification_state in web.py). The worker
  // owns the notification, so that half is a message to it; `active` rather
  // than `controller`, because a scan opened in a fresh tab may not be
  // controlled yet.
  syncNotification(state) {
    if (!state) return;
    if (navigator.setAppBadge) {
      const p = state.badge > 0 ? navigator.setAppBadge(state.badge) : navigator.clearAppBadge();
      if (p && p.catch) p.catch(() => {});
    }
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.getRegistration()
      .then((reg) => {
        if (reg && reg.active) reg.active.postMessage({ action: "sync-pending", ...state });
      })
      .catch(() => {});
  },
};

// The NFC scan page carries its state inline; the board's goes through syncBadge.
document.addEventListener("DOMContentLoaded", () => {
  const el = document.getElementById("notification-state");
  if (!el) return;
  try {
    NG.syncNotification(JSON.parse(el.textContent));
  } catch (_) {
    /* malformed state: leave the shade alone */
  }
});
