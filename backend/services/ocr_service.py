import os
import json
from google.oauth2 import service_account
from google.cloud import vision

def extract_text_from_image(image_bytes: bytes) -> str:
    """
    Invia l'immagine a Google Cloud Vision API e restituisce il testo estratto.
    """
    try:
        # 1. Recupera le credenziali dalla variabile d'ambiente di Render
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            raise ValueError("La variabile d'ambiente GOOGLE_CREDENTIALS_JSON non è impostata.")

        # 2. Crea le credenziali dal JSON
        creds_dict = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(creds_dict)

        # 3. Inizializza il client di Vision API
        client = vision.ImageAnnotatorClient(credentials=credentials)

        # 4. Prepara l'immagine
        image = vision.Image(content=image_bytes)

        # 5. Esegui il Document Text Detection (ottimo per tabelle e layout complessi)
        response = client.document_text_detection(image=image)

        # 6. Controlla se ci sono errori nell'API
        if response.error.message:
            raise Exception(f"Errore API Vision: {response.error.message}")

        # 7. Estrai il testo completo
        text = response.full_text_annotation.text
        
        if not text.strip():
            raise ValueError("Google Vision non ha rilevato alcun testo nell'immagine.")

        return text

    except Exception as e:
        print(f"ERRORE OCR VISION: {e}")
        raise e