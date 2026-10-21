// frontend/js/auth.js

async function login(email, password) {
    const { data, error } = await supabaseClient.auth.signInWithPassword({
        email: email,
        password: password,
    });
    if (error) throw error;
    return data;
}

async function logout() {
    await supabaseClient.auth.signOut();
    window.location.reload();
}

async function getCurrentUser() {
    const { data: { user } } = await supabaseClient.auth.getUser();
    return user;
}

// Controlla se l'utente ha il ruolo admin (salvato in app_metadata)
async function isAdmin() {
    const user = await getCurrentUser();
    if (!user) return false;
    // Controlla app_metadata (non user_metadata!)
    return user.app_metadata?.role === 'admin';
}

// Funzione di inizializzazione UI
async function initAuthUI() {
    const user = await getCurrentUser();
    
    // Esempio: nascondi i bottoni di scrittura se non sei admin
    const adminElements = document.querySelectorAll('.admin-only');
    
    if (user && await isAdmin()) {
        // Mostra elementi admin
        adminElements.forEach(el => el.style.display = 'block');
    } else {
        // Nascondi elementi admin
        adminElements.forEach(el => el.style.display = 'none');
    }

    // Aggiorna il nome utente nell'header se presente
    const userNameEl = document.getElementById('user-name');
    if (userNameEl && user) {
        userNameEl.textContent = user.email; // O user.user_metadata.full_name
    }
}

// Ascolta i cambiamenti di stato dell'autenticazione
supabaseClient.auth.onAuthStateChange((event, session) => {
    if (event === 'SIGNED_IN') {
        console.log('Utente loggato');
        initAuthUI();
    } else if (event === 'SIGNED_OUT') {
        console.log('Utente sloggato');
        initAuthUI();
    }
});