(() => {
  const root = document.getElementById('codex-health-monitor');
  if (!root) return;

  const csrf = root.dataset.csrf || '';
  const endpoint = '/admin/agent/health.json';
  const checkEndpoint = '/admin/agent/health/check';
  const pollMs = 5000;
  let inFlight = false;

  const byId = (id) => document.getElementById(id);
  const text = (id, value) => {
    const node = byId(id);
    if (node) node.textContent = value == null || value === '' ? '—' : String(value);
  };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const fmtTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  };
  const fmtHours = (value) => value == null ? '—' : `${Number(value).toFixed(1)} h`;

  function badge(state) {
    const value = String(state || 'UNKNOWN').toUpperCase();
    if (value === 'READY' || value === 'OK') return ['success', value === 'OK' ? '正常' : '健康'];
    if (value === 'DEGRADED') return ['warning', '降级'];
    if (value === 'BLOCKED' || value === 'FAILED') return ['error', '阻断'];
    if (value === 'DISABLED') return ['', '已关闭'];
    return ['warning', value === 'UNKNOWN' ? '检测中' : value];
  }

  function renderOverall(health) {
    const [klass, label] = badge(health.state);
    const status = byId('codex-health-overall-badge');
    if (status) {
      status.className = `badge ${klass}`.trim();
      status.textContent = label;
    }
    text('codex-health-code', health.code);
    text('codex-health-reason', health.reason);
    text('codex-health-checked-at', fmtTime(health.checked_at));
    text('codex-health-duration', health.duration_ms == null ? '—' : `${health.duration_ms} ms`);
  }

  function renderRuntime(health) {
    const runtime = health.runtime || {};
    const host = health.host || {};
    const isolation = health.isolation || {};
    const selected = health.selected_provider || {};
    text('codex-health-runtime', runtime.state === 'ok' ? `${runtime.version || 'unknown'} · ${runtime.source || 'unknown'}` : `失败 · ${runtime.reason || runtime.code || 'unknown'}`);
    text('codex-health-runtime-latency', runtime.latency_ms == null ? '—' : `${runtime.latency_ms} ms`);
    text('codex-health-host', host.state === 'ok' ? `握手正常 · ${host.latency_ms || 0} ms` : `${host.state || 'unknown'} · ${host.reason || host.code || 'unknown'}`);
    text('codex-health-host-checked-at', fmtTime(host.checked_at));
    text('codex-health-isolation', isolation.enforced ? `enforced · ${isolation.parent_unit || ''}` : `未生效 · ${isolation.reason || isolation.code || ''}`);
    text('codex-health-resource', isolation.enforced ? `Memory ${isolation.memory_mb || '—'} MB · CPU ${isolation.cpu_percent || '—'}% · PID ${isolation.pids_max || '—'}` : '—');
    text('codex-health-selected-provider', selected.provider_id ? `${selected.provider_name || selected.provider_id} / ${selected.model || '—'}` : '无可用 fresh-full Provider');
  }

  function renderProviders(health) {
    const live = Array.isArray(health.providers) ? health.providers : [];
    const compat = Array.isArray(health.compatibility) ? health.compatibility : [];
    const liveMap = new Map(live.map((row) => [Number(row.provider_id || 0), row]));
    const tbody = byId('codex-health-provider-body');
    if (!tbody) return;
    if (!compat.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">没有已启用的 Provider，或监控尚未完成第一次检测。</td></tr>';
      return;
    }
    tbody.innerHTML = compat.map((row) => {
      const l = liveMap.get(Number(row.provider_id || 0)) || {};
      const liveState = String(l.state || 'unknown');
      const liveBadge = liveState === 'ok' || liveState === 'reachable' ? 'success' : liveState === 'rate_limited' || liveState === 'upstream_error' || liveState === 'unreachable' ? 'warning' : 'error';
      const compatBadge = row.eligible ? 'success' : row.code === 'SMOKE_EXPIRING' ? 'warning' : 'error';
      const selected = row.selected ? '<span class="badge success">当前选择</span>' : '';
      const http = l.status_code == null ? '—' : `HTTP ${l.status_code}`;
      const failures = Number(l.consecutive_failures || 0);
      return `<tr>
        <td><strong>${esc(row.provider_name || row.provider_id)}</strong><div class="tiny-line">${esc(row.model || '—')} ${selected}</div></td>
        <td><span class="badge ${liveBadge}">${esc(liveState)}</span><div class="tiny-line">${esc(http)} · ${esc(l.latency_ms == null ? '—' : `${l.latency_ms} ms`)}</div></td>
        <td>${failures}</td>
        <td><span class="badge ${compatBadge}">${esc(row.level || 'none')}</span></td>
        <td>${esc(row.code || '—')}</td>
        <td>${esc(fmtHours(row.age_hours))}</td>
        <td>${esc(fmtHours(row.remaining_hours))}</td>
        <td class="details-cell">${esc(row.reason || l.error || '—')}</td>
      </tr>`;
    }).join('');
  }

  function renderHistory(health) {
    const history = Array.isArray(health.history) ? health.history : [];
    const body = byId('codex-health-history');
    if (!body) return;
    if (!history.length) {
      body.innerHTML = '<div class="muted">暂无历史记录。</div>';
      return;
    }
    body.innerHTML = history.slice(0, 20).map((row) => {
      const [klass, label] = badge(row.state);
      return `<div style="display:grid;grid-template-columns:150px 80px minmax(160px,220px) 1fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);align-items:start">
        <span class="muted">${esc(fmtTime(row.checked_at))}</span>
        <span><span class="badge ${klass}">${esc(label)}</span></span>
        <code>${esc(row.code || '—')}</code>
        <span>${esc(row.reason || '—')}</span>
      </div>`;
    }).join('');
  }

  function render(health) {
    if (!health || typeof health !== 'object') return;
    renderOverall(health);
    renderRuntime(health);
    renderProviders(health);
    renderHistory(health);
  }

  async function refresh() {
    if (inFlight) return;
    inFlight = true;
    try {
      const response = await fetch(endpoint, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data && data.health) render(data.health);
      text('codex-health-ui-status', '自动回显正常 · 每 5 秒刷新');
    } catch (error) {
      text('codex-health-ui-status', `状态拉取失败：${error.message || error}`);
    } finally {
      inFlight = false;
    }
  }

  async function manualCheck() {
    const button = byId('codex-health-check-button');
    if (button) {
      button.disabled = true;
      button.textContent = '链路检测中…';
    }
    text('codex-health-ui-status', '正在执行 Runtime / Host / Provider 全链路检测…');
    try {
      const form = new FormData();
      form.append('csrf_token', csrf);
      const response = await fetch(checkEndpoint, {
        method: 'POST',
        body: form,
        credentials: 'same-origin',
        cache: 'no-store',
      });
      const data = await response.json();
      if (data && data.health) render(data.health);
      if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
      text('codex-health-ui-status', '立即检测完成；后台自动监测继续运行');
    } catch (error) {
      text('codex-health-ui-status', `立即检测失败：${error.message || error}`);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = '立即检测链路';
      }
    }
  }

  const checkButton = byId('codex-health-check-button');
  if (checkButton) checkButton.addEventListener('click', manualCheck);
  refresh();
  window.setInterval(refresh, pollMs);
})();
