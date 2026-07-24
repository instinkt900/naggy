// naggy service worker.
//
// Runs on a LAN/VPN where the network is essentially always reachable and the app
// is still under active development, so freshness beats aggressive caching:
// everything is network-first with a cache fallback. When online you always get
// the latest code/markup; the cached shell only kicks in offline. Live data
// (/api/, /healthz) is never cached. Bump CACHE to invalidate the shell.
const CACHE = "naggy-v1";
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
