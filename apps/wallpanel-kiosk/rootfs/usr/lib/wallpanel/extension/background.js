// Keep the kiosk on the configured home origin (links cannot navigate away). The on-screen update page
// of wallpanel-api (127.0.0.1 only, shown via its HA switch) is the one other allowed origin.
// WALLPANEL_ZOOM ("Skalierung" on the update page): page zoom of the home origin, 1.25 = Home Assistant
// laid out for 1536 x 864; the update page keeps 100 %. (--force-device-scale-factor has no effect on the
// layout under Wayland: Chromium only renders a larger buffer and scales it down.)
importScripts('config.js');
const home = new URL(WALLPANEL_HOME);
const UPDATE_PAGE = 'http://127.0.0.1:8099';
function zoom(tabId) {
  chrome.tabs.getZoom(tabId, (z) => {
    if (!chrome.runtime.lastError && Math.abs(z - WALLPANEL_ZOOM) > 0.001) chrome.tabs.setZoom(tabId, WALLPANEL_ZOOM);
  });
}
// the first page load of a browser start can commit before this worker runs
chrome.tabs.query({}, (tabs) => tabs.forEach((t) => {
  try { if (new URL(t.url || t.pendingUrl).origin === home.origin) zoom(t.id); } catch {}
}));
chrome.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId !== 0) return;
  let u;
  try { u = new URL(d.url); } catch { return; }
  if (u.origin === home.origin) {
    zoom(d.tabId);
  } else if (u.origin !== UPDATE_PAGE && !u.protocol.startsWith('chrome')) {
    chrome.tabs.update(d.tabId, { url: WALLPANEL_HOME });
  }
});
