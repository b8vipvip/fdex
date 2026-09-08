(() => {
  const beijingFormatter = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  const ICONS = {
    copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2"></rect><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"></path></svg>',
    like: '<svg viewBox="0 0 24 24"><path d="M7 10v11H3V10h4Zm0 9h10.2a2 2 0 0 0 1.94-1.52l1.5-6A2 2 0 0 0 18.7 9H14l.7-3.5A2.5 2.5 0 0 0 12.25 2L7 10Z"></path></svg>',
    share: '<svg viewBox="0 0 24 24"><path d="M12 16V3"></path><path d="m7 8 5-5 5 5"></path><path d="M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6"></path></svg>',
    retry: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.34 5.66"></path><path d="M20 4v7h-7"></path></svg>',
  };

  function runtimeLog(level, event, message = '', details = {}) {
    const logger = window.FdexRuntimeLog;
    const fn = logger?.[level];
    if (typeof fn === 'function') fn.call(logger, 'web_chat', event, message, details);
  }

  function beijingTime(value) {
    const date = value ? new Date(value) : new Date();
    if (Number.isNaN(date.getTime())) return value || '';
    return `${beijingFormatter.format(date).replaceAll('/', '-')} 北京时间`;
  }

  function replaceIsoTime(text) {
    if (!text) return text;
    const iso = text.match(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})/);
    return iso ? text.replace(iso[0], beijingTime(iso[0])) : text;
  }

  function installActionStyles() {
    if (document.getElementById('fdex-chat-actions-style')) return;
    const style = document.createElement('style');
    style.id = 'fdex-chat-actions-style';
    style.textContent = '.fdex-message-actions{display:flex;align-items:center;gap:2px;margin-top:7px;min-height:30px}.chat-bubble.user .fdex-message-actions{justify-content:flex-end}.fdex-message-action{width:30px;height:30px;border:0;background:transparent;color:#8b98aa;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;padding:6px}.fdex-message-action:hover{background:rgba(148,163,184,.12);color:#d7e1ee}.fdex-message-action[aria-pressed="true"]{color:#22c55e;background:rgba(34,197,94,.12)}.fdex-message-action svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.fdex-action-status{font-size:12px;color:#94a3b8;margin-left:5px}.fdex-paste-preview{font-size:12px;color:#7dd3fc;margin-top:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.composer.fdex-paste-ready{outline:1px dashed rgba(45,212,191,.65);outline-offset:4px;border-radius:10px}';
    document.head.appendChild(style);
  }

  const bubbleText = (article) => article?.querySelector('.chat-content')?.textContent?.trim() || '';
  const feedbackKey = (article) => `fdex-chat-like:${location.pathname}:${article?.dataset?.messageId || bubbleText(article).slice(0, 160)}`;

  function setActionStatus(bar, text) {
    const node = bar.querySelector('.fdex-action-status');
    if (!node) return;
    node.textContent = text;
    setTimeout(() => { if (node.textContent === text) node.textContent = ''; }, 1600);
  }

  async function copyText(text) {
    if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
    const helper = document.createElement('textarea');
    helper.value = text; helper.style.position = 'fixed'; helper.style.opacity = '0';
    document.body.appendChild(helper); helper.select(); document.execCommand('copy'); helper.remove();
  }

  function cleanRegenerateText(text) {
    return String(text || '').split('\n').filter((line) => !/^\[附件：.*\]$/.test(line.trim())).join('\n').trim();
  }

  function regenerateSource(article) {
    if (article.classList.contains('user')) return cleanRegenerateText(bubbleText(article));
    let node = article.previousElementSibling;
    while (node) {
      if (node.classList?.contains('chat-bubble') && node.classList.contains('user')) return cleanRegenerateText(bubbleText(node));
      node = node.previousElementSibling;
    }
    return '';
  }

  function actionButton(action, label) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'fdex-message-action'; button.dataset.action = action;
    button.title = label; button.setAttribute('aria-label', label); button.innerHTML = ICONS[action];
    return button;
  }

  function decorateBubble(article, form) {
    if (!(article instanceof HTMLElement) || article.querySelector('.fdex-message-actions')) return;
    const body = article.querySelector('.chat-content');
    if (!body) return;
    const bar = document.createElement('div'); bar.className = 'fdex-message-actions';
    const copy = actionButton('copy', '复制');
    const like = actionButton('like', '点赞');
    const share = actionButton('share', '分享');
    const retry = actionButton('retry', '重新生成');
    const status = document.createElement('span'); status.className = 'fdex-action-status';
    try { like.setAttribute('aria-pressed', localStorage.getItem(feedbackKey(article)) === '1' ? 'true' : 'false'); } catch (_) { like.setAttribute('aria-pressed', 'false'); }

    copy.addEventListener('click', async () => {
      try { await copyText(bubbleText(article)); setActionStatus(bar, '已复制'); }
      catch (error) { setActionStatus(bar, '复制失败'); runtimeLog('error', 'message_copy_failed', String(error)); }
    });
    like.addEventListener('click', () => {
      const active = like.getAttribute('aria-pressed') !== 'true';
      like.setAttribute('aria-pressed', active ? 'true' : 'false');
      try { localStorage.setItem(feedbackKey(article), active ? '1' : '0'); } catch (_) {}
      setActionStatus(bar, active ? '已点赞' : '已取消');
      runtimeLog('info', 'message_feedback', active ? 'liked' : 'unliked');
    });
    share.addEventListener('click', async () => {
      const text = bubbleText(article);
      try {
        if (navigator.share) { await navigator.share({ title: 'FDEX 智体消息', text }); setActionStatus(bar, '已分享'); }
        else { await copyText(text); setActionStatus(bar, '已复制，可分享'); }
      } catch (error) { if (error?.name !== 'AbortError') setActionStatus(bar, '分享失败'); }
    });
    retry.addEventListener('click', () => {
      const source = regenerateSource(article);
      if (!source) { setActionStatus(bar, '原消息只有附件，需重新附加文件'); return; }
      const targetForm = form || document.querySelector('.chat-shell form.composer');
      const textarea = targetForm?.querySelector('textarea[name="message"]');
      if (!targetForm || !textarea) return;
      textarea.value = source; textarea.dispatchEvent(new Event('input', { bubbles: true }));
      setActionStatus(bar, '正在重新生成…'); submitAgentChat(targetForm);
    });
    bar.append(copy, like, share, retry, status); body.insertAdjacentElement('afterend', bar);
  }

  function textBubble(role, label, content, form) {
    const article = document.createElement('article'); article.className = `chat-bubble ${role}`;
    const meta = document.createElement('div'); meta.className = 'fine'; meta.textContent = label;
    const body = document.createElement('div'); body.className = 'chat-content'; body.textContent = content;
    article.append(meta, body); decorateBubble(article, form); return { article, body, meta };
  }

  function scrollHistory(history) { if (history) history.scrollTop = history.scrollHeight; }
  function agentName(form) { return (form.closest('.page') || document).querySelector('.hero h1')?.textContent?.trim() || '智体'; }
  function toolSummary(payload) {
    const events = Array.isArray(payload?.tool_events) ? payload.tool_events : [];
    return events.filter((item) => item && item.summary).map((item) => String(item.summary)).slice(0, 3).join('；');
  }

  function updatePastePreview(form, file) {
    let preview = form.querySelector('.fdex-paste-preview');
    if (!preview) { preview = document.createElement('div'); preview.className = 'fdex-paste-preview'; form.appendChild(preview); }
    preview.textContent = file ? `已粘贴附件：${file.name || 'clipboard-file'}` : '';
    form.classList.toggle('fdex-paste-ready', Boolean(file));
  }

  function attachClipboardFile(form, file) {
    const input = form.querySelector('input[name="attachment"]');
    if (!input || !file) return false;
    try {
      const transfer = new DataTransfer(); transfer.items.add(file); input.files = transfer.files;
      input.dispatchEvent(new Event('change', { bubbles: true })); updatePastePreview(form, file); return true;
    } catch (error) { runtimeLog('error', 'paste_attachment_failed', String(error)); return false; }
  }

  async function submitAgentChat(form) {
    const history = form.closest('.chat-shell')?.querySelector('.chat-history');
    const textarea = form.querySelector('textarea[name="message"]');
    const file = form.querySelector('input[name="attachment"]');
    const button = form.querySelector('button[type="submit"]');
    if (!history || !textarea || !button || button.disabled) return false;
    const message = textarea.value.trim(); const attachmentName = file?.files?.[0]?.name || '';
    if (!message && !attachmentName) return false;

    const name = agentName(form);
    const display = [message, attachmentName ? `[附件：${attachmentName}]` : ''].filter(Boolean).join('\n');
    const mine = textBubble('user', `我 · ${beijingTime()}`, display, form);
    const pending = textBubble('assistant', `${name} · 正在处理`, '正在连接 FDEX AI 线路；同时判断是否需要调用 FDEX Agent / GitHub 工具…', form);
    history.append(mine.article, pending.article); scrollHistory(history);

    const data = new FormData(form); const oldLabel = button.textContent;
    button.disabled = true; button.textContent = '发送中…'; textarea.value = '';
    const url = form.action.replace(/\/send$/, '/send-json'); const path = new URL(url, location.href).pathname;
    const started = performance.now();
    runtimeLog('info', 'send_start', 'Web chat request started', { path, message_chars: message.length, has_attachment: Boolean(attachmentName) });

    try {
      const response = await fetch(url, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-FDEX-Web-Chat': '1', Accept: 'application/json' } });
      const payload = await response.json().catch(() => null);
      if (!payload) throw new Error(`服务端返回无法解析的响应（HTTP ${response.status}）`);
      if (payload.user_message?.id) mine.article.dataset.messageId = String(payload.user_message.id);
      mine.meta.textContent = `我 · ${beijingTime(payload.user_message?.created_at) || beijingTime()}`;
      const tools = toolSummary(payload); const toolCount = Array.isArray(payload?.tool_events) ? payload.tool_events.length : 0;
      if (!payload.ok) {
        runtimeLog('error', 'send_failed', payload.error || 'Web chat response failed', { path, http_status: response.status, elapsed_ms: Math.round(performance.now() - started), tool_event_count: toolCount });
        pending.meta.textContent = `${name} · 回复失败${tools ? ` · ${tools}` : ''}`;
        pending.body.textContent = payload.error || `AI 线路调用失败（HTTP ${response.status}）`; pending.article.style.borderColor = '#ef4444';
      } else {
        if (payload.assistant_message?.id) pending.article.dataset.messageId = String(payload.assistant_message.id);
        runtimeLog('info', 'send_completed', 'Web chat response completed', { path, http_status: response.status, elapsed_ms: Math.round(performance.now() - started), tool_event_count: toolCount, response_chars: String(payload.assistant_message?.content || '').length });
        pending.meta.textContent = `${name} · ${beijingTime(payload.assistant_message?.created_at) || beijingTime()}${tools ? ` · ${tools}` : ''}`;
        pending.body.textContent = payload.assistant_message?.content || '';
        if (file) file.value = ''; updatePastePreview(form, null);
      }
    } catch (error) {
      runtimeLog('error', 'send_exception', error instanceof Error ? error.message : String(error), { path, elapsed_ms: Math.round(performance.now() - started), error_type: error?.name || 'Error' });
      pending.meta.textContent = `${name} · 网络异常 · ${beijingTime()}`; pending.body.textContent = error instanceof Error ? error.message : String(error); pending.article.style.borderColor = '#ef4444';
    } finally {
      button.disabled = false; button.textContent = oldLabel || '发送'; textarea.focus(); scrollHistory(history);
    }
    return true;
  }

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.classList.contains('composer')) return;
    if (!/\/account\/chat\/employee\/\d+\/send$/.test(new URL(form.action).pathname)) return;
    event.preventDefault(); submitAgentChat(form);
  });

  document.addEventListener('DOMContentLoaded', () => {
    installActionStyles();
    const form = document.querySelector('.chat-shell form.composer');
    const history = document.querySelector('.chat-shell .chat-history');
    const textarea = form?.querySelector('textarea[name="message"]');
    const file = form?.querySelector('input[name="attachment"]');
    document.querySelectorAll('.chat-history .fine').forEach((node) => { node.textContent = replaceIsoTime(node.textContent || ''); });
    document.querySelectorAll('.chat-history .chat-bubble').forEach((article) => decorateBubble(article, form));
    if (textarea && form) {
      textarea.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
        event.preventDefault(); submitAgentChat(form);
      });
      textarea.addEventListener('paste', (event) => {
        const files = Array.from(event.clipboardData?.files || []);
        if (files.length && attachClipboardFile(form, files[0])) {
          event.preventDefault();
          runtimeLog('info', 'paste_attachment', 'Clipboard attachment accepted', { name: files[0].name || '', type: files[0].type || '', size: files[0].size || 0 });
        }
      });
    }
    if (file && form) file.addEventListener('change', () => updatePastePreview(form, file.files?.[0] || null));
    scrollHistory(history);
  });
})();