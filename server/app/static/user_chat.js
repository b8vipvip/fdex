(() => {
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
    return { article, body };
  }

  function scrollHistory(history) {
    if (!history) return;
    history.scrollTop = history.scrollHeight;
  }

  async function submitEmployeeChat(form) {
    const history = form.closest('.chat-shell')?.querySelector('.chat-history');
    const textarea = form.querySelector('textarea[name="message"]');
    const file = form.querySelector('input[name="attachment"]');
    const button = form.querySelector('button[type="submit"]');
    if (!history || !textarea || !button) return false;

    const message = textarea.value.trim();
    const attachmentName = file?.files?.[0]?.name || '';
    if (!message && !attachmentName) return false;

    const display = [message, attachmentName ? `[附件：${attachmentName}]` : ''].filter(Boolean).join('\n');
    const mine = textBubble('user', '我 · 正在发送', display);
    const pending = textBubble('assistant', 'AI 员工 · 正在处理', '正在连接 FDEX AI 线路…');
    history.append(mine.article, pending.article);
    scrollHistory(history);

    const data = new FormData(form);
    const oldLabel = button.textContent;
    button.disabled = true;
    button.textContent = '发送中…';
    textarea.value = '';

    try {
      const url = form.action.replace(/\/send$/, '/send-json');
      const response = await fetch(url, {
        method: 'POST',
        body: data,
        credentials: 'same-origin',
        headers: { 'X-FDEX-Web-Chat': '1', 'Accept': 'application/json' },
      });
      const payload = await response.json().catch(() => null);
      if (!payload) throw new Error(`服务端返回无法解析的响应（HTTP ${response.status}）`);

      if (!payload.ok) {
        mine.article.querySelector('.fine').textContent = '我 · 已发送';
        pending.article.querySelector('.fine').textContent = 'AI 员工 · 回复失败';
        pending.body.textContent = payload.error || `AI 线路调用失败（HTTP ${response.status}）`;
        pending.article.style.borderColor = '#ef4444';
      } else {
        mine.article.querySelector('.fine').textContent = '我 · 已发送';
        pending.article.querySelector('.fine').textContent = 'AI 员工 · 已回复';
        pending.body.textContent = payload.assistant_message?.content || '';
        if (file) file.value = '';
      }
    } catch (error) {
      pending.article.querySelector('.fine').textContent = 'AI 员工 · 网络异常';
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
    submitEmployeeChat(form);
  });

  document.addEventListener('DOMContentLoaded', () => {
    const history = document.querySelector('.chat-shell .chat-history');
    scrollHistory(history);
  });
})();
