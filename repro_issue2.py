"""
repro_issue2.py — Reproduction for Issue #2:
"Friends Listening Now shows people from yesterday"

Reported by nova: at ~9am her feed showed darius "listening now" to a song he
actually played at 11pm the night before, before bed. Listens from yesterday
evening linger in the feed until the same clock time the next day.

The bug: get_friends_listening_now() kept any listen within a rolling 24-HOUR
window (cutoff = now - 24h) instead of restricting to "today." A listen from late
yesterday is still < 24h old this morning, so it was shown.

This can't be seen on a fresh same-day seed, because every friend's most recent
listen is then only a few hours old (all genuinely "today"). So this script sets
up nova's story explicitly: it gives darius a SINGLE listen timestamped at the
very end of *yesterday* (11:59pm) and nothing today. That listen is:
  - on the PREVIOUS calendar day (a "today only" feed excludes it), yet
  - less than 24 hours ago (so the old 24h window still included it).

To stay meaningful whether or not the fix is applied, this script computes BOTH
window definitions directly on the same data:
  - OLD 24h window  -> the buggy behavior (darius appears)
  - TODAY-only       -> the correct behavior (darius drops out)
It also prints what the live get_friends_listening_now() currently returns.

Everything runs inside a transaction that is rolled back, so the database
(including darius's real listening events) is left exactly as it was.

Run with:
    ./.venv/Scripts/python.exe repro_issue2.py     # Windows / Git Bash
    python repro_issue2.py                          # with the venv activated
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc

from app import create_app, db
from models import User, Song, ListeningEvent
from services.feed_service import get_friends_listening_now


def _friends_within(friend_ids, cutoff):
    """Most-recent-per-friend usernames for events at/after cutoff (mirrors the feed's dedup)."""
    events = (
        db.session.query(ListeningEvent)
        .filter(ListeningEvent.user_id.in_(friend_ids), ListeningEvent.listened_at >= cutoff)
        .order_by(desc(ListeningEvent.listened_at))
        .all()
    )
    seen, names = set(), []
    for e in events:
        if e.user_id not in seen:
            seen.add(e.user_id)
            names.append(db.session.get(User, e.user_id).username)
    return names


def main():
    app = create_app()
    with app.app_context():
        now = datetime.now(timezone.utc)
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        last_night = start_of_today - timedelta(minutes=1)   # 11:59pm YESTERDAY

        nova = db.session.query(User).filter_by(username="nova").first()
        darius = db.session.query(User).filter_by(username="darius").first()
        song = db.session.query(Song).first()
        if nova is None or darius is None:
            raise SystemExit("Users missing — run `python seed_data.py` first.")

        # darius's story: his ONLY recent listen was 11:59pm last night, none today.
        # (Not committed — rolled back at the end.)
        db.session.query(ListeningEvent).filter_by(user_id=darius.id).delete()
        db.session.add(ListeningEvent(
            user_id=darius.id, song_id=song.id, listened_at=last_night
        ))
        db.session.flush()

        hrs = (now - last_night).total_seconds() / 3600
        print(f"now:                {now.strftime('%A %Y-%m-%d %H:%M')} UTC")
        print(f"darius last listen: {last_night.strftime('%A %Y-%m-%d %H:%M')} UTC "
              f"({hrs:.1f}h ago)")
        print(f"  -> darius listened TODAY? {last_night.date() == now.date()}")
        print()

        friend_ids = [f.id for f in nova.friends]
        old_window = _friends_within(friend_ids, now - timedelta(hours=24))
        today_only = _friends_within(friend_ids, start_of_today)
        live = [e["friend"]["username"] for e in get_friends_listening_now(nova.id)]

        print(f"OLD 24h window (the bug):   nova sees {old_window}")
        print(f"  darius shown though he listened yesterday? {'darius' in old_window}")
        print(f"TODAY-only (the fix):        nova sees {today_only}")
        print(f"LIVE get_friends_listening_now(): {live}")

        db.session.rollback()  # undo the darius setup; DB unchanged


if __name__ == "__main__":
    main()
