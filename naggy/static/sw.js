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
const CACHE = "naggy-v3";
const SHELL = [
  "/",
  "/manifest.webmanifest",
  "/static/style.css",
  "/static/common.js",
  "/static/phone.js",
  "/static/htmx.min.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
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
  e.waitUntil(
    self.registration.showNotification(d.title || "Naggy", {
      body: d.body || "Something needs doing.",
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
      tag: d.tag || "naggy",       // replaces the previous nag instead of stacking
      renotify: true,              // ...but still buzzes when it does
      data: { url: d.url || "/" },
    })
  );
});

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

  e.respondWith(
    fetch(e.request)
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
