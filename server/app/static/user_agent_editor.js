(() => {
  const form = document.querySelector('form[action="/account/employees"][method="post"]');
  if (!form) return;
  const prompt = form.querySelector('textarea[name="role_prompt"]');
  const csrf = form.querySelector('input[name="csrf_token"]');
  if (!prompt || !csrf) return;

  const card = form.closest('.card');
  if (card) {
    card.id = 'new-agent';
    card.classList.add('agent-create-card');
    const intro = card.querySelector('p.fine');
    if (intro) intro.textContent = '先命名智体并用一句话说明它负责什么；身份定义提示词可以自己写，也可以交给 AI 整理。';
  }

  const field = (labelText, input) => {
    const wrap = document.createElement('div');
    wrap.className = 'agent-editor-field';
    const label = document.createElement('label');
    label.htmlFor = input.id;
    label.textContent = labelText;
    wrap.append(label, input);
    return wrap;
  };

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
  description.required = true;
  description.maxLength = 160;
  description.autocomplete = 'off';
  description.placeholder = '一句话描述这个智体负责什么';
  const descriptionField = field('一句话描述智体', description);
  const hint = document.createElement('div');
  hint.className = 'agent-description-hint';
  hint.textContent = '用于快速理解智体用途，建议控制在一句话内。';
  descriptionField.appendChild(hint);

  const oldPromptLabel = prompt.closest('label');
  const promptField = document.createElement('div');
  promptField.className = 'agent-editor-field';
  const promptLabel = document.createElement('label');
  promptLabel.htmlFor = 'agent-create-role-prompt';
  promptLabel.textContent = '身份定义提示词';
  prompt.id = 'agent-create-role-prompt';
  prompt.placeholder = '描述智体的身份、职责、工作方式、边界和输出要求。';
  const promptWrap = document.createElement('div');
  promptWrap.className = 'agent-prompt-wrap';
  promptWrap.appendChild(prompt);
  const aiButton = document.createElement('button');
  aiButton.type = 'button';
  aiButton.className = 'agent-prompt-ai';
  aiButton.textContent = 'AI整理提示词';
  aiButton.title = '根据名称、一句话描述和当前提示词，调用 FDEX AI 自动整理';
  promptWrap.appendChild(aiButton);
  const status = document.createElement('div');
  status.className = 'agent-prompt-status';
  promptField.append(promptLabel, promptWrap, status);

  if (oldPromptLabel) {
    oldPromptLabel.replaceWith(field('智体名称', name), descriptionField, promptField);
  } else {
    const submit = form.querySelector('button[type="submit"]');
    form.insertBefore(field('智体名称', name), submit);
    form.insertBefore(descriptionField, submit);
    form.insertBefore(promptField, submit);
  }

  aiButton.addEventListener('click', async () => {
    const cleanName = name.value.trim();
    const cleanDescription = description.value.trim();
    if (!cleanName || !cleanDescription) {
      status.className = 'agent-prompt-status error';
      status.textContent = '请先填写智体名称和一句话描述。';
      (!cleanName ? name : description).focus();
      return;
    }
    const oldText = aiButton.textContent;
    aiButton.disabled = true;
    aiButton.textContent = '整理中…';
    status.className = 'agent-prompt-status';
    status.textContent = '正在调用 FDEX AI 整理身份定义提示词…';
    try {
      const data = new FormData();
      data.set('csrf_token', csrf.value);
      data.set('name', cleanName);
      data.set('description', cleanDescription);
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
      status.textContent = `已由 ${payload.provider || 'FDEX AI'} 整理，可继续手动修改后创建。`;
    } catch (error) {
      status.className = 'agent-prompt-status error';
      status.textContent = error instanceof Error ? error.message : String(error);
    } finally {
      aiButton.disabled = false;
      aiButton.textContent = oldText;
    }
  });

  const params = new URLSearchParams(location.search);
  if (params.get('new') === '1' && card) {
    card.classList.add('agent-new-highlight');
    requestAnimationFrame(() => {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      name.focus({ preventScroll: true });
    });
  }
})();
