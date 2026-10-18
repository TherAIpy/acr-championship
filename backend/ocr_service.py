import pytesseract
from PIL import Image
import io
import os

# Su Render, Tesseract sarà installato tramite Dockerfile (vedi Passo 3)
# In locale su Windows, potresti dover specificare il percorso:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def extract_text_from_image(image_bytes: bytes) -> str:
    """Estrae il testo grezzo dall'immagine."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        
        # Preprocessing base per migliorare l'OCR
        img = img.convert('L') # Scala di grigi
        
        # Esegue l'OCR
        text = pytesseract.image_to_string(img, lang='ita+eng')
        return text
    except Exception as e:
        print(f"Errore durante l'OCR: {e}")
        return ""