/* PUNTO VIVO FRESH · service worker para instalación como app */
const CACHE = 'pvf-static-20261124e';
const ASSETS = [
  '/static/logo.png',
  '/static/css/styles.css',
  '/static/js/public.js',
  '/static/js/admin.js',
  '/static/js/install.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Nunca se cachean páginas, API ni datos de sesión: solo archivos estáticos.
  if (!url.pathname.startsWith('/static/')) return;
  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
      }
      return response;
    }))
  );
});

// Notificación del navegador para el administrador: el servidor envía los datos.
self.addEventListener('push', (event) => {
  let datos = { title: 'PUNTO VIVO FRESH', body: 'Tienes un pedido nuevo.', url: '/admin' };
  if (event.data) {
    try { datos = { ...datos, ...event.data.json() }; }
    catch (e) { datos.body = event.data.text(); }
  }
  event.waitUntil(
    self.registration.showNotification(datos.title || 'PUNTO VIVO FRESH', {
      body: datos.body || '',
      tag: datos.tag || 'pvf-nuevo-pedido',
      data: { url: datos.url || '/admin' },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const destino = (event.notification.data && event.notification.data.url) || '/admin';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientes) => {
      const abierto = clientes.find((c) => c.url.includes(destino));
      if (abierto) return abierto.focus();
      return self.clients.openWindow(destino);
    })
  );
});
