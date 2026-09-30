// Keep the kiosk on the configured home origin (links cannot navigate away). The on-screen update page
// of wallpanel-api (127.0.0.1 only, shown via its HA switch) is the one other allowed origin.
importScripts('config.js');
const home = new URL(WALLPANEL_HOME);
const UPDATE_PAGE = 'http://127.0.0.1:8099';
// Scaling preview on the update page: it frames the dashboard. Home Assistant sends X-Frame-Options:
// SAMEORIGIN, also on the responses its service worker fetches (initiator = HA itself), so the header is
// dropped for the home host - harmless here, the kiosk opens no other sites that could frame it.
chrome.declarativeNetRequest.updateDynamicRules({
  removeRuleIds: [1],
  addRules: [{
    id: 1, priority: 1,
    action: { type: 'modifyHeaders', responseHeaders: [{ header: 'x-frame-options', operation: 'remove' }] },
    condition: { requestDomains: [home.hostname], resourceTypes: ['sub_frame', 'xmlhttprequest', 'other'] },
  }],
});
chrome.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId !== 0) return;
  let u;
  try { u = new URL(d.url); } catch { return; }
  if (u.origin !== home.origin && u.origin !== UPDATE_PAGE && !u.protocol.startsWith('chrome')) {
    chrome.tabs.update(d.tabId, { url: WALLPANEL_HOME });
  }
});
