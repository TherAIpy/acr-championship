import re

def parse_overall_text(raw_text: str) -> list:
    results = []
    # Pulisci il testo e dividilo in righe, rimuovendo quelle vuote
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]

    for i, line in enumerate(lines):
        # 1. Cerca un tempo valido (es. 00:50.658 o 00:52,269)
        time_match = re.search(r'(\d{1,2}:\d{2}[.,]\d{3})', line)
        
        if time_match:
            time_str = time_match.group(1).replace(',', '.')
            
            # 2. Cerca il Gap nella riga successiva (es. +00.135)
            gap_str = "-"
            if i + 1 < len(lines):
                gap_match = re.search(r'(\+\d{1,2}[.,]\d{3})', lines[i+1])
                if gap_match:
                    gap_str = gap_match.group(1).replace(',', '.')
            
            # 3. Cerca il Nome del Pilota e l'Auto nelle righe precedenti
            driver_name = "Sconosciuto"
            car_model = ""
            
            # Risaliamo al massimo di 5 righe per trovare il modello dell'auto
            car_idx = -1
            for j in range(i-1, max(i-5, -1), -1):
                # Cerchiamo una riga che sembri un'auto (spesso ha un trattino o nomi noti)
                if " - " in lines[j] or any(brand in lines[j] for brand in ["Alpine", "Fiat", "Ford", "Toyota", "Subaru", "Citroen", "Hyundai", "Skoda"]):
                    car_model = lines[j]
                    car_idx = j
                    break
            
            if car_idx != -1:
                # Il nome del pilota è solitamente la riga sopra l'auto
                # A volte c'è la posizione (es. "10") o simboli strani (es. "■■") prima del nome
                driver_idx = car_idx - 1
                if driver_idx >= 0:
                    candidate_name = lines[driver_idx]
                    
                    # Se la riga è solo un numero (la posizione) o un simbolo strano, sali di un'altra riga
                    if (candidate_name.isdigit() or len(candidate_name) <= 2) and driver_idx > 0:
                        candidate_name = lines[driver_idx - 1]
                    
                    driver_name = candidate_name
            else:
                # Fallback se non trova l'auto
                if i >= 3:
                    driver_name = lines[i-3]

            # 4. Pulisci il nome del pilota da numeri di posizione o caratteri spuri all'inizio
            driver_name = re.sub(r'^\d+[\s\.\-]*', '', driver_name)
            driver_name = re.sub(r'^[^a-zA-Z0-9\[\]\(\)]+', '', driver_name).strip()

            if driver_name:
                results.append({
                    "position": len(results) + 1,
                    "driver": driver_name,
                    "time": time_str,
                    "gap": gap_str
                })

    # 5. Rimuovi eventuali duplicati (es. l'ultima riga ripetuta in fondo)
    unique_results = []
    seen_times = set()
    for r in results:
        if r['time'] not in seen_times:
            unique_results.append(r)
            seen_times.add(r['time'])
            
    # Riassegna le posizioni in ordine numerica
    for i, r in enumerate(unique_results):
        r['position'] = i + 1

    return unique_results