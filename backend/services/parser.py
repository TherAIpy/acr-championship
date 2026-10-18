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
    
    # 2. Estrai i piloti
    for i, line in enumerate(lines):
        # Cerca il tempo principale (es. 00:50.658)
        time_match = re.search(r'(\d{1,2}:\d{2}[.,]\d{3})', line)
        
        # Fallback per errori OCR gravi (es. "Nn:53.319")
        is_corrupted = False
        if not time_match and i >= 4:
            if re.match(r'^[A-Z0-9]{1,3}$', lines[i-1]) and ' - ' in lines[i-2]:
                is_corrupted = True
        
        if time_match or is_corrupted:
            if time_match:
                time_str = time_match.group(1).replace(',', '.')
            else:
                clean_digits = re.sub(r'[^\d:]', '', line)
                time_str = "00:" + clean_digits.split(':')[-1] if ':' in clean_digits else line

            # 2a. Cerca il Gap (di solito è la riga successiva che inizia con +)
            gap_str = "-"
            if i + 1 < len(lines) and lines[i+1].startswith('+'):
                gap_str = lines[i+1].replace(',', '.')

            # 2b. Cerca l'Auto (risalendo verso l'alto)
            car_idx = -1
            for j in range(i-1, max(i-6, -1), -1):
                # L'auto contiene sempre " - " o è un modello noto
                if " - " in lines[j] or any(brand in lines[j] for brand in ["Alpine", "Fiat", "Ford", "Toyota", "Subaru", "Citroen", "Hyundai", "Skoda"]):
                    car_idx = j
                    break
            
            if car_idx != -1:
                car_model = lines[car_idx]
                
                # 2c. Il Pilota è la riga sopra l'auto
                player_idx = car_idx - 1
                if player_idx >= 0:
                    potential_name = lines[player_idx]
                    
                    # Se per caso c'è un numero di posizione o una classe (es. H2), sali di un'altra riga
                    if potential_name.isdigit() or potential_name in ["H2", "H1", "WR EVO1"]:
                        player_idx -= 1
                        
                if player_idx >= 0:
                    player_raw = lines[player_idx]
                    
                    # 2d. Cerca Penalità (se la riga sopra il tempo è un altro tempo)
                    penalty_str = "-"
                    if i >= 1 and re.search(r'\d{1,2}:\d{2}[.,]\d{3}', lines[i-1]):
                        penalty_str = lines[i-1]

                    # Pulisci il nome
                    player_name = re.sub(r'^\d+[\s\.\-]*', '', player_raw).strip()
                    player_name = re.sub(r'^[^a-zA-Z0-9\[\]\(\)]+', '', player_name).strip()
                    
                    # Filtro di sicurezza per evitare di inserire intestazioni
                    if player_name and player_name not in ["Player", "Pos.", "Class", "Penalty", "Time"]:
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