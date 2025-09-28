def is_valid_ipv4(ip: str) -> bool:
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        if not 0 <= int(part) <= 255:
            return False
    return True

def cleanup_temp_files():
    import glob
    import os
    import logging

    logger = logging.getLogger(__name__)
    temp_files = glob.glob("/tmp/tor_*")
    cleaned = 0
    for temp_file in temp_files:
        try:
            if os.path.isfile(temp_file):
                os.unlink(temp_file)
                cleaned += 1
            elif os.path.isdir(temp_file):
                import shutil
                shutil.rmtree(temp_file, ignore_errors=True)
                cleaned += 1
        except Exception:
            pass
    if cleaned > 0:
        logger.info(f"Cleaned up {cleaned} temporary files")
    return cleaned

