"""Experiment log: one CSV per session in logs/."""
import csv
import os
import threading
import time
from datetime import datetime

import config

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

START = time.time()
PATH = os.path.join(LOG_DIR, f"session_{datetime.now():%Y%m%d_%H%M%S}_{config.CONDITION}.csv")

_file = open(PATH, "w", newline="")
_writer = csv.writer(_file)
_writer.writerow(["clock", "seconds", "condition", "source", "type", "text"])
_lock = threading.Lock()


def log(source, kind, text=""):
    """source: voice / gesture / gaze / key / orca / world"""
    with _lock:
        _writer.writerow([datetime.now().strftime("%H:%M:%S"), f"{time.time() - START:.2f}",
                          config.CONDITION, source, kind, text])
        _file.flush()


def close():
    with _lock:
        _file.close()
