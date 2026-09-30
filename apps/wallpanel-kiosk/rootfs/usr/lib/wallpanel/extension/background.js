// Keep the kiosk on the configured home origin (links cannot navigate away). The on-screen update page
// of wallpanel-api (127.0.0.1 only, shown via its HA switch) is the one other allowed origin.
importScripts('config.js');
const home = new URL(WALLPANEL_HOME);
const UPDATE_PAGE = 'http://127.0.0.1:8099';
chrome.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId !== 0) return;
  let u;
  try { u = new URL(d.url); } catch { return; }
  if (u.origin !== home.origin && u.origin !== UPDATE_PAGE && !u.protocol.startsWith('chrome')) {
    chrome.tabs.update(d.tabId, { url: WALLPANEL_HOME });
  }
});
