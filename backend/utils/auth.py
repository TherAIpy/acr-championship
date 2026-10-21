from functools import wraps
from flask import request, jsonify
from utils.db import get_supabase

def get_user_from_token(token: str):
    supabase = get_supabase()
    try:
        res = supabase.auth.get_user(token)
        return res.user if res.user else None
    except Exception:
        return None

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "error": "Token mancante"}), 401
        token = auth_header.split(" ", 1)[1]
        user = get_user_from_token(token)
        if not user:
            return jsonify({"success": False, "error": "Token non valido"}), 401
        role = (user.app_metadata or {}).get("role")
        if role != "admin":
            return jsonify({"success": False, "error": "Accesso negato"}), 403
        return f(*args, **kwargs)
    return decorated