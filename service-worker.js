
const CACHE_NAME = "lungscan-static-v2";

self.addEventListener("install", event => {
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  // Never cache uploads, POST requests, or analysis results.
  if (
    request.method !== "GET" ||
    url.pathname.includes("/upload") ||
    url.pathname.includes("/results") ||
    url.pathname.includes("/analyze")
  ) {
    return;
  }

  // Keep the application network-dependent so its current
  // MATLAB backend is used for actual analysis.
  event.respondWith(
    fetch(request).catch(() => caches.match(request))
  );
});
