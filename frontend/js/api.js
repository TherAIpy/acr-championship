const API_BASE = 'https://acr-backend-wv98.onrender.com/api';

async function _getValidSession() {
  try {
    const sb = window.supabaseClient || window.supabase;
    if (!sb) return null;
    const { data: { session }, error } = await sb.auth.getSession();
    if (error || !session) return null;
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

  if (!res.ok) {
    let errorMsg = `HTTP ${res.status}`;
    try {
        const data = await res.clone().json();
        if (data.error) errorMsg = data.error;
    } catch {}
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

window.api = {
    getChampionships: () => apiJson('/championships'),
    saveChampionship: (data) => apiJson('/create-championship', { method: 'POST', body: JSON.stringify(data) }),
    deleteChampionship: (id) => apiJson(`/delete-championship/${id}`, { method: 'DELETE' }),
    
    getTree: (champId) => apiJson(`/tree/${champId}`),
    
    createRally: (data) => apiJson('/create-rally', { method: 'POST', body: JSON.stringify(data) }),
    deleteRally: (id) => apiJson(`/delete-rally/${id}`, { method: 'DELETE' }),
    
    createLeg: (data) => apiJson('/create-leg', { method: 'POST', body: JSON.stringify(data) }),
    deleteLeg: (id) => apiJson(`/delete-leg/${id}`, { method: 'DELETE' }),
    
    createStage: (data) => apiJson('/create-stage', { method: 'POST', body: JSON.stringify(data) }),
    deleteStage: (id) => apiJson(`/delete-stage/${id}`, { method: 'DELETE' }),
    saveStage: (data) => apiJson('/save-stage', { method: 'POST', body: JSON.stringify(data) }),
    publishStage: (id) => apiJson(`/publish-stage/${id}`, { method: 'POST' }),
    
    getTracks: () => apiJson('/tracks'),
    getCarClasses: () => apiJson('/car-classes')
};