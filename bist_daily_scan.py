"""
Runs a single BIST daily-picks scan and exits. Triggered by its own
scheduled workflow, separate from the intraday QQQ signal-check
schedule and from the overnight recap.
"""

from main import send_bist_daily_picks

if __name__ == "__main__":
    send_bist_daily_picks()
