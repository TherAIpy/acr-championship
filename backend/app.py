from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
from datetime import datetime
from services.ocr_service import extract_text_from_image
from utils.db import get_supabase

app = Flask(__name__)
CORS(app)

# =============================================================================
# UTILITY
# =============================================================================
def time_str_to_ms(time_str):
    if not time_str or time_str == '-' or not isinstance(time_str, str):
        return 0
    try:
        parts = time_str.replace(',', '.').split(':')
        minutes = int(parts[0])
        seconds_ms = parts[1].split('.')
        seconds = int(seconds_ms[0])
        ms = int(seconds_ms[1]) if len(seconds_ms) > 1 else 0
        return (minutes * 60000) + (seconds * 1000) + ms
    except:
        return 0

def ms_to_time_str(ms):
    if ms == 0: return "00:00.000"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    milliseconds = ms % 1000
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

# =============================================================================
# PARSER (Integrato per evitare errori di import)
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
            
            gap_str = "-"
            if i + 1 < len(lines) and lines[i+1].startswith('+'):
                gap_str = lines[i+1].replace(',', '.')

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
                            "penalty": "-",
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
    rallys = supabase.table('rally').select('*, leg(*, stage(*))').execute()
    return jsonify({"rallys": rallys.data})

@app.route('/api/create-rally', methods=['POST'])
def create_rally():
    data = request.json
    supabase = get_supabase()
    res = supabase.table('rally').insert({"name": data['name']}).execute()
    return jsonify({"success": True, "rally": res.data[0]})

@app.route('/api/create-leg', methods=['POST'])
def create_leg():
    data = request.json
    supabase = get_supabase()
    res = supabase.table('leg').insert({"rally_id": data['rally_id'], "name": data['name']}).execute()
    return jsonify({"success": True, "leg": res.data[0]})

@app.route('/api/save-stage', methods=['POST'])
def save_stage():
    data = request.json
    results = data.get('results', [])
    stage_name = data.get('stage_name')
    track_name = data.get('track_name')
    leg_id = data.get('leg_id')
    
    if not results or not leg_id or not stage_name:
        return jsonify({"success": False, "error": "Dati mancanti"}), 400

    try:
        supabase = get_supabase()
        stage_res = supabase.table('stage').insert({
            "leg_id": leg_id, "name": stage_name, "track_name": track_name
        }).execute()
        stage_id = stage_res.data[0]['id']

        insert_data = []
        for r in results:
            insert_data.append({
                "stage_id": stage_id,
                "position": r.get("position"),
                "driver_name": r.get("driver"),
                "car_model": r.get("car"),
                "time_str": r.get("time"),
                "penalty": r.get("penalty"),
                "gap": r.get("gap")
            })
        supabase.table('stage_result').insert(insert_data).execute()

        return jsonify({"success": True, "message": f"Salvati {len(insert_data)} piloti"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- ENDPOINT PER CALENDARIO PUBBLICO ---
@app.route('/api/calendar', methods=['GET'])
def get_calendar():
    supabase = get_supabase()
    rallys = supabase.table('rally').select('id, name, created_at').order('created_at', desc=True).execute()
    return jsonify(rallys.data)

# --- ENDPOINT PER CLASSIFICHE ---
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
        for r in results.data:
            driver = r['driver_name']
            time_ms = time_str_to_ms(r['time_str'])
            driver_totals[driver] = driver_totals.get(driver, 0) + time_ms
            
        standings = [{"driver": k, "total_ms": v, "total_str": ms_to_time_str(v)} for k, v in driver_totals.items()]
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)