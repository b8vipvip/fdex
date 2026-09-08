(() => {
  const list = document.getElementById('fdex-recent-chat-list');
  if (!list) return;

  let loading = false;
  let lastLoaded = 0;

  const escapeText = (value) => String(value ?? '');

  function render(items) {
    list.replaceChildren();
    if (!Array.isArray(items) || items.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'recent-empty';
      empty.textContent = '暂无最近聊天';
      list.appendChild(empty);
      return;
    }
    const current = location.pathname;
    items.forEach((item) => {
      const link = document.createElement('a');
      link.className = 'recent-chat-item';
      link.href = escapeText(item.url || '#');
      link.dataset.kind = item.kind === 'group' ? 'group' : 'employee';
      if (new URL(link.href, location.href).pathname === current) link.classList.add('active');

      const kind = document.createElement('span');
      kind.className = 'recent-chat-kind';
      kind.setAttribute('aria-hidden', 'true');

      const copy = document.createElement('span');
      copy.className = 'recent-chat-copy';
      const name = document.createElement('span');
      name.className = 'recent-chat-name';
      name.textContent = escapeText(item.name || (item.kind === 'group' ? '工作群' : '智体'));
      const summary = document.createElement('span');
      summary.className = 'recent-chat-summary';
      summary.textContent = escapeText(item.summary || '');
      copy.append(name, summary);
      link.append(kind, copy);
      list.appendChild(link);
    });
  }

  async function refresh(force = false) {
    const now = Date.now();
    if (loading || (!force && now - lastLoaded < 5000)) return;
    loading = true;
    try {
      const response = await fetch('/account/sidebar/recent.json', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (payload?.ok) {
        render(payload.items || []);
        lastLoaded = Date.now();
      }
    } catch (_) {
      // Sidebar history is convenience UI; never break the main page when it cannot load.
    } finally {
      loading = false;
    }
  }

  refresh(true);
  window.addEventListener('focus', () => refresh(false));

  const history = document.querySelector('.chat-history');
  if (history && typeof MutationObserver !== 'undefined') {
    let timer = 0;
    const observer = new MutationObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => refresh(true), 900);
    });
    observer.observe(history, { childList: true });
  }
})();
