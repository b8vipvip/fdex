(() => {
  'use strict';

  const body = document.body;
  const scope = String(body?.dataset?.fdexLogScope || '').trim();
  const appVersion = String(body?.dataset?.fdexAppVersion || '').trim();
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
  if (!scope || !csrf || typeof window.fetch !== 'function') return;

  const ENDPOINT = '/api/client-logs/web-batch';
  const STORAGE_KEY = `fdex:web-runtime-log:v1:${scope}`;
  const MAX_ENTRIES = 400;
  const MAX_STORAGE_CHARS = 512 * 1024;
  const BATCH_SIZE = 50;
  const FLUSH_INTERVAL_MS = 30_000;
  const originalFetch = window.fetch.bind(window);
  let queue = [];
  let flushPromise = null;

  const secretPatterns = [
    /(authorization\s*[:=]\s*bearer\s+)[^\s,;]+/gi,
    /(bearer\s+)[A-Za-z0-9._~+/=-]{16,}/gi,
    /((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie)\s*[:=]\s*)[^\s,;]+/gi,
  ];

  function sanitizeText(value, limit = 2000) {
    let text = String(value ?? '').replaceAll('\u0000', ' ').trim();
    for (const pattern of secretPatterns) text = text.replace(pattern, '$1[REDACTED]');
    return text.slice(0, Math.max(0, limit));
  }

  function sensitiveKey(key) {
    const lower = String(key || '').toLowerCase();
    return ['password', 'passwd', 'token', 'secret', 'authorization', 'cookie', 'api_key', 'apikey']
      .some((needle) => lower.includes(needle));
  }

  function cleanDetails(value, depth = 0) {
    if (depth > 4) return '[TRUNCATED]';
    if (value === null || value === undefined) return null;
    if (typeof value === 'boolean' || typeof value === 'number') return value;
    if (typeof value === 'string') return sanitizeText(value, 1200);
    if (Array.isArray(value)) return value.slice(0, 30).map((item) => cleanDetails(item, depth + 1));
    if (typeof value === 'object') {
      const result = {};
      for (const [rawKey, rawValue] of Object.entries(value).slice(0, 40)) {
        const key = sanitizeText(rawKey, 100);
        result[key] = sensitiveKey(key) ? '[REDACTED]' : cleanDetails(rawValue, depth + 1);
      }
      return result;
    }
    return sanitizeText(value, 1200);
  }

  function loadQueue() {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      queue = Array.isArray(stored) ? stored.slice(-MAX_ENTRIES) : [];
    } catch (_) {
      queue = [];
    }
  }

  function persistQueue() {
    queue = queue.slice(-MAX_ENTRIES);
    try {
      let serialized = JSON.stringify(queue);
      while (serialized.length > MAX_STORAGE_CHARS && queue.length > 20) {
        queue.splice(0, Math.min(20, queue.length - 20));
        serialized = JSON.stringify(queue);
      }
      localStorage.setItem(STORAGE_KEY, serialized);
    } catch (_) {
      // Keep the in-memory queue when storage is blocked/full. Diagnostics must never break the app.
    }
  }

  function append(level, component, event, message = '', details = {}) {
    queue.push({
      time: new Date().toISOString(),
      level: ['debug', 'info', 'warn', 'error'].includes(level) ? level : 'info',
      component: sanitizeText(component, 120) || 'web',
      event: sanitizeText(event, 160) || 'event',
      message: sanitizeText(message, 2000),
      details: cleanDetails(details),
    });
    persistQueue();
  }

  function safePath(value) {
    try {
      const url = new URL(value, window.location.href);
      return url.origin === window.location.origin ? url.pathname : `${url.origin}${url.pathname}`;
    } catch (_) {
      return sanitizeText(value, 300);
    }
  }

  function browserLabel() {
    const browserPlatform = navigator.userAgentData?.platform || navigator.platform || 'Browser';
    return sanitizeText(`Web ${browserPlatform}`, 160);
  }

  async function flush(keepalive = false) {
    if (flushPromise || queue.length === 0 || !navigator.onLine) return flushPromise || 0;
    const batch = queue.slice(0, BATCH_SIZE);
    const payload = {
      device_name: browserLabel(),
      platform: 'web',
      app_version: appVersion,
      git_sha: '',
      os_version: sanitizeText(navigator.userAgent || navigator.platform || 'Web Browser', 120),
      entries: batch,
    };

    flushPromise = originalFetch(ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      keepalive,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-FDEX-CSRF-Token': csrf,
        'X-FDEX-Web-Client-Logs': '1',
      },
      body: JSON.stringify(payload),
    })
      .then((response) => {
        if (!response.ok) return 0;
        queue.splice(0, batch.length);
        persistQueue();
        return batch.length;
      })
      .catch(() => 0)
      .finally(() => {
        flushPromise = null;
      });
    return flushPromise;
  }

  loadQueue();

  window.FdexRuntimeLog = Object.freeze({
    debug(component, event, message = '', details = {}) { append('debug', component, event, message, details); },
    info(component, event, message = '', details = {}) { append('info', component, event, message, details); },
    warn(component, event, message = '', details = {}) { append('warn', component, event, message, details); },
    error(component, event, message = '', details = {}) { append('error', component, event, message, details); },
    flush,
  });

  window.fetch = async function fdexLoggedFetch(input, init = {}) {
    const started = performance.now();
    let url = '';
    let method = 'GET';
    try {
      if (input instanceof Request) {
        url = input.url;
        method = String(init.method || input.method || 'GET').toUpperCase();
      } else {
        url = String(input || '');
        method = String(init.method || 'GET').toUpperCase();
      }
      const parsed = new URL(url, window.location.href);
      const sameOrigin = parsed.origin === window.location.origin;
      const shouldLog = sameOrigin && parsed.pathname !== ENDPOINT;
      try {
        const response = await originalFetch(input, init);
        if (shouldLog) {
          const elapsedMs = Math.round(performance.now() - started);
          const level = response.status >= 500 ? 'error' : response.status >= 400 || elapsedMs >= 5000 ? 'warn' : 'info';
          append(level, 'web_network', 'fetch_complete', `${method} ${parsed.pathname} -> HTTP ${response.status}`, {
            method,
            path: parsed.pathname,
            status: response.status,
            ok: response.ok,
            redirected: response.redirected,
            elapsed_ms: elapsedMs,
          });
        }
        return response;
      } catch (error) {
        if (shouldLog) {
          append('error', 'web_network', 'fetch_exception', `${method} ${parsed.pathname} failed`, {
            method,
            path: parsed.pathname,
            elapsed_ms: Math.round(performance.now() - started),
            error_type: error?.name || 'Error',
            error: error?.message || String(error),
          });
        }
        throw error;
      }
    } catch (error) {
      append('error', 'web_network', 'fetch_wrapper_exception', 'Web fetch diagnostics wrapper failed', {
        method,
        path: safePath(url),
        error_type: error?.name || 'Error',
        error: error?.message || String(error),
      });
      return originalFetch(input, init);
    }
  };

  window.addEventListener('error', (event) => {
    const target = event.target;
    if (target && target !== window && target instanceof Element) {
      const source = target.getAttribute('src') || target.getAttribute('href') || '';
      append('error', 'web_runtime', 'resource_load_error', 'Web resource failed to load', {
        tag: target.tagName,
        path: safePath(source),
      });
      return;
    }
    append('error', 'web_runtime', 'javascript_error', event.message || 'Unhandled JavaScript error', {
      file: safePath(event.filename || ''),
      line: event.lineno || 0,
      column: event.colno || 0,
      error_type: event.error?.name || 'Error',
      stack: event.error?.stack || '',
      path: window.location.pathname,
    });
  }, true);

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    append('error', 'web_runtime', 'unhandled_rejection', reason?.message || String(reason || 'Unhandled promise rejection'), {
      error_type: reason?.name || typeof reason,
      stack: reason?.stack || '',
      path: window.location.pathname,
    });
  });

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const action = new URL(form.action || window.location.href, window.location.href);
    append('info', 'web_ui', 'form_submit', `${String(form.method || 'GET').toUpperCase()} ${action.pathname}`, {
      method: String(form.method || 'GET').toUpperCase(),
      path: action.pathname,
      enctype: form.enctype || '',
      has_file_input: Boolean(form.querySelector('input[type="file"]')),
    });
  }, true);

  window.addEventListener('online', () => {
    append('info', 'web_network', 'online', 'Browser network is online', { path: window.location.pathname });
    void flush();
  });
  window.addEventListener('offline', () => {
    append('warn', 'web_network', 'offline', 'Browser network is offline', { path: window.location.pathname });
  });
  document.addEventListener('visibilitychange', () => {
    append('debug', 'web_app', 'visibility_change', document.visibilityState, { path: window.location.pathname });
    if (document.visibilityState === 'visible') void flush();
  });
  window.addEventListener('pagehide', () => {
    persistQueue();
    void flush(true);
  });

  append('info', 'web_app', 'page_load', 'FDEX Web page loaded', {
    path: window.location.pathname,
    online: navigator.onLine,
    visibility: document.visibilityState,
    language: navigator.language || '',
  });

  window.addEventListener('load', () => {
    window.setTimeout(() => {
      const navigation = performance.getEntriesByType?.('navigation')?.[0];
      if (!navigation) return;
      append('debug', 'web_performance', 'navigation_timing', 'Web navigation timing', {
        path: window.location.pathname,
        dom_content_loaded_ms: Math.round(navigation.domContentLoadedEventEnd || 0),
        load_event_ms: Math.round(navigation.loadEventEnd || 0),
        response_ms: Math.round(navigation.responseEnd || 0),
        transfer_size: Number(navigation.transferSize || 0),
        type: navigation.type || '',
      });
    }, 0);
  });

  window.setTimeout(() => { void flush(); }, 1000);
  window.setInterval(() => { void flush(); }, FLUSH_INTERVAL_MS);
})();
