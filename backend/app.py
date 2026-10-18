from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from services.ocr_service import extract_text_from_image
from services.parser import parse_overall_text  # <-- Importiamo il parser

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
    
    try:
        image_bytes = file.read()
        raw_text = extract_text_from_image(image_bytes)
        
        # Passiamo il testo al parser
        parsed_data = parse_overall_text(raw_text)
        
        return jsonify({
            "success": True,
            "raw_text": raw_text,
            "parsed_data": parsed_data # Dati puliti!
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500