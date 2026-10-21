from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
from datetime import datetime
from services.ocr_service import extract_text_from_image
from utils.db import get_supabase
from utils.auth import admin_required
from pydantic import BaseModel, ValidationError
from typing import List, Optional

app = Flask(__name__)
CORS(app)

# =============================================================================
# UTILITY
# =============================================================================
def time_str_to_ms(time_str: str) -> int:
    """
    Converte una stringa tempo in millisecondi interi.
    Supporta formati: "MM:SS.mmm", "MM:SS,mmm", "SS.mmm", "MM:SS".
    I decimali vengono normalizzati a 3 cifre (es. ".6" → 600 ms).
    """
    if not time_str or time_str in ("-", "DNF", "DNS", "DSQ"):
        return 0
    if not isinstance(time_str, str):
        return 0
    s = time_str.strip().replace(',', '.')
    try:
        if ':' in s:
            minutes_part, rest = s.split(':', 1)
            minutes = int(minutes_part)
        else:
            minutes = 0
            rest = s
        if '.' in rest:
            sec_part, frac_part = rest.split('.', 1)
            seconds = int(sec_part)
            # Normalizza i decimali a 3 cifre
            frac_part = frac_part[:3].ljust(3, '0')
            ms = int(frac_part)
        else:
            seconds = int(rest)
            ms = 0
        return (minutes * 60 + seconds) * 1000 + ms
    except (ValueError, IndexError):
        return 0

def ms_to_time_str(ms):
    if ms == 0: return "00:00.000"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    milliseconds = ms % 1000
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

# =============================================================================
# PARSER OCR
# =============================================================================
def parse_overall_text(raw_text):
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    track_name = "Tracciato Sconosciuto"
    
    track_lines = []
    for line in lines:
        if line in ["OVERALL", "SECTORS", "Pos.", "Player"]:
            break
        track_lines.append(line)
    if track_lines:
        track_name = " ".join(track_lines)

    results = []
    for i, line in enumerate(lines):
        time_match = re.search(r'(\d{1,2}:\d{2}[.,]\d{3})', line)
        is_corrupted = False
        if not time_match and i >= 4:
            if re.match(r'^[A-Z0-9]{1,3}$', lines[i-1]) and ' - ' in lines[i-2]:
                is_corrupted = True
        
        if time_match or is_corrupted:
            time_str = time_match.group(1).replace(',', '.') if time_match else "00:00.000"
            
            # Gap
            gap_str = "-"
            if i + 1 < len(lines) and lines[i+1].startswith('+'):
                gap_str = lines[i+1].replace(',', '.')

            # Controllo Penalità (se la riga immediatamente precedente al tempo è una penalità)
            penalty_str = "-"
            if i - 1 >= 0:
                prev_line = lines[i-1]
                # Se la riga precedente contiene secondi/formato di penalità (es. +00:05 o +05.000 o simile)
                if re.search(r'\+\d+', prev_line) and not prev_line.startswith('+00:') and prev_line != gap_str:
                    penalty_str = prev_line
                elif prev_line in ["5s", "10s", "15s", "30s", "60s"]: # formati alternativi di penalità
                    penalty_str = prev_line

            # Ricerca Vettura e Pilota
            car_idx = -1
            for j in range(i-1, max(i-6, -1), -1):
                if " - " in lines[j] or any(brand in lines[j] for brand in ["Alpine", "Fiat", "Ford", "Toyota", "Subaru", "Citroen", "Hyundai", "Skoda"]):
                    car_idx = j
                    break
            
            if car_idx != -1:
                car_model = lines[car_idx]
                player_idx = car_idx - 1
                if player_idx >= 0 and (lines[player_idx].isdigit() or lines[player_idx] in ["H2", "H1"]):
                    player_idx -= 1
                if player_idx >= 0:
                    player_raw = lines[player_idx]
                    player_name = re.sub(r'^\d+[\s\.\-]*', '', player_raw).strip()
                    player_name = re.sub(r'^[^a-zA-Z0-9\[\]\(\)]+', '', player_name).strip()
                    
                    if player_name and player_name not in ["Player", "Pos.", "Class", "Penalty", "Time"]:
                        results.append({
                            "position": len(results) + 1,
                            "driver": player_name,
                            "car": car_model,
                            "time": time_str,
                            "penalty": penalty_str,
                            "gap": gap_str
                        })

    unique_results = []
    seen = set()
    for r in results:
        if (r['driver'], r['time']) not in seen:
            unique_results.append(r)
            seen.add((r['driver'], r['time']))
            
    for idx, r in enumerate(unique_results):
        r['position'] = idx + 1

    return {"track_name": track_name, "results": unique_results}

# =============================================================================
# ROTTE API
# =============================================================================
@app.route('/')
def health_check():
    return jsonify({"status": "online"})

@app.route('/api/upload', methods=['POST'])
def upload_image():
    if 'screenshot' not in request.files:
        return jsonify({"success": False, "error": "Nessun file caricato"}), 400
    file = request.files['screenshot']
    
    try:
        image_bytes = file.read()
        raw_text = extract_text_from_image(image_bytes)
        parsed_data = parse_overall_text(raw_text)
        return jsonify({
            "success": True,
            "raw_text": raw_text,
            "track_name": parsed_data["track_name"],
            "parsed_data": parsed_data["results"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/setup', methods=['GET'])
def get_setup():
    supabase = get_supabase()
    # Recupera i campionati con la gerarchia completa: championship -> rally -> leg -> stage
    champs = supabase.table('championship').select('*, rally(*, leg(*, stage(*)))').execute()
    return jsonify({"championships": champs.data})

@app.route('/api/create-championship', methods=['POST'])
@admin_required
def create_championship():
    data = request.json
    supabase = get_supabase()
    res = supabase.table('championship').insert({"name": data['name']}).execute()
    return jsonify({"success": True, "championship": res.data[0]})

@app.route('/api/create-rally', methods=['POST'])
def create_rally():
    data = request.json
    supabase = get_supabase()
    res = supabase.table('rally').insert({
        "name": data['name'],
        "championship_id": data.get('championship_id')
    }).execute()
    return jsonify({"success": True, "rally": res.data[0]})

@app.route('/api/create-leg', methods=['POST'])
def create_leg():
    data = request.json
    supabase = get_supabase()
    res = supabase.table('leg').insert({"rally_id": data['rally_id'], "name": data['name']}).execute()
    return jsonify({"success": True, "leg": res.data[0]})

class StageResultIn(BaseModel):
    position: Optional[int] = None
    driver: str
    car: Optional[str] = None
    time: str
    penalty: Optional[str] = "-"
    gap: Optional[str] = None
    car_id: Optional[int] = None
    car_class_id: Optional[int] = None
    is_disqualified: bool = False

class SaveStageIn(BaseModel):
    stage_id: Optional[int] = None
    stage_name: str
    track_name: Optional[str] = None
    leg_id: Optional[int] = None
    results: List[StageResultIn] = []

@app.route('/api/save-stage', methods=['POST'])
@admin_required
def save_stage():
    try:
        payload = SaveStageIn(**request.json)
    except ValidationError as e:
        return jsonify({"success": False, "error": e.errors()}), 400

    supabase = get_supabase()

    if payload.stage_id:
        # Aggiornamento: verifica che lo stage esista
        existing = supabase.table('stage').select('id').eq('id', payload.stage_id).execute()
        if not existing.data:
            return jsonify({"success": False, "error": "Stage non trovato"}), 404
        stage_id = payload.stage_id
        supabase.table('stage').update({
            "name": payload.stage_name,
            "track_name": payload.track_name
        }).eq('id', stage_id).execute()
        # Sostituisci i risultati (in una transazione ideale; qui usiamo delete + insert)
        supabase.table('stage_result').delete().eq('stage_id', stage_id).execute()
    else:
        if not payload.leg_id:
            return jsonify({"success": False, "error": "leg_id obbligatorio per la creazione"}), 400
        stage_res = supabase.table('stage').insert({
            "leg_id": payload.leg_id,
            "name": payload.stage_name,
            "track_name": payload.track_name
        }).execute()
        stage_id = stage_res.data[0]['id']

    if payload.results:
        insert_data = [{
            "stage_id": stage_id,
            "position": r.position,
            "driver_name": r.driver,
            "car_model": r.car,
            "time_str": r.time,
            "penalty": r.penalty,
            "gap": r.gap,
            "car_id": r.car_id,
            "car_class_id": r.car_class_id,
            "is_disqualified": r.is_disqualified
        } for r in payload.results]
        supabase.table('stage_result').insert(insert_data).execute()

    return jsonify({"success": True, "stage_id": stage_id, "saved": len(payload.results)})

@app.route('/api/championships', methods=['GET'])
def get_championships():
    try:
        supabase = get_supabase()
        # Prende tutti i campionati, ordinati dal più recente
        res = supabase.table('championship').select('*').order('created_at', desc=True).execute()
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/calendar', methods=['GET'])
def get_calendar():
    supabase = get_supabase()
    rallys = supabase.table('rally').select('id, name, created_at').order('created_at', desc=True).execute()
    return jsonify(rallys.data)

@app.route('/api/standings/<type>/<int:id>', methods=['GET'])
def get_standings(type, id):
    supabase = get_supabase()
    
    if type == 'stage':
        res = supabase.table('stage_result').select('*').eq('stage_id', id).order('position').execute()
        return jsonify(res.data)
        
    elif type == 'leg':
        stages = supabase.table('stage').select('id').eq('leg_id', id).execute()
        stage_ids = [s['id'] for s in stages.data]
        if not stage_ids: return jsonify([])
        
        results = supabase.table('stage_result').select('*').in_('stage_id', stage_ids).execute()
        driver_totals = {}
        driver_penalties = {}
        for r in results.data:
            driver = r['driver_name']
            time_ms = time_str_to_ms(r['time_str'])
            driver_totals[driver] = driver_totals.get(driver, 0) + time_ms
            if r.get('penalty') and r['penalty'] != '-':
                driver_penalties[driver] = r['penalty'] # Conserva l'ultima o accumula se necessario
            
        standings = [{"driver": k, "total_ms": v, "total_str": ms_to_time_str(v), "penalty": driver_penalties.get(k, "-")} for k, v in driver_totals.items()]
        standings.sort(key=lambda x: x['total_ms'])
        
        leader_ms = standings[0]['total_ms'] if standings else 0
        prev_ms = 0
        for i, s in enumerate(standings):
            s['position'] = i + 1
            s['gap_prev'] = "-" if i == 0 else ms_to_time_str(s['total_ms'] - prev_ms)
            s['gap_first'] = "-" if i == 0 else ms_to_time_str(s['total_ms'] - leader_ms)
            prev_ms = s['total_ms']
        return jsonify(standings)

    elif type == 'rally':
        legs = supabase.table('leg').select('id').eq('rally_id', id).execute()
        leg_ids = [l['id'] for l in legs.data]
        if not leg_ids: return jsonify([])
        
        stages = supabase.table('stage').select('id').in_('leg_id', leg_ids).execute()
        stage_ids = [s['id'] for s in stages.data]
        if not stage_ids: return jsonify([])
        
        results = supabase.table('stage_result').select('*').in_('stage_id', stage_ids).execute()
        driver_totals = {}
        for r in results.data:
            driver = r['driver_name']
            time_ms = time_str_to_ms(r['time_str'])
            driver_totals[driver] = driver_totals.get(driver, 0) + time_ms
            
        standings = [{"driver": k, "total_ms": v, "total_str": ms_to_time_str(v)} for k, v in driver_totals.items()]
        standings.sort(key=lambda x: x['total_ms'])
        
        points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
        leader_ms = standings[0]['total_ms'] if standings else 0
        prev_ms = 0
        for i, s in enumerate(standings):
            s['position'] = i + 1
            s['gap_prev'] = "-" if i == 0 else ms_to_time_str(s['total_ms'] - prev_ms)
            s['gap_first'] = "-" if i == 0 else ms_to_time_str(s['total_ms'] - leader_ms)
            s['points'] = points_system[i] if i < len(points_system) else 0
            prev_ms = s['total_ms']
        return jsonify(standings)

    elif type == 'championship':
        rallys = supabase.table('rally').select('id').eq('championship_id', id).execute()
        rally_ids = [r['id'] for r in rallys.data]
        if not rally_ids: return jsonify([])
        
        champ_points = {}
        points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
        
        for r_id in rally_ids:
            legs = supabase.table('leg').select('id').eq('rally_id', r_id).execute()
            leg_ids = [l['id'] for l in legs.data]
            if not leg_ids: continue
            
            stages = supabase.table('stage').select('id').in_('leg_id', leg_ids).execute()
            stage_ids = [s['id'] for s in stages.data]
            if not stage_ids: continue
            
            results = supabase.table('stage_result').select('*').in_('stage_id', stage_ids).execute()
            driver_totals = {}
            for r in results.data:
                driver = r['driver_name']
                time_ms = time_str_to_ms(r['time_str'])
                driver_totals[driver] = driver_totals.get(driver, 0) + time_ms
                
            standings = [{"driver": k, "total_ms": v} for k, v in driver_totals.items()]
            standings.sort(key=lambda x: x['total_ms'])
            
            for i, s in enumerate(standings):
                pts = points_system[i] if i < len(points_system) else 0
                drv = s['driver']
                champ_points[drv] = champ_points.get(drv, 0) + pts
                
        final_standings = [{"driver": k, "points": v} for k, v in champ_points.items()]
        final_standings.sort(key=lambda x: x['points'], reverse=True)
        for i, s in enumerate(final_standings):
            s['position'] = i + 1
        return jsonify(final_standings)

@app.route('/api/championship/<int:champ_id>', methods=['GET'])
def get_championship_detail(champ_id):
    try:
        supabase = get_supabase()
        res = supabase.table('championship').select('*').eq('id', champ_id).execute()
        if not res.data:
            return jsonify({"success": False, "error": "Campionato non trovato"}), 404
        return jsonify({"success": True, "data": res.data[0]}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/rallies/<int:champ_id>', methods=['GET'])
def get_rallies(champ_id):
    try:
        supabase = get_supabase()
        res = supabase.table('rally').select('*').eq('championship_id', champ_id).order('id', desc=False).execute()
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/create-rally', methods=['POST'])
@admin_required
def create_rally():
    try:
        data = request.json
        supabase = get_supabase()
        res = supabase.table('rally').insert({
            "championship_id": data['championship_id'],
            "name": data['name'],
            "discord_webhook_url": data.get('discord_webhook_url', '')
        }).execute()
        return jsonify({"success": True, "data": res.data[0]}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)