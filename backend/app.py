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
        r = requests.post(url, params=params, json=payload, timeout=10)
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
        r = requests.patch(url, params=params or None, json=payload, timeout=10)
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

# =============================================================================
# BLOCCO 1: LEG & STAGE
# =============================================================================

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

        res = supabase.table('leg').insert({
            "rally_id": rally_id,
            "name": name
        }).execute()
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
        track_name = (data.get('track_name') or '').strip() or None
        if not leg_id or not name:
            return jsonify({"success": False, "error": "leg_id e name obbligatori"}), 400

        supabase = get_supabase()
        leg = supabase.table('leg').select('id').eq('id', leg_id).execute()
        if not leg.data:
            return jsonify({"success": False, "error": "Leg non trovato"}), 404

        res = supabase.table('stage').insert({
            "leg_id": leg_id,
            "name": name,
            "track_name": track_name or name
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

        stages_by_leg = {}
        for s in stages:
            s['has_official_results'] = s['id'] in stages_with_official
            s['has_draft_results']    = s['id'] in stages_with_draft
            s['has_results']          = s['id'] in stages_with_official  # retro-compat
            stages_by_leg.setdefault(s['leg_id'], []).append(s)
            
        legs_by_rally = {}
        for l in legs:
            l['stages'] = stages_by_leg.get(l['id'], [])
            legs_by_rally.setdefault(l['rally_id'], []).append(l)
            
        for r in rallies:
            r['legs'] = legs_by_rally.get(r['id'], [])

        return jsonify({"success": True, "data": {"championship": champ.data[0], "rallies": rallies}}), 200
    except Exception as e:
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
    track_name: Optional[str] = None
    leg_id: Optional[int] = None
    results: List[StageResultIn] = []

@app.route('/api/save-stage', methods=['POST'])
@admin_required
def save_stage():
    try:
        # 1. Validazione
        try:
            payload = SaveStageIn(**request.json)
        except ValidationError as e:
            return jsonify({"success": False, "error": e.errors()}), 400

        supabase = get_supabase()

        # 2. Gestione Stage (Update o Insert)
        if payload.stage_id:
            existing = supabase.table('stage').select('id').eq('id', payload.stage_id).execute()
            if not existing.data:
                return jsonify({"success": False, "error": "Stage non trovato"}), 404
            stage_id = payload.stage_id
            
            if payload.stage_name and payload.stage_name != "Update":
                supabase.table('stage').update({
                    "name": payload.stage_name,
                    "track_name": payload.track_name or 'Sconosciuto' # Evita NULL
                }).eq('id', stage_id).execute()

            # Prossima version = max esistente + 1 (contando sia ufficiali che bozze correnti)
            ver_res = (
                    supabase.table('stage_result').select('version')
                    .eq('stage_id', stage_id)
                    .order('version', desc=True)
                    .limit(1)
                    .execute()
                    )
            next_version = (ver_res.data[0]['version'] + 1) if ver_res.data else 1
                
            supabase.table('stage_result').delete().eq('stage_id', stage_id).eq('is_official', False).execute()
        else:
            if not payload.leg_id:
                return jsonify({"success": False, "error": "leg_id obbligatorio per la creazione"}), 400
            stage_res = supabase.table('stage').insert({
                "leg_id": payload.leg_id,
                "name": payload.stage_name,
                "track_name": payload.track_name or 'Sconosciuto'
            }).execute()
            stage_id = stage_res.data[0]['id']
            next_version = 1

        # 3. Inserimento Risultati
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
                
                # Pulizia dati per evitare errori di validazione del DB
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
                    "is_disqualified": r.is_disqualified,
                    "time_ms": t_ms,
                    "penalty_sec": p_sec,
                    "status": "completed",
                    "is_official": False,
                    "version": next_version
                })
            supabase.table('stage_result').insert(insert_data).execute()

        return jsonify({"success": True, "stage_id": stage_id, "saved": len(payload.results)})
        
    except Exception as e:
        import traceback
        print("ERRORE SALVATAGGIO STAGE:")
        print(traceback.format_exc()) # Questo lo vedrai nei log di Render
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
# DISCORD — COSTRUZIONE CONTENUTI
# =============================================================================
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
        .order('position').execute().data
    ) or []

    if not results:
        return None  # niente da pubblicare

    lines = []
    for r in results[:15]:  # cap a 15 per non sforare il limite embed (4096 char)
        t = r.get('time_str') or _format_ms(r.get('time_ms'))
        pen = r.get('penalty') or ''
        pen_str = f" ({pen})" if pen and pen != '-' else ''
        lines.append(f"**{r['position']}.** {r['driver_name']} — `{t}`{pen_str}")

    if len(results) > 15:
        lines.append(f"... e altri {len(results) - 15} piloti")

    title_parts = [p for p in [rally['name'] if rally else None, leg['name'] if leg else None, stage['name']] if p]
    title = "🏁 " + " — ".join(title_parts)

    url = f"{frontend_base}/championship-detail.html?id={rally['championship_id']}" if rally else frontend_base

    return {
        "title": title[:256],
        "description": "\n".join(lines)[:4000],
        "color": 0xEAB308,
        "url": url,
        "footer": {"text": f"ACR Championship — {stage.get('track_name') or 'Stage'}"}
    }


def _compute_standings_for_stages(stage_ids):
    """Restituisce la classifica (somma tempi, regola DNF) come lista ordinata.
    Riusa la stessa logica di /api/standings ma filtrata su is_official=True."""
    supabase = get_supabase()
    if not stage_ids:
        return []

    results = (
        supabase.table('stage_result').select('*')
        .in_('stage_id', stage_ids).eq('is_official', True)
        .execute().data
    ) or []

    if not results:
        return []

    stage_max = {}
    for r in results:
        if r['status'] == 'completed' and r['time_ms']:
            sid = r['stage_id']
            stage_max[sid] = max(stage_max.get(sid, 0), r['time_ms'])

    totals = {}
    for r in results:
        drv = r['driver_name']
        if drv not in totals:
            totals[drv] = {'time_ms': 0, 'status': 'completed', 'car': r.get('car_model', '')}
        if r['status'] in ('dnf', 'dns', 'dsq'):
            totals[drv]['status'] = r['status']
            last = stage_max.get(r['stage_id'], 300000)
            totals[drv]['time_ms'] += last * 2
        elif r['status'] == 'completed':
            totals[drv]['time_ms'] += (r['time_ms'] or 0)

    standings = [{'driver': k, 'total_ms': v['time_ms'], 'car': v['car'], 'status': v['status']} for k, v in totals.items()]
    standings.sort(key=lambda x: x['total_ms'])

    prev, pos = -1, 1
    for i, item in enumerate(standings):
        if item['total_ms'] == prev:
            item['position'] = pos
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
    for s in standings[:15]:
        badge = ''
        if s['status'] != 'completed':
            badge = f" `{s['status'].upper()}`"
        lines.append(f"**{s['position']}.** {s['driver']} — `{s['total_str']}`{badge}")
    if len(standings) > 15:
        lines.append(f"... e altri {len(standings) - 15} piloti")

    rally = ctx['rally']
    leg = ctx['leg']
    title = "⏱️ Classifica Leg — " + (f"{rally['name']} — " if rally else "") + leg['name']
    url = f"{frontend_base}/championship-detail.html?id={rally['championship_id']}" if rally else frontend_base

    return {
        "title": title[:256],
        "description": "\n".join(lines)[:4000],
        "color": 0x3B82F6,
        "url": url,
        "footer": {"text": f"ACR Championship — aggiornato"}
    }


def _embed_rally(ctx, frontend_base="https://acr-championship.pages.dev"):
    standings = _compute_points_for_rally(ctx['rally']['id'])
    if not standings:
        return None

    lines = []
    for s in standings[:15]:
        lines.append(f"**{s['position']}.** {s['driver']} — `{s['points']} pt`")
    if len(standings) > 15:
        lines.append(f"... e altri {len(standings) - 15} piloti")

    rally = ctx['rally']
    title = "🏆 Classifica Rally — " + rally['name']
    url = f"{frontend_base}/championship-detail.html?id={rally['championship_id']}"

    return {
        "title": title[:256],
        "description": "\n".join(lines)[:4000],
        "color": 0x22C55E,
        "url": url,
        "footer": {"text": "ACR Championship — aggiornato"}
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
    for s in standings[:20]:
        lines.append(f"**{s['position']}.** {s['driver']} — `{s['points']} pt` ({s['races']} rally)")
    if len(standings) > 20:
        lines.append(f"... e altri {len(standings) - 20} piloti")

    return {
        "title": f"👑 Classifica Campionato — {champ_name}"[:256],
        "description": "\n".join(lines)[:4000],
        "color": 0xF59E0B,
        "url": f"{frontend_base}/championship-detail.html?id={champ_id}",
        "footer": {"text": "ACR Championship — aggiornato"}
    }

# =============================================================================
# DISCORD — ROTTA PUBBLICAZIONE
# =============================================================================
@app.route('/api/discord/publish/<entity_type>/<int:entity_id>', methods=['POST'])
@admin_required
def discord_publish(entity_type, entity_id):
    if entity_type not in ('stage', 'leg', 'rally', 'championship'):
        return jsonify({"success": False, "error": "entity_type non valido"}), 400

    body = request.json or {}
    webhook_url = (body.get('webhook_url') or '').strip()
    thread_id = (body.get('thread_id') or '').strip() or None

    ctx = _get_rally_for_entity(entity_type, entity_id)
    if ctx is None:
        return jsonify({"success": False, "error": "Entità non trovata"}), 404

    # Fallback webhook: se non passato, usa quello del rally
    if not webhook_url and ctx.get('rally'):
        webhook_url = (ctx['rally'].get('discord_webhook_url') or '').strip()

    if not webhook_url:
        return jsonify({
            "success": False,
            "error": "webhook_url mancante e nessun webhook configurato sul rally"
        }), 400

    # Costruzione embed
    try:
        if entity_type == 'stage':
            embed = _embed_stage(ctx)
        elif entity_type == 'leg':
            embed = _embed_leg(ctx)
        elif entity_type == 'rally':
            embed = _embed_rally(ctx)
        else:  # championship
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
    points_system = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    
    try:
        # Funzione di supporto per calcolare i tempi (con regola DNF)
        def calculate_times(stage_ids):
            results = (
                        supabase.table('stage_result').select('*')
                        .in_('stage_id', stage_ids)
                        .eq('is_official', True)
                        .execute().data
                    )
            if not results: return []
            
            driver_totals = {}
            # 1. Trova l'ultimo tempo per ogni stage (per regola DNF)
            stage_max_times = {}
            for r in results:
                if r['status'] == 'completed' and r['time_ms']:
                    sid = r['stage_id']
                    stage_max_times[sid] = max(stage_max_times.get(sid, 0), r['time_ms'])
            
            # 2. Somma i tempi applicando la regola DNF (ultimo tempo * 2)
            for r in results:
                drv = r['driver_name']
                if drv not in driver_totals:
                    driver_totals[drv] = {'time_ms': 0, 'status': 'completed', 'car': r.get('car_model', '')}
                
                if r['status'] in ['dnf', 'dns', 'dsq']:
                    driver_totals[drv]['status'] = r['status']
                    # Applica tempo fittizio: ultimo tempo dello stage * 2
                    last_time = stage_max_times.get(r['stage_id'], 300000) # default 5 min se vuoto
                    driver_totals[drv]['time_ms'] += (last_time * 2)
                elif r['status'] == 'completed':
                    driver_totals[drv]['time_ms'] += (r['time_ms'] or 0)
            
            # 3. Prepara la lista ordinata
            standings = [{'driver': k, 'total_ms': v['time_ms'], 'car': v['car'], 'status': v['status']} for k, v in driver_totals.items()]
            standings.sort(key=lambda x: x['total_ms'])
            
            # 4. Gestione Parità (stessa posizione per tempi uguali)
            prev_time = -1
            current_pos = 1
            for i, item in enumerate(standings):
                if item['total_ms'] == prev_time:
                    item['position'] = current_pos
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