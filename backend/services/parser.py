import re

def parse_overall_text(raw_text: str) -> list:
    players = []
    times = []
    gaps = []
    
    # Stato attuale della lettura
    state = None
    
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]

    for line in lines:
        # 1. Cambio di stato in base alle intestazioni della tabella
        if line == "Player":
            state = "player"
            continue
        elif line == "Time":
            state = "time"
            continue
        elif line == "Diff.":
            state = "diff"
            continue
        elif line in ["Car Model", "Class", "Penalty", "Pos."]:
            # Se troviamo altre intestazioni, smettiamo di raccogliere
            state = None
            continue
            
        # 2. Raccolta dati in base allo stato
        if state == "player":
            # Ignora spazzatura, simboli o numeri di posizione isolati
            if line in ["+", "-", "■", "=", "Enter", "SELECT", "BACK", "RECOMMENDED REFERENCE TIME", "OVERALL", "SECTORS"]:
                continue
            if re.match(r'^\d+$', line):
                continue
            players.append(line)
            
        elif state == "time":
            # Cerca un tempo valido (es. 00:50.658)
            time_match = re.search(r'(\d{1,2}:\d{2}[.,]\d{3})', line)
            if time_match:
                times.append(time_match.group(1).replace(',', '.'))
            else:
                # Gestisce errori OCR comuni (es. Nn:53.319)
                corrupted_time = re.search(r'[a-zA-Z0-9]{1,2}:\d{2}[.,]\d{3}', line)
                if corrupted_time:
                    t = corrupted_time.group(0).replace('Nn', '00').replace('O', '0').replace('o', '0').replace('S', '5').replace('s', '5')
                    times.append(t)
                    
        elif state == "diff":
            # Cerca un gap (es. +00.135)
            gap_match = re.search(r'(\+\d{1,2}[.,]\d{3})', line)
            if gap_match:
                gaps.append(gap_match.group(1).replace(',', '.'))

    # 3. Allineamento
    # Di solito il primo classificato non ha il gap, quindi i gap sono (tempi - 1)
    if len(gaps) == len(times) - 1:
        gaps.insert(0, "-")

    # 4. Costruzione dei risultati finali
    results = []
    for i in range(min(len(players), len(times))):
        gap = gaps[i] if i < len(gaps) else "-"
        
        # Pulisci il nome da eventuali numeri o spazi rimasti attaccati
        p_name = re.sub(r'^\d+[\s\.\-]*', '', players[i]).strip()
        p_name = re.sub(r'^[^a-zA-Z0-9\[\]\(\)]+', '', p_name).strip()
        
        if p_name:
            results.append({
                "position": i + 1,
                "driver": p_name,
                "time": times[i],
                "gap": gap
            })

    # 5. Rimuovi duplicati (a volte l'OCR ripete l'ultima riga in fondo)
    unique_results = []
    seen = set()
    for r in results:
        key = (r['driver'], r['time'])
        if key not in seen:
            unique_results.append(r)
            seen.add(key)
            
    # Riassegna le posizioni
    for i, r in enumerate(unique_results):
        r['position'] = i + 1

    return unique_results