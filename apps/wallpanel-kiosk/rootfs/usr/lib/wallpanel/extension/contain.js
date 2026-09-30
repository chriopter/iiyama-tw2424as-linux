// Isolate every Home Assistant card in its own compositor layer and paint
// boundary: animated cards (e.g. the energy distribution flow) then repaint only
// themselves instead of large parts of the dashboard (measured: ~18 -> ~38 fps).
// Also hide scrollbars (touch scrolling keeps working): HA scrolls inside shadow
// roots, which a page-level stylesheet does not reach, so each root gets its own.
(() => {
  const seen = new WeakSet();
  const NO_SCROLLBARS = '* { scrollbar-width: none !important; } ::-webkit-scrollbar { display: none !important; }';
  function hideScrollbars(root) {
    if (seen.has(root)) return;
    seen.add(root);
    const s = document.createElement('style');
    s.textContent = NO_SCROLLBARS;
    (root === document ? document.documentElement : root).appendChild(s);
  }
  function scan(root) {
    hideScrollbars(root);
    root.querySelectorAll('ha-card').forEach((c) => {
      if (seen.has(c)) return;
      seen.add(c);
      c.style.contain = 'layout paint style';
      c.style.willChange = 'transform';
    });
    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) scan(e.shadowRoot); });
  }
  scan(document);
  setInterval(() => scan(document), 3000);  // dashboards render cards lazily
})();
