// Isolate every Home Assistant card in its own compositor layer and paint
// boundary: animated cards (e.g. the energy distribution flow) then repaint only
// themselves instead of large parts of the dashboard (measured: ~18 -> ~38 fps).
// Also hide scrollbars (touch scrolling keeps working): HA scrolls inside shadow
// roots, which a page-level stylesheet does not reach, so each root gets its own.
// Tweak "Kopfleiste ausblenden" (settings.js, WALLPANEL_TWEAKS.hideHeader): Home Assistant's top bar goes, only
// its search and Assist buttons stay - top right, next to the badges; saves ~56 px. Not in edit mode and not on
// dashboards with view tabs (the tabs are the navigation).
(() => {
  const HIDE_HEADER = `
    div:not(.edit-mode):not(:has(ha-tab-group)) > .header { background: transparent; box-shadow: none !important;
      width: auto; left: auto; right: 0; top: 13px; pointer-events: none; backdrop-filter: none; }
    div:not(.edit-mode):not(:has(ha-tab-group)) > .header .toolbar { border-bottom: none; }
    div:not(.edit-mode):not(:has(ha-tab-group)) > .header .main-title,
    div:not(.edit-mode):not(.narrow):not(:has(ha-tab-group)) > .header ha-menu-button { display: none; }
    div:not(.edit-mode):not(:has(ha-tab-group)) > .header .action-items,
    div:not(.edit-mode):not(:has(ha-tab-group)) > .header ha-menu-button { pointer-events: auto; }
    div:not(.edit-mode):not(:has(ha-tab-group)) > hui-view-container {
      padding-top: calc(var(--safe-area-inset-top, 0px) + var(--view-container-padding-top, 0px)); }`;
  const hideHeader = typeof WALLPANEL_TWEAKS !== 'undefined' && WALLPANEL_TWEAKS.hideHeader;
  const seen = new WeakSet();
  const NO_SCROLLBARS = '* { scrollbar-width: none !important; } ::-webkit-scrollbar { display: none !important; }';
  function hideScrollbars(root) {
    if (seen.has(root)) return;
    seen.add(root);
    const s = document.createElement('style');
    s.textContent = NO_SCROLLBARS;
    (root === document ? document.documentElement : root).appendChild(s);
  }
  function tweak(el) {
    if (!hideHeader || el.localName !== 'hui-root' || seen.has(el)) return;
    seen.add(el);
    const s = document.createElement('style');
    s.textContent = HIDE_HEADER;
    el.shadowRoot.appendChild(s);
  }
  function scan(root) {
    hideScrollbars(root);
    root.querySelectorAll('ha-card').forEach((c) => {
      if (seen.has(c)) return;
      seen.add(c);
      c.style.contain = 'layout paint style';
      c.style.willChange = 'transform';
    });
    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) { tweak(e); scan(e.shadowRoot); } });
  }
  scan(document);
  // dashboards render lazily; faster at first so the top bar disappears right after loading
  let n = 0;
  const t = setInterval(() => { scan(document); if (++n === 20) clearInterval(t); }, 250);
  setInterval(() => scan(document), 3000);
})();
