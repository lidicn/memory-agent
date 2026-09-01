/* Memory Worker Service Worker — 离线壳 + 静态资源缓存
 * 注意：本 SW 仅通过 HTTPS / localhost 注册（index.html 中已做协议守卫）。
 * API 与 SSE 流一律走 network-only，保证实时数据不被缓存污染。
 */
const CACHE = 'mw-shell-v4';

// 离线壳预缓存的核心资源（首次联网时缓存，之后断网也能打开 App）
const PRECACHE = [
  '/',
  '/static/manifest.webmanifest',
  '/static/css/app.css',
  '/static/js/main.js',
  '/static/js/api.js',
  '/static/js/store.js',
  '/static/js/util.js',
  '/static/vendor/tailwind.min.js',
  '/static/vendor/alpine.min.js',
  '/static/vendor/marked.min.js',
  '/static/js/pages/dashboard.js',
  '/static/js/pages/collect.js',
  '/static/js/pages/assistant.js',
  '/static/js/pages/insights.js',
  '/static/js/pages/members.js',
  '/static/js/pages/settings.js',
  '/static/js/pages/mcp.js',
  '/static/js/pages/acp.js',
  '/static/js/pages/agent_memory.js',
  '/static/js/pages/vision.js',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      // 单个资源失败不应阻断整体安装（例如某页面模块临时缺失）
      Promise.allSettled(PRECACHE.map((url) => cache.add(url)))
    )
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // POST/PUT/DELETE 一律直连（含 SSE 流）

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // 仅处理同源

  // API / MCP 实时接口：绝不缓存，直接走网络
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/mcp')) return;

  // 导航请求：网络优先，失败回退到离线壳（缓存的 '/'）
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match('/').then((r) => r || caches.match('/static/manifest.webmanifest')))
    );
    return;
  }

  // 静态资源：stale-while-revalidate
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((res) => {
          if (res && res.status === 200 && res.type === 'basic') {
            const copy = res.clone();
            caches.open(CACHE).then((cache) => cache.put(req, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
