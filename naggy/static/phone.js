// Phone UI glue. The board and form updates are all HTMX; what's left to wire by
// hand is the interval lead word ("Every N weeks" vs "In N weeks") and the push
// notification toggle, which HTMX can't express because it has to talk to the
// browser's PushManager before it has anything to send the server.
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

  // --- push notifications ---------------------------------------------------

  // The applicationServerKey has to be raw bytes, but the server sends the key as
  // base64url (the only form the Web Push spec puts on the wire).
  function keyToBytes(b64) {
    const padded = (b64 + "=".repeat((4 - (b64.length % 4)) % 4))
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    const raw = atob(padded);
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  const btn = () => document.getElementById("notify-toggle");
  const hint = () => document.getElementById("notify-hint");

  function setState(label, message, disabled) {
    const b = btn();
    const h = hint();
    if (b) {
      b.textContent = label;
      b.disabled = !!disabled;
    }
    if (h && message) h.textContent = message;
  }

  async function subscribe(reg, key) {
    // Must be inside the click handler's task: Android only shows the permission
    // prompt for a request it can attribute to a user gesture.
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      setState("Enable notifications", "Permission denied — enable it in site settings.", false);
      return;
    }
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: keyToBytes(key),
    });
    const res = await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign(sub.toJSON(), { label: navigator.platform || "" })),
    });
    if (!res.ok) throw new Error("subscribe failed: " + res.status);
    setState("Disable notifications", "This device will be nagged when a chore falls due.", false);
  }

  async function unsubscribe(sub) {
    // Tell the server first: if we drop the browser subscription and then fail to
    // reach the server, it keeps pushing to an endpoint nothing listens on.
    await fetch("/api/push/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: sub.endpoint }),
    });
    await sub.unsubscribe();
    setState("Enable notifications", "Notifications are off for this device.", false);
  }

  async function initPush() {
    if (!btn()) return;
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      setState("Not supported", "This browser can't do push notifications.", true);
      return;
    }

    let key;
    try {
      const res = await fetch("/api/push/key");
      if (!res.ok) {
        setState("Unavailable", "Push isn't configured on the server.", true);
        return;
      }
      key = (await res.json()).key;
    } catch (_) {
      setState("Unavailable", "Couldn't reach the server.", true);
      return;
    }

    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (sub) {
      setState("Disable notifications", "This device will be nagged when a chore falls due.", false);
    } else {
      setState("Enable notifications", "Get a notification when a chore falls due.", false);
    }

    btn().addEventListener("click", async () => {
      setState("Working…", null, true);
      try {
        sub = await reg.pushManager.getSubscription();
        if (sub) {
          await unsubscribe(sub);
        } else {
          await subscribe(reg, key);
        }
      } catch (err) {
        setState("Enable notifications", "Something went wrong: " + err.message, false);
      }
    });
  }

  // Run once on load, and again after any HTMX swap re-renders the form.
  window.addEventListener("load", syncLead);
  window.addEventListener("load", initPush);
  document.body.addEventListener("htmx:afterSwap", syncLead);
})();
