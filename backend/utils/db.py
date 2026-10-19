import os
from supabase import create_client, Client

def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    # Cerca prima la service role key (amministratore), poi la chiave standard
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    
    if not url or not key:
        raise ValueError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY devono essere impostate nelle variabili d'ambiente")
        
    return create_client(url, key)