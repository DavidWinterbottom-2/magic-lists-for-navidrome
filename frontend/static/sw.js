/* Magic Lists service worker.
 *
 * Deliberately minimal. The app is a thin shell over live Navidrome and AI
 * calls, so there is nothing useful to serve offline beyond a "you're offline"
 * page — and the backend already sends Cache-Control on /static so browsers
 * revalidate app.js. An aggressive precache here would fight both, and would
 * pin a stale app.js in place after a deploy.
 *
 * So: network-first for page navigations with an offline fallback, and
 * everything else (API calls, static assets) straight to the network under the
 * server's own caching rules. The fetch handler also satisfies the browser's
 * installability requirement.
 */

const CACHE = 'magiclists-shell-v1';
const OFFLINE_URL = '/static/offline.html';

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE)
            .then(cache => cache.add(OFFLINE_URL))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    // Drop caches from older versions of this worker, then take over open tabs.
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const { request } = event;

    // Only page loads get the offline fallback; API and asset requests pass
    // through untouched so nothing is ever served stale.
    if (request.mode !== 'navigate' || request.method !== 'GET') {
        return;
    }

    event.respondWith(
        fetch(request).catch(() => caches.match(OFFLINE_URL))
    );
});
