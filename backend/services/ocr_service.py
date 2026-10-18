import os
import requests
from PIL import Image
import io

def extract_text_from_image(image_bytes: bytes) -> str:
    """
    Invia l'immagine all'API di OCR.space e restituisce il testo estratto.
    """
    try:
        api_key = os.environ.get("OCR_SPACE_API_KEY")
        if not api_key:
            raise ValueError("La variabile d'ambiente OCR_SPACE_API_KEY non è impostata.")

        # 1. Controllo e compressione dell'immagine (limite API: 1MB)
        img = Image.open(io.BytesIO(image_bytes))
        
        # Se l'immagine è troppo grande o pesante, la ridimensioniamo
        if len(image_bytes) > 1000000: # > 1 MB
            img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            image_bytes = buffer.getvalue()

        # 2. Prepara la chiamata API
        url = 'https://api.ocr.space/parse/image'
        payload = {
            'apikey': api_key,
            'language': 'ita',       # Imposta l'italiano (o 'eng' per l'inglese)
            'isOverlayRequired': False,
            'scale': True,           # Migliora la lettura di caratteri piccoli
            'OCREngine': 2           # Engine 2 è più preciso per le tabelle
        }
        
        files = {'file': ('screenshot.jpg', image_bytes, 'image/jpeg')}

        # 3. Esegui la richiesta
        response = requests.post(url, data=payload, files=files, timeout=30)
        response.raise_for_status()
        
        result = response.json()

        # 4. Gestisci eventuali errori restituiti dall'API
        if result.get('IsErroredOnProcessing'):
            error_msg = result.get('ErrorMessage', ['Errore sconosciuto'])[0]
            raise Exception(f"Errore OCR.space: {error_msg}")

        # 5. Estrai il testo
        text = "".join(result['ParsedResults'][0]['ParsedText'])
        
        if not text.strip():
            raise ValueError("OCR.space non ha rilevato alcun testo nell'immagine.")

        return text

    except Exception as e:
        print(f"ERRORE OCR SPACE: {e}")
        raise e