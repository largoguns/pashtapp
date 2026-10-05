/* PashtAPP Service Worker: estáticos cache-first, páginas network-first con fallback offline. */
const VERSION = "pashtapp-v6";
const STATIC_CACHE = `${VERSION}-static`;
const PAGE_CACHE = `${VERSION}-pages`;
const PRECACHE = [
  "/offline",
  "/static/css/app.css?v=6",
  "/static/js/app.js?v=6",
  "/static/vendor/htmx.min.js?v=6",
  "/static/vendor/chart.umd.min.js?v=6",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  // Nunca cachear API, exportaciones ni fragmentos HTMX: los datos financieros deben ser frescos.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/export/") || req.headers.get("HX-Request")) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(STATIC_CACHE).then((c) => c.put(req, copy)); }
        return res;
      }))
    );
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok && !res.redirected) { const copy = res.clone(); caches.open(PAGE_CACHE).then((c) => c.put(req, copy)); }
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match("/offline")))
    );
  }
});
