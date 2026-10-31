const API_BASE = 'https://acr-backend-wv98.onrender.com/api';

// Questa è la funzione base che fa le chiamate
async function apiCall(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };

    try {
        const sb = window.supabaseClient || window.supabase;
        if (sb) {
            const { data: { session } } = await sb.auth.getSession();
            // FIX C0: Invia il token SOLO se esiste ed è valido
            if (session?.access_token) {
                headers.Authorization = `Bearer ${session.access_token}`;
            }
        }
    } catch (e) {}

    const res = await fetch(url, { ...options, headers });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data; 
}

// Alias per far contenti tutti i tuoi file HTML
window.apiCall = apiCall;
window.apiJson = apiCall; 
window.api = {
    getChampionships: () => apiCall('/championships'),
    getTree: (id) => apiCall(`/tree/${id}`),
    saveChampionship: (data) => apiCall('/create-championship', { method: 'POST', body: JSON.stringify(data) }),
    // ... aggiungi qui eventuali altre funzioni che usavi nella vecchia api.js
};