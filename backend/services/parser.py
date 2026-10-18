import re

def parse_overall_text(raw_text: str) -> list:
    """Trasforma il testo grezzo dell'OCR in una lista di dizionari strutturati."""
    results = []
    lines = raw_text.split('\n')

    for line in lines:
        line = line.strip()
        if len(line) < 10:
            continue

        # 1. Trova il tempo (gestisce sia 00:50.658 che errori come 00:50,658)
        time_match = re.search(r'(\d{1,2}:\d{2}[.,]\d{3})', line)
        
        if not time_match:
            loose_time = re.search(r'(\d{1,2}:\d{2}[.,]?\w{2,4})', line)
            if loose_time:
                clean_time = loose_time.group(1).replace('S', '5').replace('s', '5').replace('O', '0').replace('o', '0').replace(',', '.')
                time_str = clean_time
            else:
                continue
        else:
            time_str = time_match.group(1).replace(',', '.')

        # 2. Trova il gap (es. +00.135)
        gap_match = re.search(r'\+\s*(\d{1,2})[.,]?(\d{3})', line)
        if gap_match:
            gap_str = f"+{gap_match.group(1)}.{gap_match.group(2)}"
        else:
            gap_str = "-"

        # 3. Estrai il nome del pilota usando Alpine e Fiat come ancore
        driver_name = "Sconosciuto"
        parts = re.split(r'Alpine|Fiat', line, flags=re.IGNORECASE)
        
        if len(parts) > 1:
            name_part = parts[0]
            name_match = re.search(r'([a-zA-Z].*)', name_part)
            if name_match:
                driver_name = name_match.group(1).strip()
                words = driver_name.split()
                while words and len(words[0]) <= 2:
                    words.pop(0)
                driver_name = " ".join(words)
        else:
            name_part = line[:time_match.start()].strip() if time_match else line
            name_match = re.search(r'([a-zA-Z].*)', name_part)
            if name_match:
                driver_name = name_match.group(1).strip()

        if driver_name:
            results.append({
                "position": len(results) + 1,
                "driver": driver_name,
                "time": time_str,
                "gap": gap_str
            })

    # Rimuovi duplicati
    unique_results = []
    seen_times = set()
    for r in results:
        if r['time'] not in seen_times:
            unique_results.append(r)
            seen_times.add(r['time'])
            
    for i, r in enumerate(unique_results):
        r['position'] = i + 1

    return unique_results