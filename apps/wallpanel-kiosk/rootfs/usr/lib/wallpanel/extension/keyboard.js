// Touch keyboard for cage, which has no text-input/input-method protocol support.
// MAIN world: native editing must reach inputs inside Home Assistant's shadow roots.
(() => {
  if (document.getElementById('wallpanel-keyboard')) return;
  const host = document.createElement('div');
  host.id = 'wallpanel-keyboard';
  host.popover = 'manual';
  host.style.cssText = 'position:fixed;inset:auto 0 0;width:auto;max-width:none;margin:0;padding:0;border:0;background:transparent;overflow:visible';
  const root = host.attachShadow({mode: 'open'});
  root.innerHTML = `<style>
    :host { color-scheme: dark; }
    .keyboard { background:#20252c; padding:10px 14px; box-shadow:0 -3px 18px #0008;
      font:24px system-ui; user-select:none; touch-action:manipulation; }
    .row { display:flex; justify-content:center; gap:7px; margin:6px auto; max-width:1400px; }
    button { flex:1; min-width:0; height:clamp(42px,5.5vh,64px); border:0; border-radius:8px;
      background:#3a424e; color:#fff; font:inherit; cursor:pointer; }
    button:active, button.active { background:#527aab; }
    button.wide { flex:1.8; font-size:20px; } button.space { flex:6; }
    .hint { font-size:14px; color:#bdc7d5; padding:0 5px; }
  </style><div class="keyboard" role="group" aria-label="Bildschirmtastatur">
    <div class="hint">Deutsch · Bildschirmtastatur</div><div class="keys"></div></div>`;
  document.documentElement.append(host);
  let target = null, shift = false, symbols = false, numeric = false;
  const editable = el => el instanceof HTMLElement && !el.disabled && !el.readOnly &&
    el.inputMode !== 'none' && (el.isContentEditable || el instanceof HTMLTextAreaElement ||
    (el instanceof HTMLInputElement && /^(text|search|email|url|tel|password|number)$/.test(el.type)));
  const active = () => {
    let el = document.activeElement;
    while (el?.shadowRoot?.activeElement) el = el.shadowRoot.activeElement;
    return el;
  };
  function hide() { host.hidePopover(); target = null; }
  function front() {
    if (!target?.isConnected) return hide();
    // Keep the keyboard inside an open dialog/popover's composed subtree. This
    // avoids modal inertness and an auto dropdown treating key taps as outside.
    let container = document.documentElement;
    for (let el = target; el; el = el.assignedSlot || el.parentNode || el.host) {
      if (el instanceof HTMLElement && el.matches('dialog[open], :popover-open')) {
        container = el; break;
      }
    }
    if (host.parentNode !== container) container.append(host);
    if (!host.matches(':popover-open')) host.showPopover();
  }
  function show(el) {
    if (target === el) { front(); return; }
    target = el;
    numeric = el.type === 'number' || el.type === 'tel' || /^(numeric|decimal|tel)$/.test(el.inputMode);
    shift = false; symbols = false;
    render(); front();
    requestAnimationFrame(() => {
      if (target !== el) return;
      front(); // the dropdown may have opened after the field received focus
      el.scrollIntoView({block:'center', inline:'nearest', behavior:'smooth'});
    });
  }
  function edit(text, backward = false) {
    if (!target?.isConnected || !editable(target)) return hide();
    target.focus({preventScroll:true});
    // Native commands preserve selection, undo and the browser's normal input events,
    // including for contenteditable and inputs inside nested shadow roots.
    if (document.execCommand(backward ? 'delete' : 'insertText', false, text)) return;
    if (target.isContentEditable) return;
    const value = target.value;
    let start = target.selectionStart ?? value.length, end = target.selectionEnd ?? value.length;
    if (backward && start === end) start -= [...value.slice(0, start)].pop()?.length || 0;
    const next = value.slice(0, start) + (backward ? '' : text) + value.slice(end);
    const proto = target instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(target, next);
    try { target.setSelectionRange(start + (backward ? 0 : text.length), start + (backward ? 0 : text.length)); } catch (_) {}
    target.dispatchEvent(new InputEvent('input', {bubbles:true, composed:true,
      inputType:backward ? 'deleteContentBackward' : 'insertText', data:backward ? null : text}));
  }
  function press(key) {
    if (key === 'Schließen') return hide();
    if (key === '⇧') { shift = !shift; return render(); }
    if (key === '?123' || key === 'ABC') { symbols = !symbols; numeric = false; return render(); }
    if (key === '⌫') return edit('', true);
    if (key === 'Leerzeichen') return edit(' ');
    if (key === '↵') {
      if (!target) return;
      if (target instanceof HTMLTextAreaElement || target.isContentEditable) return edit('\n');
      const el = target;
      el.dispatchEvent(new Event('change', {bubbles:true, composed:true}));
      const go = el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter',
        keyCode:13, which:13, bubbles:true, composed:true, cancelable:true}));
      el.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', bubbles:true, composed:true}));
      if (go && el.form) el.form.requestSubmit();
      return hide();
    }
    edit(key);
    if (shift) { shift = false; render(); }
  }
  function render() {
    const rows = numeric ? ['1 2 3', '4 5 6', '7 8 9', '- 0 , .'] : symbols ?
      ['1 2 3 4 5 6 7 8 9 0', '@ # € % & * ( ) [ ]', '! " § $ / \\ = + : ;', '_ - ? , . < > { }'] :
      ['q w e r t z u i o p ü', 'a s d f g h j k l ö ä', 'y x c v b n m ß , .'];
    const keys = root.querySelector('.keys'); keys.replaceChildren();
    for (const row of [...rows.map(r => r.split(' ')), ['⇧', symbols || numeric ? 'ABC' : '?123', 'Leerzeichen', '⌫', '↵', 'Schließen']]) {
      const line = document.createElement('div'); line.className = 'row';
      for (let key of row) {
        if (shift && key.length === 1) key = key.toLocaleUpperCase('de');
        const button = document.createElement('button'); button.type = 'button'; button.textContent = key;
        button.tabIndex = -1;
        if (key.length > 1) button.className = key === 'Leerzeichen' ? 'space' : 'wide';
        if (key === '⇧' && shift) button.classList.add('active');
        button.setAttribute('aria-label', key === '⌫' ? 'Löschen' : key === '↵' ? 'Eingabe' : key);
        button.addEventListener('click', () => press(key)); line.append(button);
      }
      keys.append(line);
    }
  }
  root.addEventListener('pointerdown', e => { e.preventDefault(); e.stopPropagation(); });
  root.addEventListener('click', e => e.stopPropagation());
  document.addEventListener('focusin', e => {
    const el = e.composedPath().find(editable);
    if (el) show(el); else if (!e.composedPath().includes(host)) hide();
  }, true);
  document.addEventListener('pointerdown', e => {
    if (e.composedPath().includes(host)) return;
    if (!e.composedPath().some(editable)) hide();
    else setTimeout(() => { const el = active(); if (editable(el)) show(el); }, 0);
  }, true);
  document.addEventListener('focusout', () => setTimeout(() => {
    if (target && active() !== target) hide();
  }, 0), true);
  // Also handle fields already focused when the extension is loaded.
  if (editable(active())) show(active());
})();
