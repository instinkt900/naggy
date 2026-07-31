// Phone UI glue. The board and form updates are all HTMX; what's left to wire by
// hand is the "Repeats" tick box (it gates the cadence controls), the long-press
// edit dialog (a gesture HTMX has no trigger for), and the push notification
// toggle, which HTMX can't express because it has to talk to the browser's
// PushManager before it has anything to send the server.
(function () {
  "use strict";

  // The tick box is the only control for `kind`, so it drives two things: the
  // hidden field that actually goes on the wire, and the enabled state of the
  // cadence controls. Disabling them isn't only cosmetic — a disabled control is
  // left out of the submission, which is exactly what a one-off should send.
  function syncRepeat(form) {
    if (!form) return;
    const box = form.querySelector(".repeat-check");
    const controls = form.querySelector(".repeat-controls");
    if (!box || !controls) return;
    controls.querySelectorAll("input, select").forEach((el) => { el.disabled = !box.checked; });
    controls.classList.toggle("is-off", !box.checked);
    if (form.elements.kind) form.elements.kind.value = box.checked ? "recurring" : "oneshot";
  }

  function syncRepeats() {
    document.querySelectorAll("form").forEach(syncRepeat);
  }

  document.addEventListener("change", (e) => {
    if (e.target && e.target.classList && e.target.classList.contains("repeat-check")) {
      syncRepeat(e.target.form);
    }
  });

  // form.reset() after a successful add restores the tick box but not the
  // disabled flags it drives, which would leave the two disagreeing. The event
  // fires *before* the reset lands, hence the deferral.
  document.addEventListener("reset", (e) => {
    const form = e.target;
    setTimeout(() => syncRepeat(form), 0);
  });

  // --- long-press to edit -----------------------------------------------------

  const LONG_PRESS_MS = 500;
  const MOVE_TOLERANCE = 12;   // px of finger drift still counted as a press, not a scroll

  let pressTimer = null;
  let pressOrigin = null;
  let suppressClick = false;

  function cancelPress() {
    if (pressTimer) clearTimeout(pressTimer);
    pressTimer = null;
    pressOrigin = null;
  }

  const modal = () => document.getElementById("edit-modal");
  const editForm = () => document.getElementById("edit-form");

  function setEditError(message) {
    const p = document.getElementById("edit-error");
    if (p) p.textContent = message || "";
  }

  // Fill the dialog from the card's own data-* attributes and show it.
  function openEditor(card) {
    const dlg = modal();
    const form = editForm();
    if (!dlg || !form) return;
    const d = card.dataset;
    form.dataset.id = d.id;
    form.elements.title.value = d.title || "";
    form.elements.notes.value = d.notes || "";
    form.elements.interval_n.value = d.n || "1";
    form.elements.interval_unit.value = d.unit || "week";
    form.elements.due_date.value = d.due || "";
    // A one-off keeps whatever cadence it was last given, greyed out — so
    // re-ticking the box restores it rather than starting from a default.
    form.querySelector(".repeat-check").checked = d.kind !== "oneshot";
    syncRepeat(form);
    setEditError("");
    dlg.showModal();
  }

  document.addEventListener("pointerdown", (e) => {
    // A fresh press: whatever the last one armed is spent, even if its click
    // never arrived (a cancelled pointer sequence produces none).
    suppressClick = false;
    const card = e.target.closest && e.target.closest(".card[data-id]");
    // The delete button is its own gesture; don't shadow it with an edit.
    if (!card || e.target.closest(".card-del")) return;
    pressOrigin = { x: e.clientX, y: e.clientY };
    pressTimer = setTimeout(() => {
      cancelPress();
      // The release still fires a click; swallow it, or a long press on a pending
      // card would also tick the chore off.
      suppressClick = true;
      if (navigator.vibrate) navigator.vibrate(15);
      openEditor(card);
    }, LONG_PRESS_MS);
  });

  document.addEventListener("pointermove", (e) => {
    if (!pressOrigin) return;
    if (Math.abs(e.clientX - pressOrigin.x) > MOVE_TOLERANCE ||
        Math.abs(e.clientY - pressOrigin.y) > MOVE_TOLERANCE) {
      cancelPress();   // they're scrolling the board, not holding a card
    }
  });
  ["pointerup", "pointercancel", "scroll"].forEach((type) =>
    document.addEventListener(type, cancelPress, true));

  // Capture phase, so HTMX's own handler on the card button never sees the click.
  document.addEventListener("click", (e) => {
    if (!suppressClick) return;
    suppressClick = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  // Android pops a text-selection callout on a long press otherwise.
  document.addEventListener("contextmenu", (e) => {
    if (e.target.closest && e.target.closest(".card[data-id]")) e.preventDefault();
  });

  function initEditor() {
    const dlg = modal();
    const form = editForm();
    if (!dlg || !form) return;

    form.addEventListener("submit", (e) => {
      // Native validation has already passed by the time submit fires. The
      // request goes through htmx.ajax rather than hx-patch because htmx reads
      // the URL when it processes the element, and the reminder id isn't known
      // until a card is pressed — so the response still swaps #board exactly like
      // every other mutation.
      e.preventDefault();
      setEditError("");
      htmx.ajax("PATCH", "/api/reminders/" + form.dataset.id, {
        source: form,
        target: "#board",
        swap: "outerHTML",
      });
    });

    form.addEventListener("htmx:afterRequest", (e) => {
      if (e.detail.successful) dlg.close();
      else setEditError("Couldn't save that — check the fields and try again.");
    });

    dlg.addEventListener("click", (e) => {
      // Clicking the backdrop targets the dialog itself.
      if (e.target === dlg || e.target.hasAttribute("data-close-modal")) dlg.close();
    });
  }

  // --- home-screen icon badge -------------------------------------------------

  // Kept in step with the board on every swap, so clearing the last pending chore
  // clears the badge immediately rather than waiting for the next push.
  function syncBadge() {
    const board = document.getElementById("board");
    if (!board || !navigator.setAppBadge) return;
    const n = parseInt(board.dataset.pending || "0", 10) || 0;
    const p = n > 0 ? navigator.setAppBadge(n) : navigator.clearAppBadge();
    if (p && p.catch) p.catch(() => {});
  }

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

  // Run once on load, and again after any HTMX swap re-renders the board/form.
  window.addEventListener("load", syncRepeats);
  window.addEventListener("load", syncBadge);
  window.addEventListener("load", initEditor);
  window.addEventListener("load", initPush);
  document.body.addEventListener("htmx:afterSwap", syncRepeats);
  document.body.addEventListener("htmx:afterSwap", syncBadge);
})();
