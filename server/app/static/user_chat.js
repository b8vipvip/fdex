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
    if (!history) return;
    history.scrollTop = history.scrollHeight;
  }

  function employeeName(form) {
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

  function removeField(form, name) {
    const field = form.querySelector(`[name="${name}"]`);
    if (!field) return;
    const label = field.closest('label') || field.parentElement;
    if (label && label !== form) label.remove();
    else field.remove();
  }

  function simplifyAgentForms() {
    const create = document.querySelector('form[action="/account/employees"]');
    if (create) {
      ['name', 'department', 'position', 'industry', 'knowledge_read', 'knowledge_write', 'coding_agent'].forEach((name) => removeField(create, name));
      const prompt = create.querySelector('textarea[name="role_prompt"]');
      if (prompt) {
        prompt.required = false;
        prompt.placeholder = '可留空。也可以直接定义身份，例如：你是我的语文老师，擅长讲解阅读理解和作文，并根据我的水平循序渐进教学。';
        const label = prompt.closest('label');
        if (label) label.childNodes[0].textContent = '身份定义提示词（可选）';
      }
      const button = create.querySelector('button[type="submit"]');
      if (button) button.textContent = '创建智体';
      const heading = create.closest('.card')?.querySelector('h2');
      if (heading) heading.textContent = '添加智体';
      if (!create.querySelector('[data-agent-create-help]')) {
        const help = document.createElement('p');
        help.className = 'fine';
        help.dataset.agentCreateHelp = '1';
        help.textContent = '创建时只需填写身份定义提示词，也可以留空。系统会自动分配“智体 1、智体 2…”作为显示名称，创建后可再修改名称和权限。';
        const promptLabel = prompt?.closest('label');
        if (promptLabel) promptLabel.before(help);
      }
    }

    document.querySelectorAll('form[action^="/account/employees/"]').forEach((form) => {
      ['department', 'position', 'industry'].forEach((name) => removeField(form, name));
      const name = form.querySelector('input[name="name"]');
      if (name) {
        name.required = false;
        const label = name.closest('label');
        if (label) label.childNodes[0].textContent = '显示名称（可选）';
      }
      const prompt = form.querySelector('textarea[name="role_prompt"]');
      if (prompt) {
        const label = prompt.closest('label');
        if (label) label.childNodes[0].textContent = '身份定义提示词（可选）';
        prompt.placeholder = '例如：你是我的数学老师；你是我的体育训练伙伴；也可以留空作为通用智体。';
      }
    });
  }

  function rewriteAgentCards() {
    document.querySelectorAll('a[href^="/account/chat/employee/"] .fine').forEach((node) => {
      node.textContent = (node.textContent || '').includes('Coding Agent') ? 'Coding Agent 智体' : '智体';
    });
    document.querySelectorAll('details.repo summary').forEach((summary) => {
      const strong = summary.querySelector('strong');
      if (strong && strong.textContent?.includes(' · ')) strong.textContent = strong.textContent.split(' · ')[0];
      const fine = summary.querySelector('.fine');
      if (fine) fine.textContent = (fine.textContent || '').includes('已停用') ? '已停用' : '已启用';
    });
    const path = location.pathname;
    if (/^\/account\/chat\/employee\/\d+$/.test(path)) {
      const hero = document.querySelector('.hero');
      const info = hero?.querySelector('p');
      if (info) info.textContent = (info.textContent || '').includes('Coding Agent') ? 'Coding Agent 智体' : '智体';
    }
    document.querySelectorAll('label.check').forEach((label) => {
      if (!label.querySelector('input[name="member_ids"]')) return;
      const input = label.querySelector('input');
      const raw = label.textContent || '';
      const name = raw.split(' · ')[0].trim();
      label.textContent = '';
      if (input) label.append(input, document.createTextNode(` ${name}`));
    });
    document.querySelectorAll('.badge').forEach((badge) => {
      if ((badge.textContent || '').includes(' · ')) badge.textContent = (badge.textContent || '').split(' · ')[0];
    });
  }

  function rewriteStructuralTerminology() {
    document.title = document.title.replaceAll('AI 员工', '智体').replaceAll('员工', '智体');
    const root = document.querySelector('.page');
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      const parent = node.parentElement;
      if (!parent || parent.closest('.chat-content,.conversation-preview,textarea,input,script,style')) return;
      let text = node.textContent || '';
      text = text
        .replaceAll('企业知识库', '知识库')
        .replaceAll('企业知识', '知识')
        .replaceAll('AI 员工', '智体')
        .replaceAll('员工', '智体')
        .replaceAll('岗位 Prompt', '身份定义提示词')
        .replaceAll('岗位说明', '身份定义')
        .replaceAll('在职', '已启用')
        .replaceAll('离职', '停用');
      node.textContent = text;
    });
  }

  function applyGeneralAgentUi() {
    simplifyAgentForms();
    rewriteAgentCards();
    rewriteStructuralTerminology();
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

    const name = employeeName(form);
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

      const userTime = beijingTime(payload.user_message?.created_at);
      mine.meta.textContent = `我 · ${userTime || beijingTime()}`;
      const tools = toolSummary(payload);

      if (!payload.ok) {
        pending.meta.textContent = `${name} · 回复失败${tools ? ` · ${tools}` : ''}`;
        pending.body.textContent = payload.error || `AI 线路调用失败（HTTP ${response.status}）`;
        pending.article.style.borderColor = '#ef4444';
      } else {
        const assistantTime = beijingTime(payload.assistant_message?.created_at);
        pending.meta.textContent = `${name} · ${assistantTime || beijingTime()}${tools ? ` · ${tools}` : ''}`;
        pending.body.textContent = payload.assistant_message?.content || '';
        if (file) file.value = '';
      }
    } catch (error) {
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
    submitEmployeeChat(form);
  });

  document.addEventListener('DOMContentLoaded', () => {
    applyGeneralAgentUi();
    const history = document.querySelector('.chat-shell .chat-history');
    document.querySelectorAll('.chat-history .fine').forEach((node) => {
      node.textContent = replaceIsoTime(node.textContent || '');
    });
    scrollHistory(history);
  });
})();
