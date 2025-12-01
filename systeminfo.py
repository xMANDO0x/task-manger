# system_info.py
# ----------------
# This file contains system information functions:
#   - CPU usage
#   - RAM usage
# It uses psutil (must be installed)

import psutil

def get_cpu_usage():
    """
    Returns the current CPU usage percentage.
    """
    return psutil.cpu_percent(interval=0.5)

def get_memory_usage():
    """
    Returns system memory in this form:
    {
        "total": ...,
        "used": ...,
        "free": ...,
        "percent": ...
    }
    """
    mem = psutil.virtual_memory()
    return {
        "total": mem.total,
        "used": mem.used,
        "free": mem.available,
        "percent": mem.percent
    }