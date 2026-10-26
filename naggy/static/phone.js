// Phone UI glue. The board and form updates are all HTMX; what's left to wire by
// hand is the kind picker (it picks which fields each form shows and re-labels
// the date field), the add sheet behind the + button, the long-press
// edit dialog (a gesture HTMX has no trigger for), the NFC tags in that dialog
// (Web NFC writes the tag before there's anything to swap), and the push
// notification toggle, which HTMX can't express because it has to talk to the
// browser's PushManager before it has anything to send the server.
(function () {
  "use strict";

  // The kind radios are the only control for `kind`, and they pick the form's
  // layout: each field tagged `data-kinds` shows only for the kinds it lists, and
  // its controls are disabled otherwise. Disabling isn't only cosmetic — a
  // disabled control is left out of the submission, which is exactly what a
  // one-off or a last-done reminder should send: no interval for the server to
  // store or drift. The values stay in the hidden inputs, so switching back in
  // the edit dialog restores the cadence the reminder had.
  // A last-done reminder has no due date at all, so the date becomes "the day it
  // was last done" and may be left blank (the server then means "today").
  function syncKind(form) {
    if (!form) return;
    const checked = form.querySelector('input[name="kind"]:checked');
    const kind = checked ? checked.value : "recurring";
    form.querySelectorAll("[data-kinds]").forEach((el) => {
      const on = el.dataset.kinds.split(" ").includes(kind);
      el.hidden = !on;
      el.querySelectorAll("input, select").forEach((c) => { c.disabled = !on; });
    });
    const label = form.querySelector(".kind-date-label");
    if (label) label.textContent = label.dataset["label" + kind[0].toUpperCase() + kind.slice(1)] || label.textContent;
    const date = form.querySelector('input[type="date"]');
    if (date) date.required = kind !== "open";
  }

  function syncKinds() {
    document.querySelectorAll("form").forEach(syncKind);
  }

  document.addEventListener("change", (e) => {
    if (e.target && e.target.name === "kind" && e.target.form) syncKind(e.target.form);
  });

  // form.reset() after a successful add restores the default-checked radio but
  // not the layout it drives, which would leave the two disagreeing. The event
  // fires *before* the reset lands, hence the deferral.
  document.addEventListener("reset", (e) => {
    const form = e.target;
    setTimeout(() => syncKind(form), 0);
  });

  // --- quick-pick dates ---------------------------------------------------------

  // "1 month", "a week ago" and friends, counted from the server's idea of today
  // (the form's data-today) so they land on the same day grid the schedule uses.
  // Months clamp to the end of the month, as schedule.py does, rather than
  // spilling over the way Date.setMonth would (31 Jan + 1 month is 28 Feb, not
  // 3 Mar).
  function shiftDate(iso, days, months) {
    const [y, m, d] = iso.split("-").map(Number);
    let date;
    if (months) {
      const first = new Date(y, m - 1 + months, 1);
      const last = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
      date = new Date(first.getFullYear(), first.getMonth(), Math.min(d, last));
    } else {
      date = new Date(y, m - 1, d + days);
    }
    const pad = (n) => String(n).padStart(2, "0");
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
  }

  document.addEventListener("click", (e) => {
    const chip = e.target.closest && e.target.closest(".quick-dates .chip");
    if (!chip) return;
    const form = chip.form;
    const input = form && form.querySelector('input[type="date"]');
    if (!input || !form.dataset.today) return;
    input.value = shiftDate(form.dataset.today,
      parseInt(chip.dataset.days || "0", 10), parseInt(chip.dataset.months || "0", 10));
  });

  // --- add sheet ----------------------------------------------------------------

  function initAdd() {
    const dlg = document.getElementById("add-modal");
    const form = document.getElementById("add-form");
    const open = document.getElementById("add-open");
    if (!dlg || !form || !open) return;
    const error = document.getElementById("add-error");

    open.addEventListener("click", () => {
      if (error) error.textContent = "";
      syncKind(form);
      dlg.showModal();
    });

    form.addEventListener("htmx:afterRequest", (e) => {
      if (e.detail.successful) {
        form.reset();
        dlg.close();
      } else if (error) {
        error.textContent = "Couldn't add that — check the fields and try again.";
      }
    });
  }

  // Both dialogs close on their Cancel button or a tap on the backdrop (which
  // targets the dialog itself). Closing the add sheet keeps what was typed, so an
  // accidental tap outside doesn't cost the draft.
  document.addEventListener("click", (e) => {
    const dlg = e.target.closest && e.target.closest("dialog.modal");
    if (!dlg) return;
    if (e.target === dlg || e.target.hasAttribute("data-close-modal")) dlg.close();
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
    // A one-off or an open-ended reminder keeps whatever cadence it was last
    // given, greyed out — so changing the kind back restores it rather than
    // starting from a default.
    const kind = d.kind || "recurring";
    const radio = form.querySelector('input[name="kind"][value="' + kind + '"]');
    if (radio) radio.checked = true;
    syncKind(form);
    setEditError("");
    setTagStatus("");
    loadTags(d.id);
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

    // A pending card has no ✕ (a tap anywhere on it ticks the chore off), so
    // this is where one gets deleted. The response swaps #board like any other
    // mutation.
    const del = document.getElementById("edit-delete");
    if (del) {
      del.addEventListener("click", () => {
        const title = form.elements.title.value;
        if (!confirm("Delete “" + title + "” entirely?")) return;
        setEditError("");
        // Sourced from the dialog, not the form: htmx validates a form source
        // even for a DELETE, so a half-edited (invalid) form would block it.
        htmx.ajax("DELETE", "/api/reminders/" + form.dataset.id, {
          source: dlg,
          target: "#board",
          swap: "outerHTML",
        }).then(() => dlg.close(), () => setEditError("Couldn't delete that — try again."));
      });
    }
  }

  // --- NFC tags ---------------------------------------------------------------

  // A tag holds nothing but a URL, /t/<token>, which Android opens when the phone
  // touches it; the server mints the token. Web NFC (Chrome on Android, over
  // HTTPS) writes it from here. Anywhere else the URL is shown instead, for an
  // NFC writer app to put on the tag.

  let tagWrite = null;   // AbortController of a write waiting for a tag, if any

  function setTagStatus(message) {
    const p = document.getElementById("tags-status");
    if (p) p.textContent = message || "";
  }

  function tagsUrl(id) {
    return "/api/reminders/" + id + "/tags";
  }

  async function loadTags(id) {
    const list = document.getElementById("tags-list");
    const empty = document.getElementById("tags-empty");
    if (!list) return;
    list.innerHTML = "";
    let tags = [];
    try {
      const res = await fetch(tagsUrl(id));
      if (res.ok) tags = (await res.json()).tags;
    } catch (_) {
      setTagStatus("Couldn't load this reminder's tags.");
    }
    // The dialog may have moved on to another card while this was in flight.
    if (editForm().dataset.id !== String(id)) return;
    tags.forEach((t) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = "Added " + t.created_local + " · " +
        (t.last_scan_local ? "last scanned " + t.last_scan_local : "never scanned");
      const del = document.createElement("button");
      del.type = "button";
      del.className = "tag-del";
      del.title = "Remove tag";
      del.textContent = "✕";
      del.dataset.tagId = t.id;
      li.append(label, del);
      list.append(li);
    });
    if (empty) empty.hidden = tags.length > 0;
  }

  function endTagWrite() {
    if (tagWrite) tagWrite.abort();
    tagWrite = null;
    const add = document.getElementById("tag-add");
    if (add) add.textContent = "Add a tag";
  }

  async function addTag() {
    const form = editForm();
    const id = form.dataset.id;
    const add = document.getElementById("tag-add");
    // The same button cancels a write that's waiting for a tag.
    if (tagWrite) {
      endTagWrite();
      setTagStatus("Cancelled.");
      return;
    }

    let tag;
    try {
      const res = await fetch(tagsUrl(id), { method: "POST" });
      if (!res.ok) throw new Error(res.status);
      tag = await res.json();
    } catch (_) {
      setTagStatus("Couldn't create a tag — try again.");
      return;
    }
    const url = location.origin + tag.path;

    if (!("NDEFReader" in window)) {
      setTagStatus("This browser can't write NFC tags. Write this link to the tag " +
        "with an NFC app (it's saved already): " + url);
      if (navigator.clipboard) navigator.clipboard.writeText(url).catch(() => {});
      loadTags(id);
      return;
    }

    const ctrl = tagWrite = new AbortController();
    add.textContent = "Cancel";
    setTagStatus("Hold a tag against the back of the phone…");
    try {
      // While a write is waiting, Chrome takes the tag for itself, so Android
      // won't open whatever URL a re-used tag already holds.
      await new NDEFReader().write(
        { records: [{ recordType: "url", data: url }] },
        { signal: ctrl.signal, overwrite: true });
      setTagStatus("Tag written. Scanning it now marks this chore done.");
    } catch (err) {
      // Not written, so the token points at nothing physical: drop it.
      fetch(tagsUrl(id) + "/" + tag.id, { method: "DELETE" }).catch(() => {});
      if (err.name !== "AbortError") {
        setTagStatus("Couldn't write the tag: " + (err.message || err.name));
      }
    } finally {
      // A cancel followed by a fresh tap may have started another write already.
      if (tagWrite === ctrl) {
        tagWrite = null;
        add.textContent = "Add a tag";
      }
      loadTags(id);
    }
  }

  function initTags() {
    const add = document.getElementById("tag-add");
    const list = document.getElementById("tags-list");
    const dlg = modal();
    if (!add || !list || !dlg) return;
    add.addEventListener("click", addTag);
    // Closing the dialog abandons a write still waiting for a tag.
    dlg.addEventListener("close", endTagWrite);

    list.addEventListener("click", async (e) => {
      const del = e.target.closest(".tag-del");
      if (!del) return;
      if (!confirm("Remove this tag? Scanning it will no longer do anything.")) return;
      const id = editForm().dataset.id;
      try {
        const res = await fetch(tagsUrl(id) + "/" + del.dataset.tagId, { method: "DELETE" });
        if (!res.ok) throw new Error(res.status);
        setTagStatus("Tag removed.");
      } catch (_) {
        setTagStatus("Couldn't remove that tag — try again.");
      }
      loadTags(id);
    });
  }

  // --- home-screen icon badge -------------------------------------------------

  // Kept in step with the board on every swap, so ticking a chore off updates the
  // badge and the notification immediately rather than waiting for the next push.
  function syncBadge() {
    const board = document.getElementById("board");
    if (!board) return;
    let state;
    try {
      state = JSON.parse(board.dataset.notification || "null");
    } catch (_) {
      state = null;
    }
    if (!state) {
      state = { notification: null, badge: parseInt(board.dataset.pending || "0", 10) || 0 };
    }
    NG.syncNotification(state);
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
  window.addEventListener("load", syncKinds);
  window.addEventListener("load", syncBadge);
  window.addEventListener("load", initAdd);
  window.addEventListener("load", initEditor);
  window.addEventListener("load", initTags);
  window.addEventListener("load", initPush);
  document.body.addEventListener("htmx:afterSwap", syncKinds);
  document.body.addEventListener("htmx:afterSwap", syncBadge);
})();
