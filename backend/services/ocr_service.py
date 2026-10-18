import pytesseract
from PIL import Image, ImageOps, ImageEnhance
import io

def extract_text_from_image(image_bytes: bytes) -> str:
    """Estrae il testo grezzo dall'immagine con preprocessing avanzato."""
    try:
        # Apri l'immagine
        img = Image.open(io.BytesIO(image_bytes))
        
        # 1. Converti in scala di grigi
        img = img.convert('L')
        
        # 2. Aumenta il contrasto (utile per schermate di gioco)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)
        
        # 3. Binarizzazione (trasforma tutto in bianco o nero)
        # Questo aiuta Tesseract a distinguere il testo dallo sfondo
        img = img.point(lambda x: 0 if x < 128 else 255, '1')
        
        # 4. Esegue l'OCR
        # Usiamo 'eng' come fallback se 'ita' non è installato
        text = pytesseract.image_to_string(img, lang='ita+eng')
        
        # Se il testo è vuoto, lanciamo un errore per farlo vedere al frontend
        if not text.strip():
            raise ValueError("Tesseract non ha rilevato alcun testo. L'immagine potrebbe essere troppo scura o sfocata.")
            
        return text
        
    except Exception as e:
        # Invece di restituire "", solleviamo l'errore così il frontend lo vede
        print(f"ERRORE OCR: {e}")
        raise e