from flask import Flask, request, jsonify
import os
from services.ocr_service import extract_text_from_image  # <-- Nome corretto
from utils.db import get_supabase

app = Flask(__name__)
CORS(app)

@app.route('/')
def health_check():
    return jsonify({
        "status": "online",
        "message": "ACR Championship Backend è attivo"
    })

@app.route('/api/upload', methods=['POST'])
def upload_image():
    if 'screenshot' not in request.files:
        return jsonify({"error": "Nessun file caricato"}), 400
        
    file = request.files['screenshot']
    if file.filename == '':
        return jsonify({"error": "Nome file vuoto"}), 400

    image_bytes = file.read()
    raw_text = extract_text_from_image(image_bytes)
    
    return jsonify({
        "success": True,
        "raw_text": raw_text
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)