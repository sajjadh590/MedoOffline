/* MedoOffline service worker.

   Registered only over http(s); file:// bundles skip it (see index.html).

   Caching rules:
     * Only immutable assets (fonts, icons, images) are cached.
     * Everything else — the app shell and every /api or .json response — is
       fetched from the network on each request.  This app is local-first:
       the archive changes every time the harvester runs, and a cached shell
       once made the browser show an old app.js and old questions after a
       restart, which is far worse than losing offline start-up.
*/

const CACHE_NAME = 'medo-assets-v3';

const isImmutableAsset = (url) =>
  /\.(woff2?|ttf|otf|png|jpe?g|webp|svg|ico)$/i.test(url.pathname);

self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  let url;
  try { url = new URL(req.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;
  if (!isImmutableAsset(url)) return;  // browser default: always the network

  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).then((res) => {
      if (res && res.status === 200 && res.type === 'basic') {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
      }
      return res;
    }))
  );
});
