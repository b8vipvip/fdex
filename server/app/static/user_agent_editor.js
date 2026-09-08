(() => {
  const path = location.pathname.replace(/\/$/, '') || '/';

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function makeModal(id, title) {
    const overlay = el('div', 'management-modal');
    overlay.id = id;
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');

    const panel = el('section', 'management-modal-panel');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', `${id}-title`);

    const head = el('div', 'management-modal-head');
    const heading = el('h2', '', title);
    heading.id = `${id}-title`;
    const closeButton = el('button', 'management-modal-close', '×');
    closeButton.type = 'button';
    closeButton.setAttribute('aria-label', '关闭');
    head.append(heading, closeButton);

    const body = el('div', 'management-modal-body');
    panel.append(head, body);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    const close = () => {
      overlay.hidden = true;
      overlay.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('management-modal-open');
    };
    const open = () => {
      overlay.hidden = false;
      overlay.setAttribute('aria-hidden', 'false');
      document.body.classList.add('management-modal-open');
      requestAnimationFrame(() => panel.querySelector('input,textarea,select,button')?.focus());
    };
    closeButton.addEventListener('click', close);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !overlay.hidden) close();
    });
    return { overlay, panel, body, open, close, setTitle: (value) => { heading.textContent = value; } };
  }

  function field(labelText, input, optional = false) {
    const wrap = el('div', 'agent-editor-field');
    const label = document.createElement('label');
    if (input.id) label.htmlFor = input.id;
    label.textContent = optional ? `${labelText}（选填）` : labelText;
    wrap.append(label, input);
    return wrap;
  }

  function promptField(form, prompt, name, description, idPrefix) {
    const wrap = el('div', 'agent-editor-field');
    const label = document.createElement('label');
    prompt.id = `${idPrefix}-role-prompt`;
    label.htmlFor = prompt.id;
    label.textContent = '身份定义提示词（选填）';
    prompt.placeholder = '描述智体的身份、职责、工作方式、边界和输出要求。';

    const promptWrap = el('div', 'agent-prompt-wrap');
    promptWrap.appendChild(prompt);
    const aiButton = el('button', 'agent-prompt-ai', 'AI整理提示词');
    aiButton.type = 'button';
    aiButton.title = '根据智体名称和已填写的信息，调用 FDEX AI 自动整理';
    promptWrap.appendChild(aiButton);
    const status = el('div', 'agent-prompt-status');
    wrap.append(label, promptWrap, status);

    aiButton.addEventListener('click', async () => {
      const cleanName = name.value.trim();
      if (!cleanName) {
        status.className = 'agent-prompt-status error';
        status.textContent = '请先填写智体名称。';
        name.focus();
        return;
      }
      const csrf = form.querySelector('input[name="csrf_token"]');
      if (!csrf) return;
      const oldText = aiButton.textContent;
      aiButton.disabled = true;
      aiButton.textContent = '整理中…';
      status.className = 'agent-prompt-status';
      status.textContent = '正在调用 FDEX AI 整理身份定义提示词…';
      try {
        const data = new FormData();
        data.set('csrf_token', csrf.value);
        data.set('name', cleanName);
        data.set('description', description?.value.trim() || '');
        data.set('role_prompt', prompt.value.trim());
        const response = await fetch('/account/employees/prompt/organize', {
          method: 'POST',
          body: data,
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload?.ok || !payload?.prompt) {
          throw new Error(payload?.error || `AI整理失败（HTTP ${response.status}）`);
        }
        prompt.value = String(payload.prompt).trim();
        prompt.dispatchEvent(new Event('input', { bubbles: true }));
        status.className = 'agent-prompt-status success';
        status.textContent = `已由 ${payload.provider || 'FDEX AI'} 整理，可继续手动修改。`;
      } catch (error) {
        status.className = 'agent-prompt-status error';
        status.textContent = error instanceof Error ? error.message : String(error);
      } finally {
        aiButton.disabled = false;
        aiButton.textContent = oldText;
      }
    });
    return wrap;
  }

  function enhanceCreateAgentForm(form) {
    if (form.dataset.agentEnhanced === '1') return form;
    const prompt = form.querySelector('textarea[name="role_prompt"]');
    if (!prompt) return form;
    form.dataset.agentEnhanced = '1';

    const name = document.createElement('input');
    name.type = 'text';
    name.id = 'agent-create-name';
    name.name = 'name';
    name.required = true;
    name.maxLength = 80;
    name.autocomplete = 'off';
    name.placeholder = '例如：代码审查助手';

    const description = document.createElement('input');
    description.type = 'text';
    description.id = 'agent-create-description';
    description.name = 'description';
    description.required = false;
    description.maxLength = 160;
    description.autocomplete = 'off';
    description.placeholder = '例如：负责审查代码并指出风险';
    const descriptionField = field('一句话描述智体', description, true);
    const hint = el('div', 'agent-description-hint', '可留空；用于快速说明智体用途。');
    descriptionField.appendChild(hint);

    const oldPromptLabel = prompt.closest('label');
    const nameField = field('智体名称', name);
    const roleField = promptField(form, prompt, name, description, 'agent-create');
    if (oldPromptLabel) {
      oldPromptLabel.replaceWith(nameField, descriptionField, roleField);
    } else {
      const submit = form.querySelector('button[type="submit"]');
      form.insertBefore(nameField, submit);
      form.insertBefore(descriptionField, submit);
      form.insertBefore(roleField, submit);
    }
    return form;
  }

  function createCheck(name, text) {
    const label = el('label', 'check');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.name = name;
    label.append(input, document.createTextNode(text));
    return { label, input };
  }

  function initEmployees() {
    if (path !== '/account/employees') return;
    const page = document.querySelector('main.page');
    const createForm = page?.querySelector('form[action="/account/employees"][method="post"]');
    if (!page || !createForm) return;
    enhanceCreateAgentForm(createForm);

    const createCard = createForm.closest('.card');
    const grid = createCard?.closest('.grid');
    if (!createCard || !grid) return;
    const cards = Array.from(grid.children).filter((item) => item.classList?.contains('card'));
    const listCard = cards.find((item) => item !== createCard);
    if (!listCard) return;

    const createModal = makeModal('agent-create-modal', '新建智体');
    const createHint = el('p', 'management-modal-hint', '仅“智体名称”为必填；一句话描述和身份定义提示词均可留空。');
    createModal.body.append(createHint, createForm);
    createCard.remove();

    const hero = page.querySelector('.hero');
    if (hero) {
      const actions = hero.lastElementChild;
      if (actions && actions !== hero.firstElementChild) actions.remove();
      const newButton = el('button', 'button button-primary', '＋ 新建智体');
      newButton.type = 'button';
      newButton.addEventListener('click', createModal.open);
      hero.appendChild(newButton);
    }

    const details = Array.from(listCard.querySelectorAll('details.repo'));
    const listHead = el('div', 'management-card-head');
    const headCopy = el('div');
    headCopy.append(el('h2', '', '智体列表'), el('p', 'fine', '点击智体直接进入对话；右侧可单独进入对话或编辑设置。'));
    const right = el('div', 'management-head-actions');
    right.append(el('span', 'badge', String(details.length)));
    const inlineNew = el('button', 'button button-primary', '＋ 新建智体');
    inlineNew.type = 'button';
    inlineNew.addEventListener('click', createModal.open);
    right.appendChild(inlineNew);
    listHead.append(headCopy, right);

    const rows = el('div', 'agent-list');
    if (!details.length) rows.appendChild(el('p', 'muted', '暂无智体。'));

    const csrfValue = createForm.querySelector('input[name="csrf_token"]')?.value || '';
    const editModal = makeModal('agent-edit-modal', '编辑智体');
    const editForm = document.createElement('form');
    editForm.method = 'post';
    editForm.className = 'stack';
    const csrf = document.createElement('input');
    csrf.type = 'hidden'; csrf.name = 'csrf_token'; csrf.value = csrfValue;
    const editName = document.createElement('input');
    editName.type = 'text'; editName.name = 'name'; editName.required = true; editName.maxLength = 80;
    const editDescription = document.createElement('input');
    editDescription.type = 'text'; editDescription.name = 'description'; editDescription.maxLength = 160;
    editDescription.placeholder = '可留空';
    const editPrompt = document.createElement('textarea');
    editPrompt.name = 'role_prompt'; editPrompt.rows = 9; editPrompt.maxLength = 12000;
    const editChecks = el('div', 'management-check-grid');
    const active = createCheck('active', '启用');
    const knowledgeRead = createCheck('knowledge_read', '读取知识');
    const knowledgeWrite = createCheck('knowledge_write', '写入共享知识');
    const coding = createCheck('coding_agent', 'Coding Agent');
    editChecks.append(active.label, knowledgeRead.label, knowledgeWrite.label, coding.label);
    const editActions = el('div', 'management-modal-actions');
    const cancelEdit = el('button', 'button button-secondary', '取消'); cancelEdit.type = 'button';
    cancelEdit.addEventListener('click', editModal.close);
    const saveEdit = el('button', 'button button-primary', '保存'); saveEdit.type = 'submit';
    editActions.append(cancelEdit, saveEdit);
    editForm.append(
      csrf,
      field('智体名称', editName),
      field('一句话描述智体', editDescription, true),
      promptField(editForm, editPrompt, editName, editDescription, 'agent-edit'),
      editChecks,
      editActions,
    );
    editModal.body.appendChild(editForm);

    async function openEdit(id) {
      editModal.setTitle('编辑智体');
      editForm.action = `/account/employees/${id}`;
      editName.value = '加载中…';
      editName.disabled = true;
      editDescription.value = '';
      editPrompt.value = '';
      editModal.open();
      try {
        const response = await fetch(`/account/employees/${id}.json`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload?.ok || !payload.employee) throw new Error(payload?.error || `读取失败（HTTP ${response.status}）`);
        const item = payload.employee;
        editName.disabled = false;
        editName.value = item.name || '';
        editDescription.value = item.description || '';
        editPrompt.value = item.role_prompt || '';
        active.input.checked = Boolean(item.active);
        knowledgeRead.input.checked = Boolean(item.knowledge_read);
        knowledgeWrite.input.checked = Boolean(item.knowledge_write);
        coding.input.checked = Boolean(item.coding_agent);
        editModal.setTitle(`编辑智体 · ${item.name || ''}`);
        editName.focus();
      } catch (error) {
        editName.disabled = false;
        editName.value = '';
        editModal.body.querySelector('.agent-prompt-status')?.replaceChildren(document.createTextNode(error instanceof Error ? error.message : String(error)));
      }
    }

    details.forEach((detail) => {
      const summary = detail.querySelector('summary');
      const oldForm = detail.querySelector('form[action^="/account/employees/"]');
      const match = oldForm?.action.match(/\/account\/employees\/(\d+)$/);
      if (!match) return;
      const id = match[1];
      const nameText = summary?.querySelector('strong')?.textContent?.trim() || `智体 ${id}`;
      const metaText = summary?.querySelector('.fine')?.textContent?.trim() || '';
      const chatUrl = `/account/chat/employee/${id}`;

      const row = el('div', 'agent-list-row');
      row.tabIndex = 0;
      row.setAttribute('role', 'link');
      row.setAttribute('aria-label', `与 ${nameText} 对话`);
      const copy = el('div', 'agent-list-copy');
      copy.append(el('strong', 'agent-list-name', nameText), el('span', 'agent-list-meta', metaText || '智体'));
      const actions = el('div', 'agent-list-actions');
      const chat = el('a', 'button button-secondary compact-button', '对话');
      chat.href = chatUrl;
      const edit = el('button', 'button button-secondary compact-button', '编辑');
      edit.type = 'button';
      edit.addEventListener('click', (event) => { event.stopPropagation(); openEdit(id); });
      actions.append(chat, edit);
      row.append(copy, actions);
      row.addEventListener('click', (event) => {
        if (event.target.closest('a,button')) return;
        location.href = chatUrl;
      });
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          location.href = chatUrl;
        }
      });
      rows.appendChild(row);
    });

    listCard.className = 'card span-12 stack management-list-card';
    listCard.replaceChildren(listHead, rows);
    grid.classList.add('management-single-grid');

    const params = new URLSearchParams(location.search);
    if (params.get('new') === '1') createModal.open();
  }

  function initKnowledge() {
    if (path !== '/account/knowledge') return;
    const page = document.querySelector('main.page');
    const grid = page?.querySelector('.grid');
    if (!page || !grid) return;
    const cards = Array.from(grid.children).filter((item) => item.classList?.contains('card'));
    const createCard = cards.find((card) => card.querySelector('form[action="/account/knowledge"][method="post"]'));
    const listCard = cards.find((card) => card !== createCard);
    const createForm = createCard?.querySelector('form[action="/account/knowledge"][method="post"]');
    if (!createCard || !listCard || !createForm) return;

    const modal = makeModal('knowledge-create-modal', '新增知识');
    modal.body.appendChild(createForm);
    createCard.remove();

    const header = el('div', 'management-card-head');
    const copy = el('div');
    copy.append(el('h2', '', '知识列表'), el('p', 'fine', '浏览、检索、归档当前账号的长期知识。'));
    const add = el('button', 'button button-primary', '＋ 新增知识');
    add.type = 'button';
    add.addEventListener('click', modal.open);
    header.append(copy, add);
    listCard.insertBefore(header, listCard.firstChild);
    listCard.classList.remove('span-7');
    listCard.classList.add('span-12', 'management-list-card', 'knowledge-list-card');
    grid.classList.add('management-single-grid');
  }

  initEmployees();
  initKnowledge();
})();
