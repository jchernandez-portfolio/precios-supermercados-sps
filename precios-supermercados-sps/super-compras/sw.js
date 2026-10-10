// Service worker de Súper Compras: guarda la app para abrirla sin conexión.
// Los datos (raw.githubusercontent.com) van primero a la red y caen a la copia
// guardada si no hay internet, así nunca se muestran precios viejos sin necesidad.
const VERSION = "super-compras-20261010-1";
const SHELL = ["./", "index.html", "styles.css?v=20261010-1", "app.js?v=20261010-1", "data.js", "illustration.js",
  "manifest.webmanifest", "icons/icon.svg", "icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  const isData = url.hostname === "raw.githubusercontent.com" || url.pathname.endsWith(".json");
  const isShell = url.origin === self.location.origin && !isData;
  if (!isData && !isShell) return;
  event.respondWith(fetch(request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(VERSION).then((cache) => cache.put(request, copy));
    }
    return response;
  }).catch(() => caches.match(request).then((cached) => cached || Promise.reject(new Error("sin conexión")))));
});
