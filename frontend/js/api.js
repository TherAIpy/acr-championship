// js/api.js — versione corretta (C0)
const API_BASE = 'https://acr-backend-wv98.onrender.com/api';

// evita refresh concorrenti
let _refreshPromise = null;

async function _getValidSession() {
  try {
    const sb = window.supabaseClient;
    if (!sb) return null;

    const { data: { session }, error } = await sb.auth.getSession();
    if (error || !session) return null;

    // refresh proattivo se scade entro 60s
    const expiresAt = (session.expires_at || 0) * 1000;
    if (expiresAt && Date.now() > expiresAt - 60_000) {
      if (!_refreshPromise) {
        _refreshPromise = sb.auth.refreshSession().finally(() => { _refreshPromise = null; });
      }
      const { data: refreshed } = await _refreshPromise;
      return refreshed?.session || null;
    }
    return session;
  } catch {
    return null;
  }
}

async function apiCall(path, options = {}) {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  const url = `${API_BASE}${cleanPath}`;
  const headers = {
    Accept: 'application/json',
    ...(options.headers || {}),
  };
  // content-type solo se c'è body e non è già impostato
  if (options.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  // Auth SOLO se esiste davvero un access_token valido
  const session = await _getValidSession();
  const token = session?.access_token;
  if (token && token !== 'null' && token !== 'undefined') {
    headers.Authorization = `Bearer ${token}`;
  }

  const baseOpts = {
    ...options,
    headers,
    credentials: 'omit', // niente cookie cross-origin
    cache: 'no-store',
  };

  let res = await fetch(url, baseOpts);

  // Un solo retry dopo refresh se il token era presente ma è stato rifiutato
  if (res.status === 401 && token) {
    try {
      const sb = window.supabaseClient;
      if (sb) {
        const { data } = await sb.auth.refreshSession();
        const newToken = data?.session?.access_token;
        if (newToken) {
          res = await fetch(url, {
            ...baseOpts,
            headers: { ...headers, Authorization: `Bearer ${newToken}` },
          });
        }
      }
    } catch { /* fallback anonimo: lascia il 401 originale */ }
  }

  return res;
}

async function apiJson(path, options = {}) {
  const res = await apiCall(path, options);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const err = new Error((data && data.error) || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

window.apiCall = apiCall;
window.apiJson = apiJson;
// Alias retro-compatibile per le vecchie chiamate (dashboard e detail)
window.api = {
  getChampionships: () => apiJson('/championships'),
  saveChampionship: (data) => apiJson('/create-championship', { method: 'POST', body: JSON.stringify(data) }),
  createRally: (data) => apiJson('/create-rally', { method: 'POST', body: JSON.stringify(data) }),
  createLeg: (data) => apiJson('/create-leg', { method: 'POST', body: JSON.stringify(data) }),
  createStage: (data) => apiJson('/create-stage', { method: 'POST', body: JSON.stringify(data) }),
  saveStage: (data) => apiJson('/save-stage', { method: 'POST', body: JSON.stringify(data) }),
  publishStage: (id) => apiJson(`/publish-stage/${id}`, { method: 'POST' }),
  getTree: (id) => apiJson(`/tree/${id}`)
};