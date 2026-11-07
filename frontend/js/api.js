const API_BASE = 'https://acr-backend-wv98.onrender.com/api';

// Recupera una sessione valida. Se scade a breve, la rinnova.
async function _getValidSession() {
  try {
    const sb = window.supabaseClient || window.supabase;
    if (!sb) return null;
    const { data: { session }, error } = await sb.auth.getSession();
    if (error || !session) return null;

    // Se il token scade entro 60 secondi, rinnovalo
    const expiresAt = (session.expires_at || 0) * 1000;
    if (expiresAt && Date.now() > expiresAt - 60_000) {
      const { data: refreshed } = await sb.auth.refreshSession();
      return refreshed?.session || null;
    }
    return session;
  } catch { return null; }
}

async function apiCall(path, options = {}) {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  const url = `${API_BASE}${cleanPath}`;
  const headers = { Accept: 'application/json', ...(options.headers || {}) };
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';

  const session = await _getValidSession();
  if (session?.access_token) {
    headers.Authorization = `Bearer ${session.access_token}`;
  }

  const baseOpts = { ...options, headers, credentials: 'omit', cache: 'no-store' };
  let res = await fetch(url, baseOpts);

  // Se il token è scaduto (401), lo rinnoviamo e riproviamo una volta
  if (res.status === 401) {
      const sb = window.supabaseClient || window.supabase;
      const { data } = await sb.auth.refreshSession();
      if (data?.session?.access_token) {
          headers.Authorization = `Bearer ${data.session.access_token}`;
          res = await fetch(url, { ...baseOpts, headers });
      }
  }

  if (!res.ok) {
    let errorMsg = `HTTP ${res.status}`;
    try {
        const data = await res.clone().json();
        if (data.error) errorMsg = data.error;
    } catch {
        // Se non è JSON (es. errore 500 HTML di Render), mostriamo l'errore generico
        if (res.status === 500) errorMsg = "Errore interno del server (500)";
    }
    throw new Error(errorMsg);
  }
  return res;
}

async function apiJson(path, options = {}) {
  const res = await apiCall(path, options);
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

window.apiCall = apiCall;
window.apiJson = apiJson;

// OGGETTO API COMPLETO (per far funzionare la Dashboard e il Dettaglio)
window.api = {
    // Campionati
    getChampionships: () => apiJson('/championships'),
    saveChampionship: (data) => apiJson('/create-championship', { method: 'POST', body: JSON.stringify(data) }),
    deleteChampionship: (id) => apiJson(`/delete-championship/${id}`, { method: 'DELETE' }),
    
    // Albero (Rally/Leg/Stage)
    getTree: (champId) => apiJson(`/tree/${champId}`),
    
    // Rally
    createRally: (data) => apiJson('/create-rally', { method: 'POST', body: JSON.stringify(data) }),
    deleteRally: (id) => apiJson(`/delete-rally/${id}`, { method: 'DELETE' }),
    
    // Leg
    createLeg: (data) => apiJson('/create-leg', { method: 'POST', body: JSON.stringify(data) }),
    deleteLeg: (id) => apiJson(`/delete-leg/${id}`, { method: 'DELETE' }),
    
    // Stage
    createStage: (data) => apiJson('/create-stage', { method: 'POST', body: JSON.stringify(data) }),
    updateStage: (id, data) => apiJson(`/update-stage/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteStage: (id) => apiJson(`/delete-stage/${id}`, { method: 'DELETE' }),
    
    // Risultati
    saveStage: (data) => apiJson('/save-stage', { method: 'POST', body: JSON.stringify(data) }),
    publishStage: (id) => apiJson(`/publish-stage/${id}`, { method: 'POST' }),
    
    // Dropdown
    getTracks: () => apiJson('/tracks')
};