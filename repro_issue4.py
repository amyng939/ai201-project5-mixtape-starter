"""
repro_issue4.py — Reproduction for Issue #4:
"I got notified when a friend added my song to a playlist but not when they rated it"

Reported by aaliya: adding a shared song to a playlist notifies the sharer, but
rating a shared song notifies no one — the rating saves, but no notification is
ever created, for anyone.

You cannot see this by inspecting the seed data: aaliya shared 0 songs, and there
are 0 ratings in the database until the rate endpoint is actually called. This
script performs a rating against a song a user really shared (nova's), rated by
her friend darius, and checks nova's notification count before and after.

For contrast it shows (read-only) that the seed already contains a
'song_added_to_playlist' notification — i.e. the playlist path DOES notify, while
the rating path does not.

It cleans up the rating it creates, so the database is left unchanged.

Run with:
    ./.venv/Scripts/python.exe repro_issue4.py      # Windows / Git Bash
    python repro_issue4.py                           # with the venv activated
"""

from app import create_app, db
from models import User, Song, Rating, Notification
from services.notification_service import rate_song, get_notifications


def main():
    app = create_app()
    with app.app_context():
        nova = db.session.query(User).filter_by(username="nova").first()
        darius = db.session.query(User).filter_by(username="darius").first()
        if nova is None or darius is None:
            raise SystemExit("Users missing — run `python seed_data.py` first.")

        rated_song = db.session.query(Song).filter_by(shared_by=nova.id).first()

        # --- Contrast (read-only): the playlist path already notified nova ---
        playlist_notifs = (
            db.session.query(Notification)
            .filter_by(user_id=nova.id, notification_type="song_added_to_playlist")
            .count()
        )
        print(f"nova has {playlist_notifs} 'song_added_to_playlist' notification(s) "
              f"-> the playlist path DOES notify")

        # --- The bug: rating a shared song ---
        before = len(get_notifications(nova.id))
        rate_song(darius.id, rated_song.id, 5)          # darius (friend) rates nova's song
        after = len(get_notifications(nova.id))
        print(f"darius rates '{rated_song.title}' 5 stars: "
              f"nova notifications {before} -> {after}  "
              f"(new notification? {after > before})")

        rating_saved = (
            db.session.query(Rating)
            .filter_by(user_id=darius.id, song_id=rated_song.id)
            .first()
            is not None
        )
        print(f"rating was saved to the DB? {rating_saved}  "
              f"(so the action worked - only the notification is missing)")

        # --- Cleanup: remove the rating we created ---
        db.session.query(Rating).filter_by(
            user_id=darius.id, song_id=rated_song.id
        ).delete()
        db.session.commit()


if __name__ == "__main__":
    main()
