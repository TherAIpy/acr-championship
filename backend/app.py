import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from utils.db import get_supabase
import easyocr
import cv2
import numpy as np

app = Flask(__name__)
CORS(app)

# Inizializzazione EasyOCR (lingua inglese per tempi e sigle)
reader = easyocr.Reader(['en'], gpu=False)

@app.route('/api/setup', methods=['GET'])
def get_setup():
    try:
        supabase = get_supabase()
        
        # Recupera campionati con rally, leg e stage
        champs = supabase.table('championship').select('*, rally(*, leg(*, stage(*)))').execute().data
        
        # Recupera classi auto e vetture
        classes = supabase.table('car_class').select('*, car(*)').execute().data
        
        return jsonify({
            "success": True,
            "championships": champs,
            "car_classes": classes
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/create-championship', methods=['POST'])
def create_championship():
    try:
        data = request.json
        name = data.get('name')
        season = data.get('season', '2026')
        cover_url = data.get('cover_url', '')
        
        if not name:
            return jsonify({"success": False, "error": "Il nome del campionato è obbligatorio"}), 400
            
        supabase = get_supabase()
        res = supabase.table('championship').insert({
            "name": name,
            "season": season,
            "cover_url": cover_url,
            "status": "non_iniziato"
        }).execute()
        
        return jsonify({"success": True, "championship": res.data[0]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/create-rally', methods=['POST'])
def create_rally():
    try:
        data = request.json
        name = data.get('name')
        championship_id = data.get('championship_id')
        
        if not name:
            return jsonify({"success": False, "error": "Il nome del rally è obbligatorio"}), 400
            
        supabase = get_supabase()
        res = supabase.table('rally').insert({
            "name": name,
            "championship_id": championship_id
        }).execute()
        
        return jsonify({"success": True, "rally": res.data[0]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/create-leg', methods=['POST'])
def create_leg():
    try:
        data = request.json
        name = data.get('name', 'Leg 1')
        rally_id = data.get('rally_id')
        
        if not rally_id:
            return jsonify({"success": False, "error": "ID Rally mancante"}), 400
            
        supabase = get_supabase()
        res = supabase.table('leg').insert({
            "name": name,
            "rally_id": rally_id
        }).execute()
        
        return jsonify({"success": True, "leg": res.data[0]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/create-stage', methods=['POST'])
def create_stage():
    try:
        data = request.json
        leg_id = data.get('leg_id')
        name = data.get('name', 'SS1')
        track_name = data.get('track_name', 'Tracciato')
        
        if not leg_id:
            return jsonify({"success": False, "error": "ID Leg mancante"}), 400
            
        supabase = get_supabase()
        res = supabase.table('stage').insert({
            "leg_id": leg_id,
            "name": name,
            "track_name": track_name
        }).execute()
        
        return jsonify({"success": True, "stage": res.data[0]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_screenshot():
    try:
        if 'screenshot' not in request.files:
            return jsonify({"success": False, "error": "Nessun file immagine trovato"}), 400
            
        file = request.files['screenshot']
        file_bytes = np.frombuffer(file.read(), np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        
        if img is None:
            return jsonify({"success": False, "error": "Impossibile leggere l'immagine"}), 400

        # OCR Extraction
        results_ocr = reader.readtext(img)
        parsed_data = []
        track_name = "Stage Rilevato"
        
        # Logica base di parsing OCR (estrae righe di testo rilevate)
        pos = 1
        for bbox, text, prob in results_ocr:
            text_clean = text.strip()
            # Filtro euristico per identificare tempi o piloti
            if ":" in text_clean or "." in text_clean:
                parsed_data.append({
                    "position": pos,
                    "driver": f"Pilota Test {pos}",
                    "car": "Skoda Fabia RS",
                    "time": text_clean,
                    "penalty": "-",
                    "gap": "-"
                })
                pos += 1

        return jsonify({
            "success": True,
            "track_name": track_name,
            "parsed_data": parsed_data
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/save-stage', methods=['POST'])
def save_stage():
    try:
        data = request.json
        leg_id = data.get('leg_id')
        stage_id = data.get('stage_id')
        stage_name = data.get('stage_name', 'SS1')
        track_name = data.get('track_name', 'Tracciato')
        results = data.get('results', [])
        
        supabase = get_supabase()
        
        # Se lo stage non esiste, crealo
        if not stage_id:
            s_res = supabase.table('stage').insert({
                "leg_id": leg_id,
                "name": stage_name,
                "track_name": track_name
            }).execute()
            stage_id = s_res.data[0]['id']
            
        # Salva i risultati dei piloti
        for r in results:
            supabase.table('stage_result').insert({
                "stage_id": stage_id,
                "position": r.get('position'),
                "driver_name": r.get('driver'),
                "car_name": r.get('car'),
                "time_str": r.get('time'),
                "penalty": r.get('penalty', '-'),
                "gap": r.get('gap', '-')
            }).execute()
            
        return jsonify({"success": True, "message": "Tempi salvati con successo nello stage!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/standings/<type>/<id>', methods=['GET'])
def get_standings(type, id):
    try:
        supabase = get_supabase()
        data = []
        
        if type == 'stage':
            res = supabase.table('stage_result').select('*').eq('stage_id', id).order('position').execute()
            data = res.data
        elif type == 'rally':
            # Raggruppa e somma i punteggi o tempi del rally
            res = supabase.table('stage_result').select('driver_name, time_str').execute()
            data = res.data # Logica semplificata di aggregazione
        else:
            res = supabase.table('stage_result').select('*').limit(20).execute()
            data = res.data
            
        return jsonify(data)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)