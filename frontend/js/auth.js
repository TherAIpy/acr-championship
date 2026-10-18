import { supabase } from './supabase.js';

document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = document.getElementById('email').value;
  const password = document.getElementById('password').value;

  const { data, error } = await supabase.auth.signInWithPassword({ email, password });

  if (error) {
    const errEl = document.getElementById('error');
    errEl.textContent = error.message;
    errEl.classList.remove('hidden');
    return;
  }

  // Recupera il ruolo dai metadati utente
  const role = data.user.user_metadata.role || 'driver';
  window.location.href = role === 'admin' ? '/dashboard.html?role=admin' : '/dashboard.html?role=driver';
});