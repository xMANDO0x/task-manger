# process_utils.py
# -----------------
# This file contains all functions related to process handling:
#   - listing running processes
#   - killing a process by PID
#
# It uses the standard Python "subprocess" and "sig" modules
# so the code works on most systems without extra libraries.

import os
import signal

def list_processes():
    """
    Returns a list of running processes.
    Each process is returned as a dictionary with:
    - pid
    - name
    """
    processes = []

    try:
        # TASKLIST works on Windows
        stream = os.popen("tasklist")
        output = stream.readlines()

        # Skip the first 3 lines (header)
        for line in output[3:]:
            parts = line.split()
            if len(parts) >= 2:
                name = parts[0]
                pid = parts[1]
                if pid.isdigit():
                    processes.append({"pid": int(pid), "name": name})
    except Exception as e:
        print("Error listing processes:", e)

    return processes


def kill_process(pid):
    """
    Attempts to kill a process using its PID.
    Returns True if successful, False otherwise.
    """
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False