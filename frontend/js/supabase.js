// frontend/js/supabase.js

// Sostituisci con i tuoi dati reali
const SUPABASE_URL = 'https://rvqorpdebooklqtjbnwl.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ2cW9ycGRlYm9va2xxdGpibndsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTIzMTk2ODIsImV4cCI6MjEwNzg5NTY4Mn0.oaw45keCaaP40J9nfVLaggIyzPJBT-X0XerqlMc5iMk';

// Inizializza il client
const supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// Funzione per ottenere il token corrente
async function getAuthToken() {
    const { data: { session } } = await supabaseClient.auth.getSession();
    return session ? session.access_token : null;
}