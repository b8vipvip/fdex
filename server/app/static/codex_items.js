(() => {
  "use strict";

  const panel = document.getElementById("codex-item-panel");
  if (!panel) return;

  const taskId = panel.dataset.taskId || "";
  const list = document.getElementById("codex-item-list");
  const state = document.getElementById("codex-stream-state");
  const counter = document.getElementById("codex-item-count");
  const eventCounter = document.getElementById("codex-event-count");
  const interactionPanel = document.getElementById("codex-interaction-panel");
  const interactionList = document.getElementById("codex-interaction-list");
  const interactionCounter = document.getElementById("codex-interaction-count");
  const interactionPendingCounter = document.getElementById("codex-interaction-pending-count");
  const csrfToken = interactionPanel ? String(interactionPanel.dataset.csrfToken || "") : "";
  if (!taskId || !list || !state || !counter || !eventCounter) return;

  const cards = new Map();
  const interactionCards = new Map();
  const interactionRecords = new Map();
  let lastSeq = 0;
  let eventCount = 0;
  let source = null;
  let terminalReloadTimer = null;

  const bounded = (value, limit = 50000) => {
    const text = String(value == null ? "" : value);
    return text.length > limit ? text.slice(0, limit) + `\n… FDEX UI truncated ${text.length - limit} characters` : text;
  };

  const jsonText = (value, limit = 30000) => {
    try {
      return bounded(JSON.stringify(value, null, 2), limit);
    } catch (_error) {
      return bounded(String(value), limit);
    }
  };

  const badgeClass = (status) => {
    if (["completed", "succeeded", "success", "responded"].includes(status)) return "badge ok";
    if (["failed", "error", "declined", "rejected", "orphaned", "interrupted", "cancelled", "canceled", "expired"].includes(status)) return "badge warn";
    return "badge";
  };

  const appendLine = (container, label, value, className = "") => {
    if (value === undefined || value === null || value === "") return;
    const row = document.createElement("div");
    row.className = "codex-item-line";
    const key = document.createElement("strong");
    key.textContent = `${label}: `;
    const val = document.createElement("span");
    if (className) val.className = className;
    val.textContent = bounded(value, 12000);
    row.append(key, val);
    container.appendChild(row);
  };

  const inputSummary = (content) => {
    if (!Array.isArray(content)) return "";
    return content.map((part) => {
      if (!part || typeof part !== "object") return String(part || "");
      const type = String(part.type || "input");
      if (type === "text") return String(part.text || "");
      if (type === "skill" || type === "mention") return `${type}: ${part.name || ""} ${part.path || ""}`.trim();
      if (type === "image" || type === "audio") return `${type}: ${part.url ? "attached data" : ""}`.trim();
      if (type === "localImage" || type === "localAudio") return `${type}: ${part.path || ""}`;
      return `${type}: ${jsonText(part, 2000)}`;
    }).filter(Boolean).join("\n");
  };

  const describeItem = (item) => {
    const type = String(item && item.type || "unknown");
    const detail = document.createElement("div");
    detail.className = "codex-item-detail stack";

    switch (type) {
      case "userMessage":
        appendLine(detail, "输入", inputSummary(item.content));
        break;
      case "agentMessage":
        appendLine(detail, "回复", item.text || item.message || "");
        break;
      case "plan":
        appendLine(detail, "计划", item.text || item.plan || item.explanation || jsonText(item, 12000));
        break;
      case "reasoning":
        appendLine(detail, "推理摘要", item.summary || item.text || item.content || "");
        break;
      case "commandExecution":
        appendLine(detail, "命令", Array.isArray(item.command) ? item.command.join(" ") : item.command || "", "mono");
        appendLine(detail, "目录", item.cwd || "", "mono");
        appendLine(detail, "PID", item.processId || "", "mono");
        appendLine(detail, "来源", item.source || "");
        appendLine(detail, "退出码", item.exitCode === undefined ? "" : item.exitCode, "mono");
        appendLine(detail, "耗时", item.durationMs === undefined ? "" : `${item.durationMs} ms`);
        if (item.aggregatedOutput) {
          const pre = document.createElement("pre");
          pre.className = "codex-item-output";
          pre.textContent = bounded(item.aggregatedOutput);
          detail.appendChild(pre);
        }
        break;
      case "fileChange":
        appendLine(detail, "文件变更", jsonText(item.changes || item, 20000), "mono");
        break;
      case "mcpToolCall":
        appendLine(detail, "MCP", `${item.server || ""}${item.tool ? "/" + item.tool : ""}`);
        appendLine(detail, "只读提示", item.readOnlyHint === undefined ? "" : String(item.readOnlyHint));
        appendLine(detail, "参数", jsonText(item.arguments || item.args || {}, 10000), "mono");
        appendLine(detail, "结果", jsonText(item.result || item.error || "", 20000), "mono");
        break;
      case "dynamicToolCall":
        appendLine(detail, "动态工具", item.tool || item.name || "");
        appendLine(detail, "参数", jsonText(item.arguments || item.args || {}, 10000), "mono");
        appendLine(detail, "结果", jsonText(item.result || item.error || "", 20000), "mono");
        break;
      case "collabAgentToolCall":
        appendLine(detail, "协作工具", item.tool || "");
        appendLine(detail, "发送 Thread", item.senderThreadId || "", "mono");
        appendLine(detail, "接收 Thread", Array.isArray(item.receiverThreadIds) ? item.receiverThreadIds.join(", ") : "", "mono");
        appendLine(detail, "提示", item.prompt || "");
        appendLine(detail, "子 Agent 状态", jsonText(item.agentsStates || {}, 12000), "mono");
        break;
      case "subAgentActivity":
        appendLine(detail, "活动", item.kind || "");
        appendLine(detail, "Agent Thread", item.agentThreadId || "", "mono");
        appendLine(detail, "Agent Path", item.agentPath || "", "mono");
        break;
      case "webSearch":
        appendLine(detail, "搜索", item.query || item.text || jsonText(item, 12000));
        break;
      case "imageView":
      case "imageGeneration":
        appendLine(detail, "图像", item.path || item.url || item.prompt || jsonText(item, 12000));
        break;
      case "hookPrompt":
        appendLine(detail, "Hook", item.prompt || item.text || jsonText(item, 12000));
        break;
      case "functionCallOutput":
        appendLine(detail, "函数输出", item.output || jsonText(item, 20000), "mono");
        break;
      case "contextCompaction":
        appendLine(detail, "上下文压缩", item.message || item.summary || "Codex 正在压缩 Thread 上下文");
        break;
      case "sleep":
        appendLine(detail, "等待", item.durationMs === undefined ? jsonText(item, 8000) : `${item.durationMs} ms`);
        break;
      case "enteredReviewMode":
      case "exitedReviewMode":
        appendLine(detail, "Review Mode", type === "enteredReviewMode" ? "进入" : "退出");
        break;
      default:
        appendLine(detail, "协议数据", jsonText(item, 22000), "mono");
    }
    return detail;
  };

  const cardKey = (threadId, turnId, itemId) => `${threadId || ""}\u0000${turnId || ""}\u0000${itemId || ""}`;

  const upsertItem = (record) => {
    const payload = record && record.payload && typeof record.payload === "object" ? record.payload : {};
    const threadId = String(record.thread_id || payload.threadId || "");
    const turnId = String(record.turn_id || payload.turnId || "");
    const itemId = String(record.item_id || payload.id || "");
    if (!itemId) return null;
    const key = cardKey(threadId, turnId, itemId);
    let card = cards.get(key);
    if (!card) {
      const empty = document.getElementById("codex-item-empty");
      if (empty) empty.remove();
      card = document.createElement("article");
      card.className = "repo codex-item-card";
      card.dataset.threadId = threadId;
      card.dataset.turnId = turnId;
      card.dataset.itemId = itemId;
      const head = document.createElement("div");
      head.className = "row between";
      const identity = document.createElement("div");
      const title = document.createElement("strong");
      title.className = "codex-item-title";
      const ids = document.createElement("div");
      ids.className = "mono fine";
      ids.textContent = `item ${itemId} · turn ${turnId || "-"}`;
      identity.append(title, ids);
      const status = document.createElement("span");
      status.className = "badge codex-item-status";
      head.append(identity, status);
      const body = document.createElement("div");
      body.className = "codex-item-body";
      const live = document.createElement("pre");
      live.className = "codex-item-live";
      live.hidden = true;
      card.append(head, body, live);
      cards.set(key, card);
      list.appendChild(card);
    }

    const type = String(record.item_type || payload.type || "unknown");
    const statusText = String(record.status || payload.status || "inProgress");
    card.querySelector(".codex-item-title").textContent = type;
    const statusNode = card.querySelector(".codex-item-status");
    statusNode.textContent = statusText;
    statusNode.className = `${badgeClass(statusText)} codex-item-status`;
    const body = card.querySelector(".codex-item-body");
    body.replaceChildren(describeItem(payload));

    if (Object.prototype.hasOwnProperty.call(record, "delta_text")) {
      const live = card.querySelector(".codex-item-live");
      const persisted = String(record.delta_text || "");
      live.textContent = bounded(persisted);
      live.hidden = !persisted;
    }

    counter.textContent = String(cards.size);
    return card;
  };

  const itemRecordFromNotification = (event) => {
    const params = event.params && typeof event.params === "object" ? event.params : {};
    const item = params.item && typeof params.item === "object" ? params.item : {};
    const completed = event.method === "item/completed";
    return {
      thread_id: params.threadId || "",
      turn_id: params.turnId || "",
      item_id: item.id || params.itemId || "",
      item_type: item.type || "unknown",
      status: item.status || (completed ? "completed" : "inProgress"),
      payload: item,
    };
  };

  const appendDelta = (event) => {
    const params = event.params && typeof event.params === "object" ? event.params : {};
    const itemId = String(params.itemId || "");
    if (!itemId) return;
    const key = cardKey(params.threadId || "", params.turnId || "", itemId);
    let card = cards.get(key);
    if (!card) {
      card = upsertItem({
        thread_id: params.threadId || "",
        turn_id: params.turnId || "",
        item_id: itemId,
        item_type: "stream",
        status: "inProgress",
        payload: { id: itemId, type: "stream" },
      });
    }
    if (!card) return;
    const live = card.querySelector(".codex-item-live");
    const delta = params.delta !== undefined
      ? params.delta
      : (params.textDelta !== undefined
          ? params.textDelta
          : (params.outputDelta !== undefined
              ? params.outputDelta
              : (params.contentDelta !== undefined ? params.contentDelta : params.summaryTextDelta)));
    if (delta === undefined || delta === null) return;
    live.hidden = false;
    live.textContent = bounded((live.textContent || "") + (typeof delta === "string" ? delta : jsonText(delta, 12000)));
  };

  const reconcileTurn = (event) => {
    const params = event.params && typeof event.params === "object" ? event.params : {};
    const turn = params.turn && typeof params.turn === "object" ? params.turn : {};
    const turnId = String(turn.id || params.turnId || "");
    if (!turnId) return;
    cards.forEach((card) => {
      if (card.dataset.turnId !== turnId) return;
      const statusNode = card.querySelector(".codex-item-status");
      if (statusNode && statusNode.textContent === "inProgress") {
        statusNode.textContent = "orphaned";
        statusNode.className = "badge warn codex-item-status";
      }
    });
  };

  const normalizeInteraction = (raw) => {
    const data = raw && typeof raw === "object" ? raw : {};
    return {
      id: String(data.id || data.interactionId || ""),
      method: String(data.method || ""),
      state: String(data.state || "pending"),
      threadId: String(data.thread_id || data.threadId || ""),
      turnId: String(data.turn_id || data.turnId || ""),
      itemId: String(data.item_id || data.itemId || ""),
      approvalId: String(data.approval_id || data.approvalId || ""),
      blocking: data.blocking !== false,
      request: data.request && typeof data.request === "object" ? data.request : {},
      responseSummary: (data.response_summary && typeof data.response_summary === "object")
        ? data.response_summary
        : ((data.responseSummary && typeof data.responseSummary === "object") ? data.responseSummary : {}),
      error: String(data.error || ""),
      createdAt: String(data.created_at || data.createdAt || ""),
      updatedAt: String(data.updated_at || data.updatedAt || ""),
    };
  };

  const hiddenInput = (name, value) => {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    return input;
  };

  const actionButton = (label, value, className) => {
    const button = document.createElement("button");
    button.type = "submit";
    button.name = "action";
    button.value = value;
    button.className = className;
    button.textContent = label;
    return button;
  };

  const responseForm = (interaction) => {
    const form = document.createElement("form");
    form.method = "post";
    form.action = `/account/agent/tasks/${encodeURIComponent(taskId)}/codex/interactions/${encodeURIComponent(interaction.id)}/respond`;
    form.className = "row";
    form.appendChild(hiddenInput("csrf_token", csrfToken));
    return form;
  };

  const describeInteraction = (interaction) => {
    const detail = document.createElement("div");
    detail.className = "stack";
    const request = interaction.request || {};

    if (interaction.method === "item/commandExecution/requestApproval") {
      appendLine(detail, "原因", request.reason || "");
      const pre = document.createElement("pre");
      pre.className = "codex-item-output";
      pre.textContent = bounded(request.command || "-", 30000);
      detail.appendChild(pre);
      appendLine(detail, "目录", request.cwd || "", "mono");
      appendLine(detail, "类型", request.kind || "");
    } else if (interaction.method === "item/fileChange/requestApproval") {
      appendLine(detail, "原因", request.reason || "");
      appendLine(detail, "授权根目录", request.grantRoot || "", "mono");
    } else if (interaction.method === "item/permissions/requestApproval") {
      appendLine(detail, "原因", request.reason || "");
      appendLine(detail, "目录", request.cwd || "", "mono");
      const pre = document.createElement("pre");
      pre.className = "codex-item-output";
      pre.textContent = jsonText(request.permissions || {}, 30000);
      detail.appendChild(pre);
    } else if (interaction.method !== "item/tool/requestUserInput") {
      appendLine(detail, "协议数据", jsonText(request, 30000), "mono");
    }

    if (interaction.state === "pending") {
      if (["item/commandExecution/requestApproval", "item/fileChange/requestApproval"].includes(interaction.method)) {
        const form = responseForm(interaction);
        form.append(
          actionButton("允许一次", "accept", "primary"),
          actionButton("本 Session 允许", "acceptForSession", "secondary"),
          actionButton("拒绝", "decline", "danger-button"),
          actionButton("取消", "cancel", "secondary"),
        );
        detail.appendChild(form);
      } else if (interaction.method === "item/permissions/requestApproval") {
        const form = responseForm(interaction);
        form.append(
          actionButton("仅本 Turn 授权", "grant_turn", "primary"),
          actionButton("本 Session 授权", "grant_session", "secondary"),
          actionButton("拒绝权限", "deny", "danger-button"),
        );
        detail.appendChild(form);
      } else if (interaction.method === "item/tool/requestUserInput") {
        const form = responseForm(interaction);
        form.className = "stack";
        const questions = Array.isArray(request.questions) ? request.questions : [];
        questions.forEach((questionRaw) => {
          const question = questionRaw && typeof questionRaw === "object" ? questionRaw : {};
          const questionId = String(question.id || "");
          if (!questionId) return;
          const fieldset = document.createElement("fieldset");
          fieldset.className = "repo stack";
          const legend = document.createElement("legend");
          const title = document.createElement("strong");
          title.textContent = bounded(question.header || "Codex 提问", 500);
          legend.appendChild(title);
          fieldset.appendChild(legend);
          const prompt = document.createElement("p");
          prompt.textContent = bounded(question.question || "", 12000);
          fieldset.appendChild(prompt);
          const options = Array.isArray(question.options) ? question.options : [];
          options.forEach((optionRaw) => {
            const option = optionRaw && typeof optionRaw === "object" ? optionRaw : {};
            const label = document.createElement("label");
            label.className = "check";
            const input = document.createElement("input");
            input.type = "checkbox";
            input.name = `q:${questionId}`;
            input.value = bounded(option.label || "", 12000);
            label.appendChild(input);
            const text = document.createElement("span");
            text.textContent = `${bounded(option.label || "", 1000)}${option.description ? ` · ${bounded(option.description, 3000)}` : ""}`;
            label.appendChild(text);
            fieldset.appendChild(label);
          });
          if (!options.length || question.isOther) {
            const input = document.createElement("input");
            input.type = question.isSecret ? "password" : "text";
            input.name = `q:${questionId}`;
            input.maxLength = 12000;
            input.autocomplete = "off";
            input.placeholder = question.isSecret ? "敏感回答不会显示或写入历史" : "输入回答";
            fieldset.appendChild(input);
          }
          if (question.isSecret) {
            const note = document.createElement("div");
            note.className = "fine";
            note.textContent = "此问题由 Codex 标记为 secret；FDEX 不会把回答正文写入事件或审计历史。";
            fieldset.appendChild(note);
          }
          form.appendChild(fieldset);
        });
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.className = "primary";
        submit.textContent = "提交回答";
        form.appendChild(submit);
        detail.appendChild(form);
      }
    }

    if (interaction.responseSummary && Object.keys(interaction.responseSummary).length) {
      appendLine(detail, "响应摘要", jsonText(interaction.responseSummary, 8000), "mono");
    }
    if (interaction.error) {
      const error = document.createElement("div");
      error.className = "alert alert-error";
      error.textContent = bounded(interaction.error, 5000);
      detail.appendChild(error);
    }
    return detail;
  };

  const refreshInteractionCounters = () => {
    if (interactionCounter) interactionCounter.textContent = String(interactionRecords.size);
    if (interactionPendingCounter) {
      let pending = 0;
      interactionRecords.forEach((record) => {
        if (record.state === "pending") pending += 1;
      });
      interactionPendingCounter.textContent = String(pending);
    }
  };

  const hasPendingInteraction = () => {
    let pending = false;
    interactionRecords.forEach((record) => {
      if (record.state === "pending") pending = true;
    });
    return pending;
  };

  const upsertInteraction = (raw) => {
    if (!interactionList) return null;
    const interaction = normalizeInteraction(raw);
    if (!interaction.id) return null;
    interactionRecords.set(interaction.id, interaction);
    const empty = document.getElementById("codex-interaction-empty");
    if (empty) empty.remove();
    let card = interactionCards.get(interaction.id);
    if (!card) {
      card = document.createElement("article");
      card.className = "repo codex-interaction-card";
      card.dataset.interactionId = interaction.id;
      interactionCards.set(interaction.id, card);
      interactionList.prepend(card);
    }
    const head = document.createElement("div");
    head.className = "row between";
    const identity = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = interaction.method || "Codex interaction";
    const ids = document.createElement("div");
    ids.className = "mono fine";
    ids.textContent = `item ${interaction.itemId || "-"}${interaction.approvalId ? ` · approval ${interaction.approvalId}` : ""}`;
    identity.append(title, ids);
    const status = document.createElement("span");
    status.className = badgeClass(interaction.state);
    status.textContent = interaction.state;
    head.append(identity, status);
    card.replaceChildren(head, describeInteraction(interaction));
    refreshInteractionCounters();
    return card;
  };

  const renderInteractionSnapshot = (records) => {
    if (!interactionList) return;
    interactionList.replaceChildren();
    interactionCards.clear();
    interactionRecords.clear();
    const safeRecords = Array.isArray(records) ? records : [];
    // Store rows arrive newest first. Prepending each oldest-to-newest keeps newest at the top.
    safeRecords.slice().reverse().forEach(upsertInteraction);
    if (!safeRecords.length) {
      const empty = document.createElement("p");
      empty.id = "codex-interaction-empty";
      empty.className = "muted";
      empty.textContent = "当前没有 Codex 审批或提问。";
      interactionList.appendChild(empty);
    }
    refreshInteractionCounters();
  };

  const scheduleTerminalReload = () => {
    if (terminalReloadTimer) window.clearTimeout(terminalReloadTimer);
    terminalReloadTimer = window.setTimeout(() => {
      if (!hasPendingInteraction()) window.location.reload();
    }, 3000);
  };

  const applyEvent = (event) => {
    if (!event || typeof event !== "object") return;
    eventCount += 1;
    eventCounter.textContent = String(eventCount);
    const method = String(event.method || "");
    if (method === "item/started" || method === "item/completed") {
      upsertItem(itemRecordFromNotification(event));
      return;
    }
    if (method.startsWith("fdex/interaction/")) {
      upsertInteraction(event.params || {});
      return;
    }
    if (method.toLowerCase().includes("/delta") || method.endsWith("Delta")) {
      appendDelta(event);
      return;
    }
    if (method === "turn/completed") {
      reconcileTurn(event);
      scheduleTerminalReload();
    }
  };

  const connect = () => {
    if (source) source.close();
    state.textContent = "连接中";
    state.className = "badge";
    source = new EventSource(`/account/agent/tasks/${encodeURIComponent(taskId)}/codex/events?after=${lastSeq}`);
    source.addEventListener("open", () => {
      state.textContent = "实时已连接";
      state.className = "badge ok";
    });
    source.addEventListener("codex", (message) => {
      try {
        const event = JSON.parse(message.data);
        lastSeq = Math.max(lastSeq, Number(event.seq || message.lastEventId || 0));
        applyEvent(event);
      } catch (_error) {
        state.textContent = "事件解析异常";
        state.className = "badge warn";
      }
    });
    source.addEventListener("reconnect", () => {
      source.close();
      window.setTimeout(connect, 300);
    });
    source.addEventListener("error", () => {
      state.textContent = "重连中";
      state.className = "badge warn";
    });
  };

  fetch(`/account/agent/tasks/${encodeURIComponent(taskId)}/codex/snapshot`, {
    credentials: "same-origin",
    cache: "no-store",
  })
    .then((response) => {
      if (!response.ok) throw new Error(`snapshot ${response.status}`);
      return response.json();
    })
    .then((snapshot) => {
      lastSeq = Number(snapshot.latest_seq || 0);
      const items = Array.isArray(snapshot.items) ? snapshot.items : [];
      list.replaceChildren();
      cards.clear();
      items.forEach(upsertItem);
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.id = "codex-item-empty";
        empty.textContent = "等待 Codex Item 事件……";
        list.appendChild(empty);
      }
      renderInteractionSnapshot(snapshot.interactions || []);
      connect();
    })
    .catch((error) => {
      state.textContent = "实时流不可用";
      state.className = "badge warn";
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = bounded(error.message || error, 1000);
      list.appendChild(note);
    });

  window.addEventListener("beforeunload", () => {
    if (source) source.close();
    if (terminalReloadTimer) window.clearTimeout(terminalReloadTimer);
  });
})();
