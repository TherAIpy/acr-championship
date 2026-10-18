from flask import Flask, request, jsonify
from services.ocr_service import extract_text
from services.parser import parse_overall_screen
from services.discord_service import publish_or_update_standings
from services.standings_service import compute_gaps, compute_rally_points
from utils.db import get_supabase

app = Flask(__name__)

@app.route('/api/upload', methods=['POST'])
def upload_screenshot():
    file = request.files['screenshot']
    raw_text = extract_text(file)
    parsed = parse_overall_screen(raw_text)
    return jsonify(parsed)

@app.route('/api/stage/<int:stage_id>/publish', methods=['POST'])
def publish_stage(stage_id):
    data = request.json
    # Salva i risultati su Supabase, calcola gap, aggiorna Discord
    supabase = get_supabase()
    # ... logica di salvataggio ...
    publish_or_update_standings(stage_id, data)
    return jsonify({"ok": True})