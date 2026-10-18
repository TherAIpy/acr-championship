def time_str_to_ms(time_str):
    """Converte '00:50.658' in millisecondi (50658)."""
    if not time_str or time_str == '-':
        return 0
    try:
        # Gestisce formati tipo "00:50.658" o "0:50.658"
        parts = time_str.split(':')
        minutes = int(parts[0])
        seconds_ms = parts[1].split('.')
        seconds = int(seconds_ms[0])
        ms = int(seconds_ms[1])
        return (minutes * 60000) + (seconds * 1000) + ms
    except:
        return 0

def ms_to_time_str(ms):
    """Converte millisecondi (50658) in '00:50.658'."""
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    milliseconds = ms % 1000
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"