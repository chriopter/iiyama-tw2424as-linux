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

  // Tweak "Assist sofort zuhören" (settings.js, WALLPANEL_TWEAKS.assistListen): tapping the Assist button
  // starts listening right away (no second tap on the mic) – the frontend's own start_listening parameter
  // of the voice command dialog.
  if (typeof WALLPANEL_TWEAKS !== 'undefined' && WALLPANEL_TWEAKS.assistListen) customElements.whenDefined('ha-voice-command-dialog').then((c) => {
    const show = c.prototype.showDialog;
    if (show.__wallpanel) return;
    c.prototype.showDialog = function (params) {
      return show.call(this, { ...(params || {}), start_listening: true });
    };
    c.prototype.showDialog.__wallpanel = true;
  });
})();
