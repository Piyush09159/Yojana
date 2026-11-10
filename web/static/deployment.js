/* Persistence enhancement shared by FastAPI-served and standalone dashboards.
 * Stores only an opaque case ID. Owner credentials stay in HttpOnly cookies.
 */
(() => {
  const originalFetch = window.fetch.bind(window);
  const pending = new Map();
  window.fetch = async (url, options = {}) => {
    const path = String(url);
    let key;
    if (path.startsWith('/api/rescue/') && options.body && sid) {
      const check = await originalFetch('/api/cases/' + encodeURIComponent(sid) + '?optional=true');
      const saved = check.ok ? await check.json() : {};
      if (saved.available) options = {...options, body: JSON.stringify({...JSON.parse(options.body), case_id: sid})};
    }
    if (path === '/api/v2/conversation' && options.body) {
      const body = JSON.parse(options.body);
      key = JSON.stringify([body.session_id, body.text]);
      if (!pending.has(key)) pending.set(key, crypto.randomUUID());
      body.request_id = pending.get(key);
      options = {...options, body: JSON.stringify(body)};
    }
    const response = await originalFetch(url, options);
    if (path === '/api/v2/conversation') {
      const data = await response.clone().json().catch(() => ({}));
      if (response.ok) {
        pending.delete(key);
        try { sessionStorage.setItem('yojana-case-id', data.session_id); } catch { /* Optional. */ }
      } else if (data.detail?.code) {
        // Reuse the existing dashboard error/retry UI, with a meaningful state.
        return new Response(JSON.stringify({detail: data.detail.code.replaceAll('_', ' ')}),
                            {status: response.status, headers: {'Content-Type': 'application/json'}});
      }
    }
    if (path.startsWith('/api/v2/conversation/') && options.method === 'DELETE' && response.ok) {
      pending.clear();
      try { sessionStorage.removeItem('yojana-case-id'); } catch { /* Optional. */ }
    }
    return response;
  };
  const hint = document.querySelector('.privacy-hint');
  if (hint) hint.textContent = 'Sending shares your message with this server and its configured language service. A configured server saves a minimized conversation for its retention period (30 days by default). New chat deletes your saved conversation. Provider and messaging-service logs have separate policies. Do not send identity or account numbers.';
  const status = document.querySelector('#case-status');
  if (status) status.textContent = 'No investigation started. Saved cases follow the server retention policy.';
  for (const notice of document.querySelectorAll('#page-channels .notice')) {
    if (notice.textContent.includes('An administrator')) notice.textContent = 'An administrator configures and pairs the separate WAHA service. Its API and dashboard require authentication. Signed message webhooks go directly to the backend /api/webhooks/waha endpoint. Send YOJANA to opt in and STOP to leave. Configuration does not confirm connectivity. Keep all credentials on the server.';
  }
  async function resume() {
    let saved;
    try { saved = sessionStorage.getItem('yojana-case-id'); } catch { return; }
    if (!saved) return;
    try {
      const response = await originalFetch('/api/cases/' + encodeURIComponent(saved) + '?optional=true');
      if (!response.ok) throw new Error('Case unavailable');
      const data = await response.json();
      if (!data.available) throw new Error('Case expired or unavailable');
      sid = data.id;
      facts(data.profile);
      document.querySelector('#chat').replaceChildren();
      for (const message of data.messages) bubble(message.content, message.role === 'user' ? 'user' : 'bot');
      document.querySelector('#stat-stage').textContent = data.status;
      activity('Saved conversation restored. Continue chatting or use New chat to delete it.');
    } catch {
      try { sessionStorage.removeItem('yojana-case-id'); } catch { /* Optional. */ }
      activity('Saved conversation is unavailable or expired. Start a new conversation.');
    }
  }
  window.addEventListener('load',()=>window.YojanaCase?window.YojanaCase.refresh():resume(),{once:true});
})();
