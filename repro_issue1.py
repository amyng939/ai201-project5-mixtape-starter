"""
repro_issue1.py — Reproduction for Issue #1: "My listening streak keeps resetting"

Reported by kenji: streak was 12 on Saturday, he listened again Sunday morning,
and it dropped to 1 instead of going to 13. It only ever happened on a Sunday.

The bug can't be triggered with a live HTTP request on an arbitrary day, because
the streak logic reads the real `datetime.now()`. This script drives
`update_listening_streak(user, now)` directly with a controlled "now" so the
Saturday -> Sunday scenario is reproducible on any day.

It does NOT commit — the database is left untouched (db.session.rollback()).

Run with:
    ./.venv/Scripts/python.exe repro_issue1.py      # Windows / Git Bash
    python repro_issue1.py                           # with the venv activated
"""

from datetime import datetime, timezone

from app import create_app, db
from models import User
from services.streak_service import update_listening_streak


def main():
    app = create_app()
    with app.app_context():
        kenji = db.session.query(User).filter_by(username="kenji").first()
        if kenji is None:
            raise SystemExit("kenji not found — run `python seed_data.py` first.")

        # --- Case 1: kenji's report. Streak 12 on Saturday, listens Sunday. ---
        kenji.listening_streak = 12
        kenji.last_listened_at = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)  # Saturday
        sunday_morning = datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc)           # next day = Sunday
        update_listening_streak(kenji, sunday_morning)
        print(f"SUNDAY listen (after Sat) -> streak = {kenji.listening_streak}  "
              f"(expected 13, bug produces 1)")

        # --- Case 2: contrast. Same "listened yesterday" setup, but not a Sunday. ---
        kenji.listening_streak = 12
        kenji.last_listened_at = datetime(2026, 7, 9, 20, 0, tzinfo=timezone.utc)   # Thursday
        friday_morning = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)           # Friday
        update_listening_streak(kenji, friday_morning)
        print(f"FRIDAY listen (after Thu) -> streak = {kenji.listening_streak}  "
              f"(increments normally)")

        # Leave the database exactly as we found it.
        db.session.rollback()


if __name__ == "__main__":
    main()