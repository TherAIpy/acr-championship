// frontend/js/auth.js

// js/auth.js

async function initAuthUI() {
    const { data: { session } } = await supabase.auth.getSession();
    const userEmail = document.getElementById('user-email');
    const btnLogout = document.getElementById('btn-logout');
    const btnLogin = document.getElementById('btn-login');

    if (session) {
        // Utente Loggato
        if (userEmail) {
            userEmail.textContent = session.user.email;
            userEmail.classList.remove('hidden');
        }
        if (btnLogout) {
            btnLogout.classList.remove('hidden');
            // Rimuoviamo eventuali listener precedenti per evitare doppioni
            btnLogout.onclick = async () => {
                await supabase.auth.signOut();
                window.location.reload();
            };
        }
        if (btnLogin) btnLogin.classList.add('hidden');
    } else {
        // Utente NON Loggato
        if (userEmail) userEmail.classList.add('hidden');
        if (btnLogout) btnLogout.classList.add('hidden');
        if (btnLogin) btnLogin.classList.remove('hidden');
    }

    applyAdminUI(session);
}

function applyAdminUI(session = null) {
    // Se non passiamo la sessione, la recuperiamo (utile se chiamata da altre funzioni)
    if (!session) {
        supabase.auth.getSession().then(({ data }) => applyAdminUI(data.session));
        return;
    }

    const isAdmin = session && session.user && session.user.app_metadata && session.user.app_metadata.role === 'admin';
    const adminElements = document.querySelectorAll('.admin-only');

    adminElements.forEach(el => {
        if (isAdmin) {
            el.classList.remove('hidden');
        } else {
            el.classList.add('hidden');
        }
    });
}