"""
repro_issue4.py — Reproduction for Issue #4:
"I got notified when a friend added my song to a playlist but not when they rated it"

Reported by aaliya: adding a shared song to a playlist notifies the sharer, but
rating a shared song notifies no one — the rating saves, yet no notification is
created.

You cannot see this by inspecting the seed data: aaliya shared 0 songs, and there
are 0 ratings in the DB until the rate endpoint is actually called. So this script
performs a rating against a song a user really shared (nova's), rated by her friend
darius, and checks whether a notification appears for nova.

It is written to stay meaningful before AND after the fix:
  - BEFORE the fix (rate_song does not notify): nova's notification count is
    unchanged and "new rating notification?" is False.
  - AFTER the fix (rate_song notifies the sharer): a 'song_rated' notification is
    created for nova and the script prints its body.
For contrast it also shows (read-only) that the playlist path already notifies.

Cleanup restores the seed state: it deletes the rating it creates AND any
notification that appeared during the run (identified by diffing notification ids),
so the database is left unchanged whether or not the fix is present.

Run with:
    ./.venv/Scripts/python.exe repro_issue4.py     # Windows / Git Bash
    python repro_issue4.py                          # with the venv activated
"""

from app import create_app, db
from models import User, Song, Rating, Notification
from services.notification_service import rate_song


def main():
    app = create_app()
    with app.app_context():
        nova = db.session.query(User).filter_by(username="nova").first()
        darius = db.session.query(User).filter_by(username="darius").first()
        if nova is None or darius is None:
            raise SystemExit("Users missing — run `python seed_data.py` first.")

        song = db.session.query(Song).filter_by(shared_by=nova.id).first()

        # Contrast (read-only): the playlist path already notified nova.
        playlist_notifs = (
            db.session.query(Notification)
            .filter_by(user_id=nova.id, notification_type="song_added_to_playlist")
            .count()
        )
        print(f"nova has {playlist_notifs} 'song_added_to_playlist' notification(s) "
              f"-> the playlist path DOES notify")

        # Snapshot nova's notifications before the rating.
        before_ids = {
            n.id for n in db.session.query(Notification).filter_by(user_id=nova.id)
        }

        # The action from the issue: darius (a friend) rates nova's song.
        rate_song(darius.id, song.id, 5)

        after = db.session.query(Notification).filter_by(user_id=nova.id).all()
        new_notifs = [n for n in after if n.id not in before_ids]

        print(f"darius rates '{song.title}' 5 stars: "
              f"nova notifications {len(before_ids)} -> {len(after)}  "
              f"(new rating notification? {len(new_notifs) > 0})")
        for n in new_notifs:
            print(f"  created [{n.notification_type}]: {n.body}")

        rating_saved = (
            db.session.query(Rating)
            .filter_by(user_id=darius.id, song_id=song.id)
            .first()
            is not None
        )
        print(f"rating was saved to the DB? {rating_saved}")

        # Cleanup: remove the rating and any notification created this run.
        db.session.query(Rating).filter_by(
            user_id=darius.id, song_id=song.id
        ).delete()
        for n in new_notifs:
            db.session.delete(n)
        db.session.commit()


if __name__ == "__main__":
    main()
