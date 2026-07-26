// naggy service worker.
//
// Runs on a LAN/VPN where the network is essentially always reachable and the app
// is still under active development, so freshness beats aggressive caching:
// everything is network-first with a cache fallback. When online you always get
// the latest code/markup; the cached shell only kicks in offline. Live data
// (/api/, /healthz) is never cached. Bump CACHE to invalidate the shell.
//
// It also receives Web Push messages: the server signs and encrypts them itself
// (see naggy/notify.py), so the payload arriving here has only been relayed by the
// browser's push service, never read by it.
// The app version arrives as ?v= on the registration URL (see base.html), and is
// the single source of truth for cache busting: it names the cache *and* stamps
// every asset URL, so shipping a release retires the old cache and sidesteps the
// browser's HTTP cache in one move. Nothing to remember to bump by hand.
const V = new URL(self.location.href).searchParams.get("v") || "dev";
const CACHE = `naggy-${V}`;
const SHELL = [
  "/",
  "/manifest.webmanifest",
  `/static/style.css?v=${V}`,
  `/static/common.js?v=${V}`,
  `/static/phone.js?v=${V}`,
  `/static/htmx.min.js?v=${V}`,
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  // Deliberately not addAll(): that goes through the browser's HTTP cache, so a
  // stale script could be pinned into a freshly-versioned cache and survive the
  // very deploy meant to replace it. `reload` forces a real trip to the network.
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.all(SHELL.map((u) =>
        fetch(u, { cache: "reload" }).then((r) => (r.ok ? c.put(u, r) : null))
      )))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("push", (e) => {
  // A push with an undecodable body still deserves a notification: Android shows
  // a generic "site updated" one if we don't call showNotification at all.
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch (_) {
    d = {};
  }
  // The home-screen icon badge is the part that can't be swiped away, so it is
  // the honest "something is still outstanding" signal. Web notifications can't
  // be made non-dismissible; Android's ongoing flag isn't exposed to us.
  if (typeof d.badge_count === "number") setBadge(d.badge_count);

  e.waitUntil(
    self.registration.showNotification(d.title || "Naggy", {
      body: d.body || "Something needs doing.",
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
      tag: d.tag || "naggy",         // replaces the previous nag instead of stacking
      renotify: d.renotify !== false, // reposts replace quietly; only the first alerts
      silent: d.silent === true,
      data: { url: d.url || "/" },
    })
  );
});

function setBadge(count) {
  // Not universally implemented, and it throws rather than no-ops where it isn't.
  try {
    if (count > 0) self.navigator.setAppBadge(count);
    else self.navigator.clearAppBadge();
  } catch (_) {
    /* no badging support here */
  }
}

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  // Focus the already-open app if there is one — opening a second window every
  // time you tap a reminder gets old fast.
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (new URL(w.url).pathname === url && "focus" in w) return w.focus();
      }
      return self.clients.openWindow(url);
    })
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return; // never intercept mutations
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") return;

  // Same reasoning as install: bypass the HTTP cache for assets so the worker
  // can't re-cache something stale. Navigations are left alone — a Request in
  // navigate mode can't be safely rebuilt with an init, and the HTML is already
  // coming back fresh.
  const opts = url.pathname.startsWith("/static/") ? { cache: "no-store" } : undefined;

  e.respondWith(
    fetch(e.request, opts)
      .then((resp) => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return resp;
      })
      .catch(() =>
        caches.match(e.request).then(
          (cached) => cached || (e.request.mode === "navigate" ? caches.match("/") : undefined)
        )
      )
  );
});
