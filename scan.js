/* SCAN: DOM-only global search. No dependency on aviation panel markup. */
(() => {
  'use strict';
  const KEY = 'spatchin-scan-v1';
  const palette = ['#ffd166', '#70d6ff', '#c5a3ff', '#95e6a4', '#ffabc7', '#ffb480'];
  const ui = Object.fromEntries(['terms', 'enabled', 'case', 'prev', 'next', 'count', 'legend', 'toggle', 'panel', 'indicator', 'keyword-count'].map(id => [id, document.getElementById(`scan-${id}`)]));
  let settings = {keywords: '', enabled: true, caseSensitive: false, expanded: false, colors: {}};
  try {
    const saved = JSON.parse(localStorage.getItem(KEY));
    if (saved && typeof saved === 'object') {
      if (typeof saved.keywords === 'string') settings.keywords = saved.keywords;
      for (const key of ['enabled', 'caseSensitive', 'expanded']) if (typeof saved[key] === 'boolean') settings[key] = saved[key];
      if (saved.colors && typeof saved.colors === 'object' && !Array.isArray(saved.colors)) {
        for (const [term, color] of Object.entries(saved.colors)) {
          if (/^#[0-9a-f]{6}$/i.test(color)) Object.defineProperty(settings.colors, term, {value: color, enumerable: true, writable: true, configurable: true});
        }
      }
    }
  } catch { /* Storage unavailable or malformed: use session defaults. */ }
  ui.terms.value = settings.keywords;
  ui.enabled.checked = settings.enabled;
  ui.case.checked = settings.caseSensitive;
  let matches = [], active = -1, timer;
  const excluded = 'script, style, noscript, input, textarea, select, option, [contenteditable]:not([contenteditable="false"]), [data-scan-ignore], #clock';
  function persist() {
    try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch { /* SCAN still works without storage. */ }
  }
  function disclosure() {
    ui.toggle.setAttribute('aria-expanded', String(settings.expanded));
    ui.panel.hidden = !settings.expanded;
    ui.indicator.textContent = settings.expanded ? '▴' : '▾';
  }
  function terms() {
    const seen = new Set();
    return settings.keywords.split(',').map(s => s.trim()).filter(term => {
      const key = settings.caseSensitive ? term : term.toLocaleLowerCase();
      if (!term || seen.has(key)) return false;
      seen.add(key);
      return true;
    }).map((term, index) => {
      let color = Object.hasOwn(settings.colors, term) ? settings.colors[term] : null;
      if (!color) {
        // Retain assigned colors across edits and reloads; extend beyond the palette.
        const used = new Set(Object.values(settings.colors));
        color = palette.find(c => !used.has(c));
        if (!color) {
          let n = index;
          do { color = '#' + ((0x87bbed + n++ * 0x394327) & 0xffffff).toString(16).padStart(6, '0'); } while (used.has(color));
        }
        Object.defineProperty(settings.colors, term, {value: color, enumerable: true, writable: true, configurable: true});
      }
      return {term, color, index};
    });
  }
  function paint(element, color) {
    element.style.backgroundColor = color;
    const rgb = color.slice(1).match(/../g).map(c => parseInt(c, 16) / 255).map(c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const luminance = rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
    element.style.color = luminance > 0.179 ? '#000000' : '#ffffff';
  }
  function status() {
    ui.count.textContent = !settings.enabled ? 'Highlighting off' : `${active < 0 ? 0 : active + 1} / ${matches.length} matches`;
    ui.prev.disabled = ui.next.disabled = !matches.length;
  }
  function apply() {
    observer.disconnect();
    try {
      const oldActive = active;
      const parents = new Set();
      document.querySelectorAll('mark[data-scan-match]').forEach(mark => {
        parents.add(mark.parentNode);
        mark.replaceWith(document.createTextNode(mark.textContent));
      });
      parents.forEach(parent => parent.normalize());
      matches = [];
      const keywords = terms();
      const activeKeywords = settings.enabled ? keywords.length : 0;
      ui['keyword-count'].textContent = `${activeKeywords} active keyword${activeKeywords === 1 ? '' : 's'}`;
      ui.legend.replaceChildren(...keywords.map(({term, color}) => {
        const chip = document.createElement('span');
        chip.className = 'scan-chip'; paint(chip, color);
        chip.textContent = term;
        return chip;
      }));
      if (settings.enabled && keywords.length) {
        // Escape every keyword: searches are literal, never user-provided regex or HTML.
        const ordered = [...keywords].sort((a, b) => b.term.length - a.term.length || a.index - b.index);
        const pattern = new RegExp(ordered.map(k => k.term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), settings.caseSensitive ? 'gu' : 'giu');
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
          acceptNode: node => node.nodeValue.trim() && !node.parentElement.closest(excluded) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT
        });
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        for (const node of nodes) {
          const text = node.nodeValue;
          const found = [...text.matchAll(pattern)];
          if (!found.length) continue;
          const fragment = document.createDocumentFragment();
          let offset = 0;
          for (const match of found) {
            const keyword = ordered.find(k => new RegExp(`^(?:${k.term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})$`, settings.caseSensitive ? 'u' : 'iu').test(match[0]));
            fragment.append(document.createTextNode(text.slice(offset, match.index)));
            const mark = document.createElement('mark');
            mark.dataset.scanMatch = ''; paint(mark, keyword.color);
            mark.textContent = match[0]; fragment.append(mark); matches.push(mark);
            offset = match.index + match[0].length;
          }
          fragment.append(document.createTextNode(text.slice(offset)));
          node.replaceWith(fragment);
        }
      }
      active = oldActive < 0 || !matches.length ? -1 : Math.min(oldActive, matches.length - 1);
      if (active >= 0) matches[active].classList.add('scan-current');
      persist(); status();
    } finally {
      observer.observe(document.body, {subtree: true, childList: true, characterData: true});
    }
  }
  function navigate(direction) {
    // Flush a pending refresh before selecting a match in replaced content.
    if (timer) { clearTimeout(timer); timer = null; apply(); }
    if (!matches.length) return;
    if (active >= 0) matches[active].classList.remove('scan-current');
    active = active < 0 ? (direction > 0 ? 0 : matches.length - 1) : (active + direction + matches.length) % matches.length;
    const target = matches[active];
    // Standard disclosure mechanisms; custom panels can also handle scan:reveal.
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
      if (parent.hidden) {
        parent.hidden = false;
        document.querySelectorAll('[aria-controls], [data-expand]').forEach(control => {
          if ((parent.id && control.getAttribute('aria-controls')?.split(/\s+/).includes(parent.id)) || (control.dataset.expand && parent.id === `detail-${control.dataset.expand}`)) {
            control.setAttribute('aria-expanded', 'true');
            if (control.dataset.expand) control.textContent = 'Hide details ▴';
          }
        });
      }
    }
    target.dispatchEvent(new CustomEvent('scan:reveal', {bubbles: true}));
    target.classList.add('scan-current');
    target.scrollIntoView({block: 'center', behavior: 'instant'});
    status();
  }
  const observer = new MutationObserver(records => {
    if (!records.some(record => !record.target.parentElement?.closest(excluded) && !(record.target instanceof Element && record.target.closest(excluded)))) return;
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; apply(); }, 80);
  });
  function change() {
    settings.keywords = ui.terms.value;
    settings.enabled = ui.enabled.checked;
    settings.caseSensitive = ui.case.checked;
    active = -1;
    clearTimeout(timer); timer = null; apply();
  }
  ui.terms.addEventListener('input', change);
  ui.toggle.addEventListener('click', () => {
    settings.expanded = !settings.expanded;
    disclosure(); persist();
  });
  ui.enabled.addEventListener('change', change);
  ui.case.addEventListener('change', change);
  ui.prev.addEventListener('click', () => navigate(-1));
  ui.next.addEventListener('click', () => navigate(1));
  ui.terms.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); navigate(event.shiftKey ? -1 : 1); }
  });
  disclosure(); apply();
})();
