(() => {
  const beijingFormatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });

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
    if (!iso) return text;
    return text.replace(iso[0], beijingTime(iso[0]));
  }

  function textBubble(role, label, content) {
    const article = document.createElement('article');
    article.className = `chat-bubble ${role}`;
    const meta = document.createElement('div');
    meta.className = 'fine';
    meta.textContent = label;
    const body = document.createElement('div');
    body.className = 'chat-content';
    body.textContent = content;
    article.append(meta, body);
    return { article, body, meta };
  }

  function scrollHistory(history) {
    if (history) history.scrollTop = history.scrollHeight;
  }

  function agentName(form) {
    const shell = form.closest('.page') || document;
    const title = shell.querySelector('.hero h1');
    return title?.textContent?.trim() || '智体';
  }

  function toolSummary(payload) {
    const events = Array.isArray(payload?.tool_events) ? payload.tool_events : [];
    return events
      .filter((item) => item && item.summary)
      .map((item) => String(item.summary))
      .slice(0, 3)
      .join('；');
  }

  async function submitAgentChat(form) {
    const history = form.closest('.chat-shell')?.querySelector('.chat-history');
    const textarea = form.querySelector('textarea[name="message"]');
    const file = form.querySelector('input[name="attachment"]');
    const button = form.querySelector('button[type="submit"]');
    if (!history || !textarea || !button) return false;

    const message = textarea.value.trim();
    const attachmentName = file?.files?.[0]?.name || '';
    if (!message && !attachmentName) return false;

    const name = agentName(form);
    const display = [message, attachmentName ? `[附件：${attachmentName}]` : ''].filter(Boolean).join('\n');
    const mine = textBubble('user', `我 · ${beijingTime()}`, display);
    const pending = textBubble('assistant', `${name} · 正在处理`, '正在连接 FDEX AI 线路；同时判断是否需要调用 FDEX Agent / GitHub 工具…');
    history.append(mine.article, pending.article);
    scrollHistory(history);

    const data = new FormData(form);
    const oldLabel = button.textContent;
    button.disabled = true;
    button.textContent = '发送中…';
    textarea.value = '';

    const url = form.action.replace(/\/send$/, '/send-json');
    const path = new URL(url, window.location.href).pathname;
    const started = performance.now();
    runtimeLog('info', 'send_start', 'Web chat request started', {
      path,
      message_chars: message.length,
      has_attachment: Boolean(attachmentName),
    });

    try {
      const response = await fetch(url, {
        method: 'POST',
        body: data,
        credentials: 'same-origin',
        headers: { 'X-FDEX-Web-Chat': '1', Accept: 'application/json' },
      });
      const payload = await response.json().catch(() => null);
      if (!payload) throw new Error(`服务端返回无法解析的响应（HTTP ${response.status}）`);

      mine.meta.textContent = `我 · ${beijingTime(payload.user_message?.created_at) || beijingTime()}`;
      const tools = toolSummary(payload);
      const toolCount = Array.isArray(payload?.tool_events) ? payload.tool_events.length : 0;
      if (!payload.ok) {
        runtimeLog('error', 'send_failed', payload.error || 'Web chat response failed', {
          path,
          http_status: response.status,
          elapsed_ms: Math.round(performance.now() - started),
          tool_event_count: toolCount,
        });
        pending.meta.textContent = `${name} · 回复失败${tools ? ` · ${tools}` : ''}`;
        pending.body.textContent = payload.error || `AI 线路调用失败（HTTP ${response.status}）`;
        pending.article.style.borderColor = '#ef4444';
      } else {
        runtimeLog('info', 'send_completed', 'Web chat response completed', {
          path,
          http_status: response.status,
          elapsed_ms: Math.round(performance.now() - started),
          tool_event_count: toolCount,
          response_chars: String(payload.assistant_message?.content || '').length,
        });
        pending.meta.textContent = `${name} · ${beijingTime(payload.assistant_message?.created_at) || beijingTime()}${tools ? ` · ${tools}` : ''}`;
        pending.body.textContent = payload.assistant_message?.content || '';
        if (file) file.value = '';
      }
    } catch (error) {
      runtimeLog('error', 'send_exception', error instanceof Error ? error.message : String(error), {
        path,
        elapsed_ms: Math.round(performance.now() - started),
        error_type: error?.name || 'Error',
      });
      pending.meta.textContent = `${name} · 网络异常 · ${beijingTime()}`;
      pending.body.textContent = error instanceof Error ? error.message : String(error);
      pending.article.style.borderColor = '#ef4444';
    } finally {
      button.disabled = false;
      button.textContent = oldLabel || '发送';
      textarea.focus();
      scrollHistory(history);
    }
    return true;
  }

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.classList.contains('composer')) return;
    if (!/\/account\/chat\/employee\/\d+\/send$/.test(new URL(form.action).pathname)) return;
    event.preventDefault();
    submitAgentChat(form);
  });

  document.addEventListener('DOMContentLoaded', () => {
    const history = document.querySelector('.chat-shell .chat-history');
    document.querySelectorAll('.chat-history .fine').forEach((node) => {
      node.textContent = replaceIsoTime(node.textContent || '');
    });
    scrollHistory(history);
  });
})();