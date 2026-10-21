// frontend/js/api.js

const API_BASE_URL = 'https://acr-backend-wv98.onrender.com/api';

async function apiCall(endpoint, method = 'GET', body = null) {
    const token = await getAuthToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const config = { method: method, headers: headers };
    if (body) config.body = JSON.stringify(body);

    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, config);
        
        if (response.status === 401 || response.status === 403) {
            console.error("Accesso negato o sessione scaduta.");
        }

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Errore API');
        return data;
    } catch (error) {
        console.error(`Errore chiamata ${endpoint}:`, error);
        throw error;
    }
}

const api = {
    getChampionships: () => apiCall('/championships'),
    getChampionshipDetail: (champ_id) => apiCall(`/championship/${champ_id}`),
    getRallies: (champ_id) => apiCall(`/rallies/${champ_id}`),
    saveChampionship: (data) => apiCall('/create-championship', 'POST', data),
    createRally: (data) => apiCall('/create-rally', 'POST', data),
    saveStage: (data) => apiCall('/save-stage', 'POST', data)
};