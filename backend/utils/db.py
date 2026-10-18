import os
from supabase import create_client, Client

def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") # Usa la service_role key qui
    
    if not url or not key:
        raise ValueError("SUPABASE_URL e SUPABASE_KEY devono essere impostate nelle variabili d'ambiente")
        
    return create_client(url, key)