"""
Runs a single overnight recap and exits. Triggered by its own scheduled
workflow, separate from the intraday signal-check schedule.
"""

from main import send_night_recap

if __name__ == "__main__":
    send_night_recap()
