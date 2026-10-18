from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from services.ocr_service import extract_text_from_image
from utils.db import get_supabase

app = Flask(__name__)
CORS(app)

@app.route('/')
def health_check():
    return jsonify({"status": "online"})

@app.route('/api/upload', methods=['POST'])
def upload_image():
    if 'screenshot' not in request.files:
        return jsonify({"success": False, "error": "Nessun file caricato"}), 400
        
    file = request.files['screenshot']
    if file.filename == '':
        return jsonify({"success": False, "error": "Nome file vuoto"}), 400

    try:
        image_bytes = file.read()
        raw_text = extract_text_from_image(image_bytes)
        return jsonify({
            "success": True,
            "raw_text": raw_text
        })
    except Exception as e:
        # Cattura l'errore dell'OCR e lo restituisce al frontend
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500