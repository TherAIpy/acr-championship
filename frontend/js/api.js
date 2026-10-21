// frontend/js/api.js

const API_BASE_URL = 'https://acr-backend-wv98.onrender.com/api';

// Funzione generica per le chiamate
async function apiCall(endpoint, method = 'GET', body = null) {
    const token = await getAuthToken();
    const headers = {
        'Content-Type': 'application/json'
    };
    
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const config = {
        method: method,
        headers: headers
    };

    if (body) {
        config.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, config);
        
        // Se il backend risponde 401 (token scaduto/non valido) o 403 (non admin)
        if (response.status === 401 || response.status === 403) {
            console.error("Accesso negato o sessione scaduta.");
            // Opzionale: logout forzato o redirect al login
            // supabaseClient.auth.signOut();
            // window.location.href = '/login.html';
        }

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Errore API');
        return data;
    } catch (error) {
        console.error(`Errore chiamata ${endpoint}:`, error);
        throw error;
    }
}

// Endpoint specifici (esempi che userai tra poco)
const api = {
    getChampionships: () => apiCall('/championships'),
    getChampionshipDetail: (champ_id) => apiCall(`/championship/${champ_id}`),
    getRallies: (champ_id) => apiCall(`/rallies/${champ_id}`),
    saveStage: (data) => apiCall('/save-stage', 'POST', data),
    saveChampionship: (data) => apiCall('/create-championship', 'POST', data)
    // ... altri endpoint
};