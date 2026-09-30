// SPDX-License-Identifier: MIT
// Workarounds for third-party dashboard cards (remove once fixed upstream).
(() => {
  const findAll = (root, tag, out = []) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.localName === tag) out.push(el);
      if (el.shadowRoot) findAll(el.shadowRoot, tag, out);
    }
    return out;
  };
  // valetudo-map-card 2023.04.0 polls the map every 3 s while docked if the robot was already docked
  // at page load (issue Hypfer/lovelace-valetudo-map-card#149, fixed on master in 86fcf87, unreleased):
  // apply the card's own docked interval. Any real state change makes the card set its interval again.
  setInterval(() => {
    for (const c of findAll(document, 'valetudo-map-card')) {
      if (c.lastRobotState === 'docked' && c.pollInterval !== 120000) c.pollInterval = 120000;
    }
  }, 5000);
})();
