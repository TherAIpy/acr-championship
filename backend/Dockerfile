# Usa un'immagine Python ufficiale e leggera
FROM python:3.12-slim

# Installa Tesseract OCR e i pacchetti per la lingua italiana
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ita \
    && rm -rf /var/lib/apt/lists/*

# Imposta la cartella di lavoro
WORKDIR /app

# Copia i requisiti e installali
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutto il resto del codice
COPY . .

# Avvia l'applicazione con Gunicorn
CMD ["gunicorn", "app:app", "--timeout", "120"]