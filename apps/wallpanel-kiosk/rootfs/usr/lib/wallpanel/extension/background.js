// Keep the kiosk on the configured home origin (links cannot navigate away).
importScripts('config.js');
const home = new URL(WALLPANEL_HOME);
chrome.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId !== 0) return;
  let u;
  try { u = new URL(d.url); } catch { return; }
  if (u.origin !== home.origin && !u.protocol.startsWith('chrome')) {
    chrome.tabs.update(d.tabId, { url: WALLPANEL_HOME });
  }
});
