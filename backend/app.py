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

import requests
from urllib.parse import urlparse

# =============================================================================
# DISCORD HELPERS
# =============================================================================
DISCORD_API_BASE = "https://discord.com/api/v10"

def _parse_webhook_id(webhook_url: str) -> str:
    """Estrae l'ID del webhook dall'URL Discord.
    Es: https://discord.com/api/webhooks/1234567890/abcdef → '1234567890'"""
    if not webhook_url:
        return ""
    try:
        m = re.search(r'/webhooks/(\d+)/', webhook_url)
        return m.group(1) if m else ""
    except Exception:
        return ""

def _send_discord_message(webhook_url: str, embed: dict, thread_id: str = None) -> dict:
    """Invia un nuovo messaggio via webhook.
    Restituisce {'ok': True, 'message_id': str} oppure {'ok': False, 'error': str}."""
    if not webhook_url:
        return {"ok": False, "error": "webhook_url vuoto"}

    url = webhook_url
    params = {"wait": "true"}          # fondamentale: senza wait, Discord non restituisce l'ID
    if thread_id:
        params["thread_id"] = thread_id

    payload = {"embeds": [embed]}

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.post(url, params=params, json=payload, headers=headers, timeout=10)
        if r.status_code in (200, 204):
            data = r.json() if r.text else {}
            return {"ok": True, "message_id": str(data.get("id", ""))}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _edit_discord_message(webhook_url: str, message_id: str, embed: dict, thread_id: str = None) -> dict:
    """Modifica un messaggio esistente inviato dallo stesso webhook."""
    if not webhook_url or not message_id:
        return {"ok": False, "error": "webhook_url o message_id mancante"}

    url = f"{webhook_url.rstrip('/')}/messages/{message_id}"
    params = {}
    if thread_id:
        params["thread_id"] = thread_id

    payload = {"embeds": [embed]}

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.patch(url, params=params or None, json=payload, headers=headers, timeout=10)
        if r.status_code in (200, 204):
            return {"ok": True}
        # Se il messaggio è stato cancellato a mano su Discord, lo trattiamo come "da ricreare"
        if r.status_code == 404:
            return {"ok": False, "error": "message_not_found", "code": 404}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _upsert_discord_message(entity_type: str, entity_id: int, webhook_url: str,
                            embed: dict, thread_id: str = None) -> dict:
    """Logica POST vs PATCH:
       - se esiste già un record (entity_type, entity_id, webhook_id): PATCH
       - altrimenti: POST e salva il message_id
       - se il PATCH fallisce con 404 (messaggio cancellato): ricrea con POST
    Restituisce {'ok': bool, 'action': 'created'|'updated'|'recreated', 'message_id': str}."""
    webhook_id = _parse_webhook_id(webhook_url)
    if not webhook_id:
        return {"ok": False, "error": "webhook_url non valido"}

    supabase = get_supabase()

    existing = (
        supabase.table('discord_publish_log')
        .select('id, message_id')
        .eq('entity_type', entity_type)
        .eq('entity_id', entity_id)
        .eq('webhook_id', webhook_id)
        .execute()
    ).data

    if existing:
        message_id = existing[0]['message_id']
        edit = _edit_discord_message(webhook_url, message_id, embed, thread_id)
        if edit['ok']:
            supabase.table('discord_publish_log').update({
                "updated_at": datetime.utcnow().isoformat()
            }).eq('id', existing[0]['id']).execute()
            return {"ok": True, "action": "updated", "message_id": message_id}

        if edit.get('code') == 404:
            # Il messaggio non esiste più su Discord: lo ricreiamo
            sent = _send_discord_message(webhook_url, embed, thread_id)
            if not sent['ok']:
                return {"ok": False, "error": sent['error']}
            supabase.table('discord_publish_log').update({
                "message_id": sent['message_id'],
                "updated_at": datetime.utcnow().isoformat()
            }).eq('id', existing[0]['id']).execute()
            return {"ok": True, "action": "recreated", "message_id": sent['message_id']}

        return {"ok": False, "error": edit['error']}

    # Primo invio
    sent = _send_discord_message(webhook_url, embed, thread_id)
    if not sent['ok']:
        return {"ok": False, "error": sent['error']}

    supabase.table('discord_publish_log').insert({
        "entity_type": entity_type,
        "entity_id": entity_id,
        "webhook_id": webhook_id,
        "message_id": sent['message_id']
    }).execute()

    return {"ok": True, "action": "created", "message_id": sent['message_id']}

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

@app.route('/api/delete-championship/<int:champ_id>', methods=['DELETE'])
@admin_required
def delete_championship(champ_id):
    try:
        supabase = get_supabase()
        
        # 1. Trova tutti i Rally del campionato
        rallies = supabase.table('rally').select('id').eq('championship_id', champ_id).execute().data
        rally_ids = [r['id'] for r in rallies]
        
        if rally_ids:
            # 2. Trova tutte le Leg
            legs = supabase.table('leg').select('id').in_('rally_id', rally_ids).execute().data
            leg_ids = [l['id'] for l in legs]
            
            if leg_ids:
                # 3. Trova tutte le Stage
                stages = supabase.table('stage').select('id').in_('leg_id', leg_ids).execute().data
                stage_ids = [s['id'] for s in stages]
                
                if stage_ids:
                    # 4. Elimina i risultati delle stage
                    supabase.table('stage_result').delete().in_('stage_id', stage_ids).execute()
                
                # 5. Elimina le Stage, Leg e Rally
                supabase.table('stage').delete().in_('leg_id', leg_ids).execute()
                supabase.table('leg').delete().in_('rally_id', rally_ids).execute()
            supabase.table('rally').delete().eq('championship_id', champ_id).execute()
            
        # 6. Elimina il Campionato
        supabase.table('championship').delete().eq('id', champ_id).execute()
        
        return jsonify({"success": True, "message": "Campionato eliminato"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/delete-rally/<int:rally_id>', methods=['DELETE'])
@admin_required
def delete_rally(rally_id):
    try:
        supabase = get_supabase()
        legs = supabase.table('leg').select('id').eq('rally_id', rally_id).execute().data
        leg_ids = [l['id'] for l in legs]
        if leg_ids:
            stages = supabase.table('stage').select('id').in_('leg_id', leg_ids).execute().data
            stage_ids = [s['id'] for s in stages]
            if stage_ids:
                supabase.table('stage_result').delete().in_('stage_id', stage_ids).execute()
                supabase.table('stage').delete().in_('leg_id', leg_ids).execute()
            supabase.table('leg').delete().eq('rally_id', rally_id).execute()
        supabase.table('rally').delete().eq('id', rally_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/create-rally', methods=['POST'])
@admin_required
def create_rally():
    try:
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        if start_date == "": start_date = None
        if end_date == "": end_date = None

        res = supabase.table('rally').insert({
            "championship_id": data['championship_id'],
            "name": data['name'],
            "discord_webhook_url": data.get('discord_webhook_url', ''),
            "start_date": start_date,
            "end_date": end_date
        }).execute()
        return jsonify({"success": True, "data": res.data[0]}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def _update_rally_dates(rally_id):
    """Calcola la data minima e massima delle Leg e aggiorna il Rally."""
    supabase = get_supabase()
    legs = supabase.table('leg').select('date').eq('rally_id', rally_id).not_.is_('date', 'null').execute().data
    if legs:
        dates = sorted([l['date'] for l in legs if l['date']])
        if dates:
            supabase.table('rally').update({
                'start_date': dates[0],
                'end_date': dates[-1]
            }).eq('id', rally_id).execute()

@app.route('/api/create-leg', methods=['POST'])
@admin_required
def create_leg():
    try:
        data = request.json or {}
        rally_id = data.get('rally_id')
        name = (data.get('name') or '').strip()
        if not rally_id or not name:
            return jsonify({"success": False, "error": "rally_id e name obbligatori"}), 400

        supabase = get_supabase()
        rally = supabase.table('rally').select('id').eq('id', rally_id).execute()
        if not rally.data:
            return jsonify({"success": False, "error": "Rally non trovato"}), 404

        leg_date = data.get('date')
        if leg_date == "" or leg_date is None: leg_date = None

        res = supabase.table('leg').insert({
            "rally_id": rally_id,
            "name": name,
            "date": leg_date
        }).execute()
        
        # Aggiorna le date del rally genitore
        _update_rally_dates(rally_id)
        
        return jsonify({"success": True, "data": res.data[0]}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/legs/<int:rally_id>', methods=['GET'])
def get_legs(rally_id):
    try:
        supabase = get_supabase()
        res = supabase.table('leg').select('*').eq('rally_id', rally_id).order('id').execute()
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/create-stage', methods=['POST'])
@admin_required
def create_stage():
    try:
        data = request.json or {}
        leg_id = data.get('leg_id')
        name = (data.get('name') or '').strip()
        
        if not leg_id or not name:
            return jsonify({"success": False, "error": "leg_id e name obbligatori"}), 400

        supabase = get_supabase()
        track_id = data.get('track_id')
        
        # 1. Recupera i km dal database in base al track_id
        distance_km = data.get('distance_km', 0)
        if track_id:
            track_res = supabase.table('track').select('length_km').eq('id', track_id).execute()
            if track_res.data and track_res.data[0].get('length_km'):
                distance_km = track_res.data[0]['length_km']

        # 2. Inserisci la stage con i km calcolati
        res = supabase.table('stage').insert({
            "leg_id": leg_id,
            "name": name,
            "track_id": track_id,
            "track_name": data.get('track_name') or name,
            "weather": data.get('weather', 'Sereno'),
            "surface": data.get('surface', 'Asfalto'),
            "temperature": data.get('temperature', 20),
            "start_time": data.get('start_time') if data.get('start_time') else None,
            "distance_km": distance_km # <-- Aggiunto qui!
        }).execute()
        
        return jsonify({"success": True, "data": res.data[0]}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/stages/<int:leg_id>', methods=['GET'])
def get_stages(leg_id):
    try:
        supabase = get_supabase()
        res = supabase.table('stage').select('*').eq('leg_id', leg_id).order('id').execute()
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/stage-results/<int:stage_id>', methods=['GET'])
def get_stage_results(stage_id):
    """Risultati ufficiali (pubblici)."""
    try:
        supabase = get_supabase()
        res = (
            supabase.table('stage_result').select('*')
            .eq('stage_id', stage_id)
            .eq('is_official', True)
            .order('position')
            .execute()
        )
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/stage-results/<int:stage_id>/draft', methods=['GET'])
@admin_required
def get_stage_results_draft(stage_id):
    """Bozze (solo admin)."""
    try:
        supabase = get_supabase()
        res = (
            supabase.table('stage_result').select('*')
            .eq('stage_id', stage_id)
            .eq('is_official', False)
            .order('position')
            .execute()
        )
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/update-stage/<int:stage_id>', methods=['PUT'])
@admin_required
def update_stage(stage_id):
    try:
        data = request.json or {}
        supabase = get_supabase()
        
        update_data = {
            "name": data.get('name'),
            "track_id": data.get('track_id'),
            "track_name": data.get('track_name'),
            "weather": data.get('weather'),
            "surface": data.get('surface'),
            "temperature": data.get('temperature'),
            "start_time": data.get('start_time') if data.get('start_time') else None
        }
        
        # Recupera i km dal database in base al track_id
        if data.get('track_id'):
            track_res = supabase.table('track').select('length_km').eq('id', data.get('track_id')).execute()
            if track_res.data and track_res.data[0].get('length_km'):
                update_data['distance_km'] = track_res.data[0]['length_km']
        
        res = supabase.table('stage').update(update_data).eq('id', stage_id).execute()
        return jsonify({"success": True, "data": res.data[0]}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/track-results/<int:track_id>', methods=['GET'])
def get_track_results(track_id):
    try:
        supabase = get_supabase()
        
        # 1. Prendi le info del TRACCIATO (non dello stage)
        track_res = supabase.table('track').select('name, location, length_km').eq('id', track_id).execute()
        track_info = track_res.data[0] if track_res.data else {"name": "Tracciato Sconosciuto", "location": "", "length_km": 0}
        
        # 2. Trova tutte le stage (sessioni) che usano questo tracciato
        stages = supabase.table('stage').select('id').eq('track_id', track_id).execute().data
        if not stages:
            return jsonify({"success": True, "data": [], "track_info": track_info}), 200

        stage_ids = [s['id'] for s in stages]

        # 3. Prendi i risultati ufficiali di tutte quelle stage
        results = supabase.table('stage_result').select('*').in_('stage_id', stage_ids).eq('is_official', True).execute().data

        # 4. Aggiungi i metadati meteo per contestualizzare i tempi
        stages_meta = supabase.table('stage').select('id, weather, surface, temperature').in_('id', stage_ids).execute().data
        meta_dict = {s['id']: s for s in stages_meta}

        for r in results:
            m = meta_dict.get(r['stage_id'], {})
            r['weather'] = m.get('weather')
            r['surface'] = m.get('surface')
            r['temperature'] = m.get('temperature')

        # 5. Ordina per tempo
        results.sort(key=lambda x: x['time_ms'])

        return jsonify({"success": True, "data": results, "track_info": track_info}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/tree/<int:champ_id>', methods=['GET'])
def get_tree(champ_id):
    try:
        supabase = get_supabase()
        champ = supabase.table('championship').select('*').eq('id', champ_id).execute()
        if not champ.data:
            return jsonify({"success": False, "error": "Campionato non trovato"}), 404

        rallies = supabase.table('rally').select('*').eq('championship_id', champ_id).order('id').execute().data or []
        if not rallies:
            return jsonify({"success": True, "data": {"championship": champ.data[0], "rallies": []}}), 200

        rally_ids = [r['id'] for r in rallies]
        legs = supabase.table('leg').select('*').in_('rally_id', rally_ids).order('id').execute().data or []
        leg_ids = [l['id'] for l in legs]
        stages = supabase.table('stage').select('*').in_('leg_id', leg_ids).order('id').execute().data if leg_ids else []

        # Recuperiamo i risultati separando bozze e ufficiali
        stage_ids = [s['id'] for s in stages]
        results = (
            supabase.table('stage_result')
            .select('stage_id, is_official')
            .in_('stage_id', stage_ids)
            .execute().data
        ) if stage_ids else []

        stages_with_official = set(r['stage_id'] for r in results if r.get('is_official'))
        stages_with_draft    = set(r['stage_id'] for r in results if not r.get('is_official'))

        champ_stats = {'total_rallies': len(rallies), 'total_legs': 0, 'total_stages': 0, 'total_km': 0.0, 'is_completed': True}
        
        stages_by_leg = {}
        for s in stages:
            s['has_official_results'] = s['id'] in stages_with_official
            s['has_draft_results']    = s['id'] in stages_with_draft
            s['has_results']          = s['id'] in stages_with_official
            stages_by_leg.setdefault(s['leg_id'], []).append(s)
            
        legs_by_rally = {}
        for l in legs:
            l['stages'] = stages_by_leg.get(l['id'], [])
            
            # Una Leg è completa se tutte le sue stage hanno risultati ufficiali
            l['is_completed'] = len(l['stages']) > 0 and all(s['has_official_results'] for s in l['stages'])
            
            l['total_stages'] = len(l['stages'])
            l['total_km'] = sum(float(s.get('distance_km', 0) or 0) for s in l['stages'])
            
            legs_by_rally.setdefault(l['rally_id'], []).append(l)
            
            champ_stats['total_legs'] += 1
            champ_stats['total_stages'] += l['total_stages']
            champ_stats['total_km'] += l['total_km']
            
        for r in rallies:
            r['legs'] = legs_by_rally.get(r['id'], [])
            
            # Un Rally è completo se tutte le sue Leg sono complete (e ha almeno una Leg)
            r['is_completed'] = len(r['legs']) > 0 and all(l['is_completed'] for l in r['legs'])
            if not r['is_completed']:
                champ_stats['is_completed'] = False # Se un rally non è completo, il campionato non lo è
                
            r['total_legs'] = len(r['legs'])
            r['total_stages'] = sum(l['total_stages'] for l in r['legs'])
            r['total_km'] = sum(l['total_km'] for l in r['legs'])

        # Aggiungi le statistiche all'oggetto campionato
        champ_data = champ.data[0]
        champ_data.update(champ_stats)

        return jsonify({"success": True, "data": {"championship": champ_data, "rallies": rallies}}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

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
    leg_id: Optional[int] = None
    track_id: Optional[int] = None
    distance_km: Optional[float] = None
    weather: Optional[str] = "Sereno"
    temperature: Optional[int] = 20
    surface: Optional[str] = "Asfalto"     # NUOVO
    start_time: Optional[str] = None       # NUOVO
    notes: Optional[str] = None            # NUOVO
    results: List[StageResultIn] = []

@app.route('/api/save-stage', methods=['POST'])
@admin_required
def save_stage():
    try:
        try:
            payload = SaveStageIn(**request.json)
        except ValidationError as e:
            return jsonify({"success": False, "error": e.errors()}), 400

        supabase = get_supabase()
        
        # --- LOGICA PER TROVARE IL CAMPIONATO E LE CLASSI AMMESSE ---
        allowed_classes = []
        # Se stiamo modificando una stage esistente, risaliamo al campionato
        target_stage_id = payload.stage_id
        if not target_stage_id and payload.leg_id:
            # Se è nuova, risaliamo dalla Leg
            leg_res = supabase.table('leg').select('rally_id').eq('id', payload.leg_id).execute()
            if leg_res.data:
                rally_res = supabase.table('rally').select('championship_id').eq('id', leg_res.data[0]['rally_id']).execute()
                if rally_res.data:
                    champ_res = supabase.table('championship').select('allowed_classes').eq('id', rally_res.data[0]['championship_id']).execute()
                    if champ_res.data:
                        allowed_classes = champ_res.data[0].get('allowed_classes') or []
        
        if target_stage_id:
            # Se è un update, risaliamo dalla Stage esistente
            stage_res = supabase.table('stage').select('leg_id').eq('id', target_stage_id).execute()
            if stage_res.data:
                leg_res = supabase.table('leg').select('rally_id').eq('id', stage_res.data[0]['leg_id']).execute()
                if leg_res.data:
                    rally_res = supabase.table('rally').select('championship_id').eq('id', leg_res.data[0]['rally_id']).execute()
                    if rally_res.data:
                        champ_res = supabase.table('championship').select('allowed_classes').eq('id', rally_res.data[0]['championship_id']).execute()
                        if champ_res.data:
                            allowed_classes = champ_res.data[0].get('allowed_classes') or []

        # Metadati Stage
        stage_meta = {
            "name": payload.stage_name,
            "track_id": payload.track_id,
            "distance_km": payload.distance_km,
            "weather": payload.weather,
            "temperature": payload.temperature,
            "surface": payload.surface,
            "start_time": payload.start_time if payload.start_time else None,
            "notes": payload.notes
        }

        if payload.track_id:
            track_res = supabase.table('track').select('name').eq('id', payload.track_id).execute()
            if track_res.data:
                stage_meta['track_name'] = track_res.data[0]['name']

        if payload.stage_id:
            existing = supabase.table('stage').select('id').eq('id', payload.stage_id).execute()
            if not existing.data:
                return jsonify({"success": False, "error": "Stage non trovato"}), 404
            stage_id = payload.stage_id
            
            if payload.stage_name and payload.stage_name != "Update":
                supabase.table('stage').update(stage_meta).eq('id', stage_id).execute()

            ver_res = supabase.table('stage_result').select('version').eq('stage_id', stage_id).order('version', desc=True).limit(1).execute()
            next_version = (ver_res.data[0]['version'] + 1) if ver_res.data else 1
                
            supabase.table('stage_result').delete().eq('stage_id', stage_id).eq('is_official', False).execute()
        else:
            if not payload.leg_id:
                return jsonify({"success": False, "error": "leg_id obbligatorio per la creazione"}), 400
            
            stage_meta['leg_id'] = payload.leg_id
            stage_res = supabase.table('stage').insert(stage_meta).execute()
            stage_id = stage_res.data[0]['id']
            next_version = 1

        if payload.results:
            insert_data = []
            for r in payload.results:
                t_ms = time_str_to_ms(r.time)
                p_sec = 0.0
                if r.penalty and r.penalty != '-':
                    p_str = r.penalty.replace('+', '').replace('s', '').strip()
                    try:
                        if ':' in p_str:
                            p_parts = p_str.split(':')
                            p_sec = float(p_parts[0])*60 + float(p_parts[1])
                        else:
                            p_sec = float(p_str)
                    except:
                        pass
                
                # --- DSQ AUTOMATICO ---
                is_dsq = r.is_disqualified
                if allowed_classes and r.car_class_id:
                    # Se il campionato ha classi ammesse e il pilota ha una classe assegnata
                    if r.car_class_id not in allowed_classes:
                        is_dsq = True # Squalifica se la classe non è ammessa
                
                insert_data.append({
                    "stage_id": stage_id,
                    "position": r.position,
                    "driver_name": r.driver or "Sconosciuto",
                    "car_model": r.car or "Sconosciuta",
                    "time_str": r.time or "00:00.000",
                    "penalty": r.penalty or "-",
                    "gap": r.gap or "-",
                    "car_id": r.car_id,
                    "car_class_id": r.car_class_id,
                    "is_disqualified": is_dsq, # Applicato qui
                    "time_ms": t_ms,
                    "penalty_sec": p_sec,
                    "status": "dsq" if is_dsq else "completed", # Se DSQ, cambia lo status
                    "is_official": False,
                    "version": next_version
                })
            supabase.table('stage_result').insert(insert_data).execute()

        return jsonify({"success": True, "stage_id": stage_id, "saved": len(payload.results)})
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/publish-stage/<int:stage_id>', methods=['POST'])
@admin_required
def publish_stage(stage_id):
    """Promuove le bozze correnti a ufficiali, sostituendo eventuali ufficiali precedenti."""
    try:
        supabase = get_supabase()

        drafts = (
            supabase.table('stage_result').select('id')
            .eq('stage_id', stage_id)
            .eq('is_official', False)
            .execute()
        )
        if not drafts.data:
            return jsonify({"success": False, "error": "Nessuna bozza da pubblicare per questo stage"}), 400

        # Rimuove la versione ufficiale precedente
        supabase.table('stage_result').delete() \
            .eq('stage_id', stage_id).eq('is_official', True).execute()

        # Promuove le bozze
        supabase.table('stage_result').update({"is_official": True}) \
            .eq('stage_id', stage_id).eq('is_official', False).execute()

        return jsonify({"success": True, "published": len(drafts.data)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

# =============================================================================
# DISCORD — COSTRUZIONE CONTENUTI (C1: Compatti + Webhook Globale)
# =============================================================================
# Recupera il webhook globale dalle variabili d'ambiente di Render
GLOBAL_DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

def _get_rally_for_entity(entity_type, entity_id):
    """Risale al rally a partire da stage/leg/rally/championship.
    Ritorna (rally, leg, stage) con i campi utili, o None in caso di errore."""
    supabase = get_supabase()

    if entity_type == 'championship':
        return {"rally": None, "leg": None, "stage": None, "championship_id": entity_id}

    if entity_type == 'rally':
        rally = supabase.table('rally').select('*').eq('id', entity_id).execute().data
        if not rally:
            return None
        return {"rally": rally[0], "leg": None, "stage": None}

    if entity_type == 'leg':
        leg = supabase.table('leg').select('*').eq('id', entity_id).execute().data
        if not leg:
            return None
        rally = supabase.table('rally').select('*').eq('id', leg[0]['rally_id']).execute().data
        return {"rally": rally[0] if rally else None, "leg": leg[0], "stage": None}

    if entity_type == 'stage':
        stage = supabase.table('stage').select('*').eq('id', entity_id).execute().data
        if not stage:
            return None
        leg = supabase.table('leg').select('*').eq('id', stage[0]['leg_id']).execute().data
        rally = supabase.table('rally').select('*').eq('id', leg[0]['rally_id']).execute().data if leg else []
        return {"rally": rally[0] if rally else None, "leg": leg[0] if leg else None, "stage": stage[0]}

    return None


def _format_ms(ms):
    if not ms:
        return "-"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def _embed_stage(ctx, frontend_base="https://acr-championship.pages.dev"):
    supabase = get_supabase()
    stage = ctx['stage']
    leg = ctx['leg']
    rally = ctx['rally']

    results = (
        supabase.table('stage_result').select('*')
        .eq('stage_id', stage['id']).eq('is_official', True)
        .order('position').limit(3).execute().data # C1: Solo Top 3
    ) or []

    if not results:
        return None

    lines = []
    for r in results:
        t = r.get('time_str') or _format_ms(r.get('time_ms'))
        pen = r.get('penalty') or ''
        pen_str = f" ({pen})" if pen and pen != '-' else ''
        medal = ["🥇", "🥈", "🥉"][r['position']-1]
        lines.append(f"{medal} **{r['driver_name']}** — `{t}`{pen_str}")

    title_parts = [p for p in [rally['name'] if rally else None, leg['name'] if leg else None, stage['name']] if p]
    title = "🏁 " + " — ".join(title_parts)
    url = f"{frontend_base}/championship-detail.html?id={rally['championship_id']}" if rally else frontend_base

    return {
        "title": title[:256],
        "description": "**Podio Stage:**\n" + "\n".join(lines) + f"\n\n[👉 Vedi classifica completa]({url})",
        "color": 0xEAB308,
        "url": url,
        "footer": {"text": f"ACR Championship • {stage.get('track_name') or 'Stage'}"}
    }


def _compute_standings_for_stages(stage_ids):
    supabase = get_supabase()
    if not stage_ids: return []

    # 1. Prendi tutti i risultati ufficiali per queste stage
    results = (
        supabase.table('stage_result').select('*')
        .in_('stage_id', stage_ids).eq('is_official', True)
        .execute().data
    ) or []

    if not results: return []

    # 2. QUALE STAGE SONO STATE EFFETTIVAMENTE CARICATE?
    # Creiamo un set con gli ID delle stage che hanno almeno un risultato
    stages_with_results = set(r['stage_id'] for r in results)

    # 3. Trova il tempo massimo per ogni stage (per DNF)
    stage_max = {}
    for r in results:
        if r['status'] == 'completed' and r['time_ms']:
            sid = r['stage_id']
            stage_max[sid] = max(stage_max.get(sid, 0), r['time_ms'])

    # 4. Trova tutti i piloti
    all_drivers = set(r['driver_name'] for r in results)
    
    # Raggruppa i risultati per pilota
    driver_results = {}
    for r in results:
        drv = r['driver_name']
        if drv not in driver_results: driver_results[drv] = {}
        driver_results[drv][r['stage_id']] = r

    totals = {}
    for drv in all_drivers:
        totals[drv] = {'time_ms': 0, 'status': 'completed', 'car': ''}
        
        # CICLA SOLO SULLE STAGE CHE HANNO RISULTATI (Ignora quelle vuote!)
        for sid in stages_with_results:
            r = driver_results[drv].get(sid)
            
            if r:
                if r.get('car_model'): totals[drv]['car'] = r.get('car_model') 
                
                if r['status'] in ('dnf', 'dns', 'dsq'):
                    totals[drv]['status'] = r['status']
                    last = stage_max.get(sid, 300000)
                    totals[drv]['time_ms'] += last * 2
                elif r['status'] == 'completed':
                    totals[drv]['time_ms'] += (r['time_ms'] or 0)
            else:
                # Il pilota NON ha un risultato per questa stage (ma la stage ha risultati per altri) -> DNF
                last = stage_max.get(sid, 300000)
                totals[drv]['time_ms'] += last * 2
                totals[drv]['status'] = 'dnf'

    standings = [{'driver': k, 'total_ms': v['time_ms'], 'car': v['car'], 'status': v['status']} for k, v in totals.items()]
    standings.sort(key=lambda x: x['total_ms'])

    prev, pos = -1, 1
    for i, item in enumerate(standings):
        if item['total_ms'] == prev: item['position'] = pos
        else:
            item['position'] = i + 1
            pos = item['position']
        prev = item['total_ms']
        item['total_str'] = _format_ms(item['total_ms'])

    return standings

def _compute_standings_for_leg(leg_id):
    supabase = get_supabase()
    stages = supabase.table('stage').select('id').eq('leg_id', leg_id).execute().data or []
    return _compute_standings_for_stages([s['id'] for s in stages])


def _compute_points_for_rally(rally_id, points_system=None):
    if points_system is None:
        points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

    supabase = get_supabase()
    legs = supabase.table('leg').select('id').eq('rally_id', rally_id).execute().data or []
    stage_ids = []
    for leg in legs:
        stages = supabase.table('stage').select('id').eq('leg_id', leg['id']).execute().data or []
        stage_ids.extend([s['id'] for s in stages])

    standings = _compute_standings_for_stages(stage_ids)
    for s in standings:
        s['points'] = points_system[s['position'] - 1] if s['position'] <= len(points_system) else 0
    return standings


def _compute_points_for_championship(champ_id, points_system=None):
    """Somma i punti rally di tutti i rally del campionato, per pilota."""
    if points_system is None:
        points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

    supabase = get_supabase()
    rallies = supabase.table('rally').select('id').eq('championship_id', champ_id).execute().data or []

    driver_points = {}
    for rally in rallies:
        rally_standings = _compute_points_for_rally(rally['id'], points_system)
        for s in rally_standings:
            drv = s['driver']
            if drv not in driver_points:
                driver_points[drv] = {'points': 0, 'car': s.get('car', ''), 'races': 0}
            driver_points[drv]['points'] += s['points']
            driver_points[drv]['races'] += 1

    out = [{'driver': k, 'points': v['points'], 'car': v['car'], 'races': v['races']} for k, v in driver_points.items()]
    out.sort(key=lambda x: (-x['points'], x['driver']))
    for i, item in enumerate(out):
        item['position'] = i + 1
    return out


def _embed_leg(ctx, frontend_base="https://acr-championship.pages.dev"):
    standings = _compute_standings_for_leg(ctx['leg']['id'])
    if not standings:
        return None

    lines = []
    for s in standings[:3]: # C1: Solo Top 3
        medal = ["🥇", "🥈", "🥉"][s['position']-1]
        badge = f" `{s['status'].upper()}`" if s['status'] != 'completed' else ''
        lines.append(f"{medal} **{s['driver']}** — `{s['total_str']}`{badge}")

    rally = ctx['rally']
    leg = ctx['leg']
    title = "⏱️ Classifica Leg — " + (f"{rally['name']} — " if rally else "") + leg['name']
    url = f"{frontend_base}/championship-detail.html?id={rally['championship_id']}" if rally else frontend_base

    return {
        "title": title[:256],
        "description": "**Podio Leg:**\n" + "\n".join(lines) + f"\n\n[👉 Vedi classifica completa]({url})",
        "color": 0x3B82F6,
        "url": url,
        "footer": {"text": "ACR Championship • Aggiornato"}
    }


def _embed_rally(ctx, frontend_base="https://acr-championship.pages.dev"):
    standings = _compute_points_for_rally(ctx['rally']['id'])
    if not standings:
        return None

    lines = []
    for s in standings[:3]: # C1: Solo Top 3
        medal = ["🥇", "🥈", "🥉"][s['position']-1]
        lines.append(f"{medal} **{s['driver']}** — `{s['points']} pt`")

    rally = ctx['rally']
    title = "🏆 Classifica Rally — " + rally['name']
    url = f"{frontend_base}/championship-detail.html?id={rally['championship_id']}"

    return {
        "title": title[:256],
        "description": "**Podio Rally:**\n" + "\n".join(lines) + f"\n\n[👉 Vedi classifica completa]({url})",
        "color": 0x22C55E,
        "url": url,
        "footer": {"text": "ACR Championship • Aggiornato"}
    }


def _embed_championship(ctx, frontend_base="https://acr-championship.pages.dev"):
    champ_id = ctx['championship_id']
    supabase = get_supabase()
    champ = supabase.table('championship').select('name').eq('id', champ_id).execute().data
    champ_name = champ[0]['name'] if champ else f"Campionato #{champ_id}"

    standings = _compute_points_for_championship(champ_id)
    if not standings:
        return None

    lines = []
    for s in standings[:3]: # C1: Solo Top 3
        medal = ["🥇", "🥈", "🥉"][s['position']-1]
        lines.append(f"{medal} **{s['driver']}** — `{s['points']} pt` ({s['races']} rally)")

    return {
        "title": f"👑 Classifica Campionato — {champ_name}"[:256],
        "description": "**Podio Assoluto:**\n" + "\n".join(lines) + f"\n\n[👉 Vedi classifica completa]({frontend_base}/championship-detail.html?id={champ_id})",
        "color": 0xF59E0B,
        "url": f"{frontend_base}/championship-detail.html?id={champ_id}",
        "footer": {"text": "ACR Championship • Aggiornato"}
    }

# =============================================================================
# DISCORD — ROTTA PUBBLICAZIONE (Aggiornata per Webhook Globale)
# =============================================================================
@app.route('/api/discord/status/<entity_type>/<int:entity_id>', methods=['GET'])
@admin_required
def discord_status(entity_type, entity_id):
    if entity_type not in ('stage', 'leg', 'rally', 'championship'):
        return jsonify({"success": False, "error": "entity_type non valido"}), 400

    webhook_url = (request.args.get('webhook_url') or '').strip() or GLOBAL_DISCORD_WEBHOOK

    ctx = _get_rally_for_entity(entity_type, entity_id)
    if ctx is None:
        return jsonify({"success": False, "error": "Entità non trovata"}), 404

    rally_webhook = (ctx.get('rally') or {}).get('discord_webhook_url') or ''
    if not webhook_url:
        webhook_url = rally_webhook

    webhook_id = _parse_webhook_id(webhook_url)
    if not webhook_id:
        return jsonify({
            "success": True,
            "published": False,
            "reason": "no_webhook"
        })

    supabase = get_supabase()
    rows = (
        supabase.table('discord_publish_log')
        .select('message_id, updated_at')
        .eq('entity_type', entity_type)
        .eq('entity_id', entity_id)
        .eq('webhook_id', webhook_id)
        .execute().data
    )

    if rows:
        return jsonify({
            "success": True,
            "published": True,
            "message_id": rows[0]['message_id'],
            "last_update": rows[0].get('updated_at')
        })

    return jsonify({
        "success": True,
        "published": False
    })

@app.route('/api/discord/publish/<entity_type>/<int:entity_id>', methods=['POST'])
@admin_required
def discord_publish(entity_type, entity_id):
    if entity_type not in ('stage', 'leg', 'rally', 'championship'):
        return jsonify({"success": False, "error": "entity_type non valido"}), 400

    body = request.json or {}
    # C1: Usa il webhook passato dal frontend, altrimenti usa quello GLOBALE
    webhook_url = (body.get('webhook_url') or '').strip() or GLOBAL_DISCORD_WEBHOOK
    thread_id = (body.get('thread_id') or '').strip() or None

    ctx = _get_rally_for_entity(entity_type, entity_id)
    if ctx is None:
        return jsonify({"success": False, "error": "Entità non trovata"}), 404

    # Fallback ulteriore: se non c'è il globale, cerca quello del rally (retrocompatibilità)
    if not webhook_url and ctx.get('rally'):
        webhook_url = (ctx['rally'].get('discord_webhook_url') or '').strip()

    if not webhook_url:
        return jsonify({
            "success": False,
            "error": "Nessun webhook configurato (manca la variabile GLOBAL_DISCORD_WEBHOOK o il webhook del rally)"
        }), 400

    # Costruzione embed
    try:
        if entity_type == 'stage':
            embed = _embed_stage(ctx)
        elif entity_type == 'leg':
            embed = _embed_leg(ctx)
        elif entity_type == 'rally':
            embed = _embed_rally(ctx)
        else:
            embed = _embed_championship(ctx)
    except Exception as e:
        return jsonify({"success": False, "error": f"Errore costruzione embed: {e}"}), 500

    if embed is None:
        return jsonify({"success": False, "error": "Nessun dato ufficiale da pubblicare"}), 400

    result = _upsert_discord_message(entity_type, entity_id, webhook_url, embed, thread_id)
    if not result['ok']:
        return jsonify({"success": False, "error": result.get('error')}), 502

    return jsonify({
        "success": True,
        "action": result.get('action'),
        "message_id": result.get('message_id')
    })

# =============================================================================
# ROTTE PER LE TENDINE (TRACKS, CARS, CLASSI)
# =============================================================================
@app.route('/api/tracks', methods=['GET'])
def get_tracks():
    try:
        supabase = get_supabase()
        res = supabase.table('track').select('*').order('name').execute()
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/car-classes', methods=['GET'])
def get_car_classes():
    try:
        supabase = get_supabase()
        res = supabase.table('car_class').select('*').order('name').execute()
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/cars', methods=['GET'])
def get_cars():
    try:
        supabase = get_supabase()
        res = supabase.table('car').select('*, car_class(name)').order('brand').execute()
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# =============================================================================
# ROTTE ELIMINAZIONE (LEG E STAGE)
# =============================================================================
@app.route('/api/delete-leg/<int:leg_id>', methods=['DELETE'])
@admin_required
def delete_leg(leg_id):
    try:
        supabase = get_supabase()
        stages = supabase.table('stage').select('id').eq('leg_id', leg_id).execute().data
        stage_ids = [s['id'] for s in stages]
        if stage_ids:
            supabase.table('stage_result').delete().in_('stage_id', stage_ids).execute()
            supabase.table('stage').delete().eq('leg_id', leg_id).execute()
        supabase.table('leg').delete().eq('id', leg_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/delete-stage/<int:stage_id>', methods=['DELETE'])
@admin_required
def delete_stage(stage_id):
    try:
        supabase = get_supabase()
        supabase.table('stage_result').delete().eq('stage_id', stage_id).execute()
        supabase.table('stage').delete().eq('id', stage_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/championships', methods=['GET'])
def get_championships():
    try:
        supabase = get_supabase()
        
        # 1. Controlliamo se chi fa la richiesta è un admin
        auth_header = request.headers.get('Authorization')
        is_admin = False
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                user = supabase.auth.get_user(token)
                if user and user.user.app_metadata.get('role') == 'admin':
                    is_admin = True
            except:
                pass # Se il token è scaduto, lo trattiamo come anonimo
        
        # 2. Prepariamo la query
        query = supabase.table('championship').select('*')
        
        # 3. Filtriamo SOLO se non è admin
        if not is_admin:
            query = query.eq('is_visible', True)
            
        res = query.order('created_at', desc=True).execute()
        return jsonify({"success": True, "data": res.data}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/calendar', methods=['GET'])
def get_calendar():
    supabase = get_supabase()
    rallys = supabase.table('rally').select('id, name, created_at').order('created_at', desc=True).execute()
    return jsonify(rallys.data)

@app.route('/api/standings/championship/<int:champ_id>', methods=['GET'])
def get_standings_championship(champ_id):
    try:
        supabase = get_supabase()
        champ = supabase.table('championship').select('*').eq('id', champ_id).execute()
        if not champ.data:
            return jsonify({"success": False, "error": "Campionato non trovato"}), 404

        points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

        # 1. Trova tutti i rally del campionato
        rallies = supabase.table('rally').select('id').eq('championship_id', champ_id).execute().data or []
        rally_ids = [r['id'] for r in rallies]

        # 2. Trova tutte le leg di questi rally
        legs = supabase.table('leg').select('id', 'rally_id').in_('rally_id', rally_ids).execute().data or []
        leg_ids = [l['id'] for l in legs]
        leg_to_rally = {l['id']: l['rally_id'] for l in legs}

        # 3. Trova tutte le stage di queste leg
        stages = supabase.table('stage').select('id', 'leg_id').in_('leg_id', leg_ids).execute().data or []
        stage_ids = [s['id'] for s in stages]
        stage_to_leg = {s['id']: s['leg_id'] for s in stages}

        # 4. Trova tutti i risultati ufficiali per queste stage
        results = (
            supabase.table('stage_result').select('*')
            .in_('stage_id', stage_ids).eq('is_official', True)
            .execute().data
        ) or []

        # Raggruppa i risultati per rally
        results_by_rally = {}
        for r in results:
            sid = r['stage_id']
            lid = stage_to_leg.get(sid)
            rid = leg_to_rally.get(lid)
            if rid:
                if rid not in results_by_rally:
                    results_by_rally[rid] = []
                results_by_rally[rid].append(r)

        driver_points = {}

        # Calcola i punti per ogni rally
        for rid, rally_results in results_by_rally.items():
            # Applica la regola DNF e somma i tempi
            stage_max = {}
            for r in rally_results:
                if r['status'] == 'completed' and r['time_ms']:
                    sid = r['stage_id']
                    stage_max[sid] = max(stage_max.get(sid, 0), r['time_ms'])

            totals = {}
            for r in rally_results:
                drv = r['driver_name']
                if drv not in totals:
                    totals[drv] = {'time_ms': 0, 'status': 'completed', 'car': r.get('car_model', '')}
                
                if r['status'] in ('dnf', 'dns', 'dsq'):
                    totals[drv]['status'] = r['status']
                    last = stage_max.get(r['stage_id'], 300000)
                    totals[drv]['time_ms'] += last * 2
                elif r['status'] == 'completed':
                    totals[drv]['time_ms'] += (r['time_ms'] or 0)

            # Ordina e assegna i punti per questo rally
            standings = [{'driver': k, 'total_ms': v['time_ms'], 'status': v['status'], 'car': v['car']} for k, v in totals.items()]
            standings.sort(key=lambda x: x['total_ms'])
            for i, item in enumerate(standings):
                item['position'] = i + 1

            # Accumula i punti nel totale del campionato
            for s in standings:
                drv = s['driver']
                if drv not in driver_points:
                    driver_points[drv] = {'points': 0, 'car': s.get('car', ''), 'races': 0}
                
                pts = points_system[s['position'] - 1] if s['position'] <= len(points_system) else 0
                driver_points[drv]['points'] += pts
                driver_points[drv]['races'] += 1

        # Prepara la classifica finale
        out = [{'driver': k, 'points': v['points'], 'car': v['car'], 'races': v['races']} for k, v in driver_points.items()]
        out.sort(key=lambda x: (-x['points'], x['driver']))
        for i, item in enumerate(out):
            item['position'] = i + 1

        return jsonify({
            "success": True,
            "championship": champ.data[0],
            "data": out
        }), 200
        
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/standings/<type>/<int:id>', methods=['GET'])
def get_standings(type, id):
    supabase = get_supabase()
    points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    
    try:
        # Funzione di supporto per calcolare i tempi (con regola DNF e partecipazione)
        def calculate_times(stage_ids):
            results = (
                        supabase.table('stage_result').select('*')
                        .in_('stage_id', stage_ids)
                        .eq('is_official', True)
                        .execute().data
                    )
            if not results: return []
            
            # Filtra solo le stage che hanno risultati
            stages_with_results = set(r['stage_id'] for r in results)
            
            stage_max_times = {}
            for r in results:
                if r['status'] == 'completed' and r['time_ms']:
                    sid = r['stage_id']
                    stage_max_times[sid] = max(stage_max_times.get(sid, 0), r['time_ms'])
            
            all_drivers = set(r['driver_name'] for r in results)
            driver_results = {}
            for r in results:
                drv = r['driver_name']
                if drv not in driver_results: driver_results[drv] = {}
                driver_results[drv][r['stage_id']] = r

            driver_totals = {}
            for drv in all_drivers:
                driver_totals[drv] = {'time_ms': 0, 'status': 'completed', 'car': ''}
                
                # CICLA SOLO SULLE STAGE VALIDE
                for sid in stages_with_results:
                    r = driver_results[drv].get(sid)
                    
                    if r:
                        if r.get('car_model'): driver_totals[drv]['car'] = r.get('car_model')
                        
                        if r['status'] in ['dnf', 'dns', 'dsq']:
                            driver_totals[drv]['status'] = r['status']
                            last_time = stage_max_times.get(sid, 300000)
                            driver_totals[drv]['time_ms'] += (last_time * 2)
                        elif r['status'] == 'completed':
                            driver_totals[drv]['time_ms'] += (r['time_ms'] or 0)
                    else:
                        last_time = stage_max_times.get(sid, 300000)
                        driver_totals[drv]['time_ms'] += (last_time * 2)
                        driver_totals[drv]['status'] = 'dnf'
            
            standings = [{'driver': k, 'total_ms': v['time_ms'], 'car': v['car'], 'status': v['status']} for k, v in driver_totals.items()]
            standings.sort(key=lambda x: x['total_ms'])
            
            prev_time = -1
            current_pos = 1
            for i, item in enumerate(standings):
                if item['total_ms'] == prev_time: item['position'] = current_pos
                else:
                    item['position'] = i + 1
                    current_pos = item['position']
                prev_time = item['total_ms']
                item['total_str'] = ms_to_time_str(item['total_ms'])
            
            return standings
        
        if type == 'stage':
            # Logica per singolo stage
            res = (
                    supabase.table('stage_result').select('*')
                    .eq('stage_id', id)
                    .eq('is_official', True)
                    .order('position')
                    .execute()
                )
            return jsonify({"success": True, "data": res.data}), 200

        elif type == 'leg':
            # Logica per Leg: Somma tempi, senza punti
            stages = supabase.table('stage').select('id').eq('leg_id', id).execute().data
            stage_ids = [s['id'] for s in stages]
            if not stage_ids: return jsonify({"success": True, "data": []}), 200
            
            standings = calculate_times(stage_ids)
            # Rimuoviamo i punti se presenti (non richiesti per le leg)
            return jsonify({"success": True, "data": standings}), 200

        elif type == 'rally':
            # Logica per Rally: Calcola i tempi, assegna i punti, ma il frontend mostrerà solo i punti
            legs = supabase.table('leg').select('id').eq('rally_id', id).execute().data
            leg_ids = [l['id'] for l in legs]
            stages = supabase.table('stage').select('id').in_('leg_id', leg_ids).execute().data
            stage_ids = [s['id'] for s in stages]
            if not stage_ids: return jsonify({"success": True, "data": []}), 200
            
            standings = calculate_times(stage_ids)
            
            # Assegna i punti in base alla posizione
            for s in standings:
                s['points'] = points_system[s['position']-1] if s['position'] <= len(points_system) else 0
                
            return jsonify({"success": True, "data": standings}), 200
            
        return jsonify({"success": False, "error": "Tipo classifica non valido"}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

# =============================================================================
# MATRICI PUNTEGGI (Punti Rally by Rally / Punti Leg by Leg)
# =============================================================================
@app.route('/api/standings/matrix/championship/<int:champ_id>', methods=['GET'])
def get_matrix_championship(champ_id):
    try:
        supabase = get_supabase()
        # Recuperiamo i rally del campionato
        rallies = supabase.table('rally').select('id, name').eq('championship_id', champ_id).order('id').execute().data or []
        if not rallies:
            return jsonify({"success": True, "rallies": [], "drivers": []}), 200

        # Calcoliamo i punti per ogni rally
        matrix_data = {}
        points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

        for r in rallies:
            # Usiamo la funzione esistente per calcolare i punti di QUEL rally
            rally_standings = _compute_points_for_rally(r['id'], points_system)
            for s in rally_standings:
                drv = s['driver']
                if drv not in matrix_data:
                    matrix_data[drv] = {'driver': drv, 'car': s.get('car', ''), 'rallies': {}, 'total': 0}
                
                matrix_data[drv]['rallies'][str(r['id'])] = s['points']
                matrix_data[drv]['total'] += s['points']

        # Ordiniamo i piloti per punteggio totale decrescente
        drivers_list = list(matrix_data.values())
        drivers_list.sort(key=lambda x: (-x['total'], x['driver']))

        return jsonify({
            "success": True,
            "rallies": rallies, 
            "drivers": drivers_list
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/standings/matrix/rally/<int:rally_id>', methods=['GET'])
def get_matrix_rally(rally_id):
    try:
        supabase = get_supabase()
        # Recuperiamo le leg del rally
        legs = supabase.table('leg').select('id, name').eq('rally_id', rally_id).order('id').execute().data or []
        if not legs:
            return jsonify({"success": True, "legs": [], "drivers": []}), 200

        matrix_data = {}
        points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

        for l in legs:
            # Usiamo la funzione che calcola i tempi della singola leg
            leg_standings = _compute_standings_for_leg(l['id'])
            for s in leg_standings:
                drv = s['driver']
                if drv not in matrix_data:
                    matrix_data[drv] = {'driver': drv, 'car': s.get('car', ''), 'legs': {}, 'total': 0}
                
                # Assegniamo i punti in base alla posizione nella leg
                pts = points_system[s['position'] - 1] if s['position'] <= len(points_system) else 0
                matrix_data[drv]['legs'][str(l['id'])] = pts
                matrix_data[drv]['total'] += pts

        drivers_list = list(matrix_data.values())
        drivers_list.sort(key=lambda x: (-x['total'], x['driver']))

        return jsonify({
            "success": True,
            "legs": legs, 
            "drivers": drivers_list
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

@app.route('/api/toggle-championship/<int:champ_id>', methods=['PUT'])
@admin_required
def toggle_championship(champ_id):
    try:
        supabase = get_supabase()
        current = supabase.table('championship').select('is_visible').eq('id', champ_id).execute()
        if not current.data: return jsonify({"success": False, "error": "Non trovato"}), 404
        
        new_status = not current.data[0].get('is_visible', True)
        supabase.table('championship').update({"is_visible": new_status}).eq('id', champ_id).execute()
        return jsonify({"success": True, "is_visible": new_status}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/update-championship/<int:champ_id>', methods=['PUT'])
@admin_required
def update_championship(champ_id):
    try:
        data = request.json
        supabase = get_supabase()
        supabase.table('championship').update({"name": data['name']}).eq('id', champ_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/update-rally/<int:rally_id>', methods=['PUT'])
@admin_required
def update_rally(rally_id):
    try:
        data = request.json
        supabase = get_supabase()
        supabase.table('rally').update({"name": data['name']}).eq('id', rally_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/update-leg/<int:leg_id>', methods=['PUT'])
@admin_required
def update_leg(leg_id):
    try:
        data = request.json
        supabase = get_supabase()
        supabase.table('leg').update({"name": data['name']}).eq('id', leg_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500