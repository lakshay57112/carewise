/* Carewise service worker: shows a notification when the server says a dose is due,
   even when the page is closed. Tapping it opens Carewise, which speaks the reminder. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(d.title || "Carewise", {
    body: d.body || "Time for your medicine.",
    tag: d.tag || "dose",
    renotify: true,
    requireInteraction: true,
    icon: "/static/icon-192.png",
    badge: "/static/badge.png",
    vibrate: [200, 100, 200],
    data: d,
    // A test shouldn't be able to tick off a real dose.
    actions: d.test ? [] : [{ action: "taken", title: "Taken" }],
  }));
});

self.addEventListener("notificationclick", (event) => {
  const d = event.notification.data || {};
  event.notification.close();

  if (event.action === "taken" && d.sid && d.slot) {
    event.waitUntil(
      fetch("/api/dose", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: d.sid, slot_key: d.slot, taken: true }),
      }).catch(() => {})
    );
    return;
  }

  const url = `/?sid=${encodeURIComponent(d.sid || "")}&dose=${encodeURIComponent(d.slot || "")}&lang=${encodeURIComponent(d.lang || "")}`;
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (open.length) {
      const c = open[0];
      await c.focus();
      c.postMessage({ type: "dose", sid: d.sid, slot: d.slot, lang: d.lang });
      return;
    }
    await self.clients.openWindow(url);
  })());
});
