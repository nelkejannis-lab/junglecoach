// Service worker: makes the web app installable and shows push notifications.
// Pushes arrive empty; the text is fetched from the server for this subscription.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
self.addEventListener("push", (e) => {
  e.waitUntil((async () => {
    let m = { title: "Moosi", body: "Neues in Moosi", url: "/m/" };
    try {
      const sub = await self.registration.pushManager.getSubscription();
      if (sub) m = await (await fetch("/api/push/inbox?ep=" + encodeURIComponent(sub.endpoint), { cache: "no-store" })).json();
    } catch (err) { /* keep the default text */ }
    await self.registration.showNotification(m.title || "Moosi", { body: m.body || "", icon: "/moosi/logo.webp", badge: "/moosi/head.webp",
      data: { url: m.url || "/m/" }, tag: m.title });
  })());
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/m/";
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = all.find((c) => new URL(c.url).pathname.startsWith("/m"));
    if (open) { await open.focus(); return; }
    await self.clients.openWindow(url);
  })());
});
