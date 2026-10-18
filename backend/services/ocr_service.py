import pytesseract
from PIL import Image, ImageOps, ImageEnhance
import io

def extract_text_from_image(image_bytes: bytes) -> str:
    """Estrae il testo grezzo dall'immagine con preprocessing per UI scure."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        
        # 1. Scala di grigi
        img = img.convert('L')
        
        # 2. INVERTI I COLORI (Tesseract preferisce testo nero su sfondo bianco)
        img = ImageOps.invert(img)
        
        # 3. Aumenta il contrasto per staccare il testo dallo sfondo
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
        
        # 4. Raddoppia la dimensione (l'upscaling aiuta molto con font piccoli)
        width, height = img.size
        img = img.resize((width * 2, height * 2), Image.Resampling.LANCZOS)
        
        # 5. Configurazione Tesseract per blocchi di testo (PSM 6)
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(img, lang='ita+eng', config=custom_config)
        
        if not text.strip():
            raise ValueError("Tesseract non ha rilevato alcun testo.")
            
        return text
        
    except Exception as e:
        print(f"ERRORE OCR: {e}")
        raise e