(() => {
  "use strict";

  const panel = document.getElementById("codex-item-panel");
  if (!panel) return;

  const taskId = panel.dataset.taskId || "";
  const list = document.getElementById("codex-item-list");
  const state = document.getElementById("codex-stream-state");
  const counter = document.getElementById("codex-item-count");
  const eventCounter = document.getElementById("codex-event-count");
  if (!taskId || !list || !state || !counter || !eventCounter) return;

  const cards = new Map();
  let lastSeq = 0;
  let eventCount = 0;
  let source = null;

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
    if (["completed", "succeeded", "success"].includes(status)) return "badge ok";
    if (["failed", "error", "declined", "rejected", "orphaned", "interrupted"].includes(status)) return "badge warn";
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
    const delta = params.delta !== undefined ? params.delta : (params.textDelta !== undefined ? params.textDelta : params.outputDelta);
    if (delta === undefined || delta === null) return;
    live.hidden = false;
    live.textContent = bounded((live.textContent || "") + String(delta));
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

  const applyEvent = (event) => {
    if (!event || typeof event !== "object") return;
    eventCount += 1;
    eventCounter.textContent = String(eventCount);
    if (event.method === "item/started" || event.method === "item/completed") {
      upsertItem(itemRecordFromNotification(event));
      return;
    }
    if (String(event.method || "").includes("/delta") || String(event.method || "").endsWith("Delta")) {
      appendDelta(event);
      return;
    }
    if (event.method === "turn/completed") reconcileTurn(event);
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
  });
})();
