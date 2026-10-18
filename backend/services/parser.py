import re

def parse_overall_text(raw_text: str) -> dict:
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    
    # 1. Estrai il nome del tracciato (tutto ciò che sta prima di OVERALL o SECTORS)
    track_name = "Tracciato Sconosciuto"
    track_lines = []
    for line in lines:
        if line in ["OVERALL", "SECTORS", "Pos.", "Player"]:
            break
        track_lines.append(line)
    if track_lines:
        track_name = " ".join(track_lines)

    results = []
    
    # 2. Estrai i piloti riga per riga
    for i, line in enumerate(lines):
        # Cerca il tempo principale (es. 00:50.658)
        time_match = re.search(r'(\d{1,2}:\d{2}[.,]\d{3})', line)
        
        # Fallback per errori OCR gravi (es. "Nn:53.319" o "กก:53.319")
        is_corrupted = False
        if not time_match and i >= 3:
            if re.match(r'^[A-Z0-9]{1,3}$', lines[i-1]) and ' - ' in lines[i-2]:
                is_corrupted = True
        
        if time_match or is_corrupted:
            if time_match:
                time_str = time_match.group(1).replace(',', '.')
            else:
                clean_digits = re.sub(r'[^\d:]', '', line)
                time_str = "00:" + clean_digits.split(':')[-1] if ':' in clean_digits else line

            # Cerca il Gap (di solito è la riga successiva che inizia con +)
            gap_str = "-"
            if i + 1 < len(lines) and lines[i+1].startswith('+'):
                gap_str = lines[i+1].replace(',', '.')
            
            # Cerca Penalità, Classe, Auto e Pilota risalendo
            penalty_str = ""
            class_idx = i - 1
            
            # Se la riga precedente è un tempo, è la penalità!
            if i - 1 >= 0 and re.search(r'\d{1,2}:\d{2}[.,]\d{3}', lines[i-1]):
                penalty_str = lines[i-1]
                class_idx = i - 2
            
            if class_idx >= 2:
                car_model = lines[class_idx - 1]
                player_raw = lines[class_idx - 2]
                
                # Pulisci il nome del pilota da numeri e simboli iniziali
                player_name = re.sub(r'^\d+[\s\.\-]*', '', player_raw).strip()
                player_name = re.sub(r'^[^a-zA-Z0-9\[\]\(\)]+', '', player_name).strip()
                
                if player_name:
                    results.append({
                        "position": len(results) + 1,
                        "driver": player_name,
                        "car": car_model,
                        "time": time_str,
                        "penalty": penalty_str,
                        "gap": gap_str
                    })

    # 3. Rimuovi duplicati e riordina
    unique_results = []
    seen = set()
    for r in results:
        key = (r['driver'], r['time'])
        if key not in seen:
            unique_results.append(r)
            seen.add(key)
            
    for idx, r in enumerate(unique_results):
        r['position'] = idx + 1

    return {
        "track_name": track_name,
        "results": unique_results
    }