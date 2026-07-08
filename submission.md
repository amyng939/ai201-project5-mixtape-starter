# Project 5: Mixtape — Submission

## Milestone 1: Codebase Map

Mixtape is a social music app (Flask + SQLAlchemy, SQLite) where friends share
songs, rate them, build collaborative playlists, and track listening stats. It's
a pure JSON API — there are no HTML templates, every endpoint returns JSON.

---

### The main files and what each one does

**`app.py` — application factory + DB handle.**
Defines the module-level `db = SQLAlchemy()` that every other module imports, and
`create_app()`, which configures the DB URI (`sqlite:///mixtape.db` by default),
registers the four blueprints under URL prefixes (`/songs`, `/playlists`,
`/users`, `/feed`), and calls `db.create_all()`. This is the standard Flask
*application-factory* pattern — nothing runs at import time; you build an app by
calling the factory. That's why the run command is `FLASK_APP=app:create_app`.

**`models.py` — the data model.** Defines 7 tables via SQLAlchemy models plus 3
association tables:

| Model | Purpose | Notable columns |
|-------|---------|-----------------|
| `User` | A person | `listening_streak`, `last_listened_at`, self-referential `friends` (many-to-many via `friendships`) |
| `Tag` | A genre/mood label | `name` (unique) |
| `Song` | A shared track | `shared_by` (FK → User = the sharer), `share_note`, tags (M2M) |
| `ListeningEvent` | One play of a song by a user | `listened_at` — the raw signal behind streaks & feeds |
| `Rating` | A user's 1–5 score for a song | **unique constraint on (user_id, song_id)** — one rating per user per song |
| `Playlist` | A named collection | `created_by`, `is_collaborative` |
| `Notification` | An inbox message | `notification_type`, `body`, `read` |

Three **association tables** (not models, just `db.Table`):
- `friendships` — symmetric user↔user; the seed inserts *both* directions.
- `song_tags` — song↔tag.
- `playlist_entries` — the important one: a **join table with extra columns**
  (`position`, `added_by`, `added_at`). Songs in a playlist have an *explicit
  ordered position*, not just insertion order. This is what makes "song #N in the
  playlist" a real concept.

Every model has a `to_dict()` used for JSON serialization. Primary keys are
Python-generated UUID strings (`generate_uuid`), and all timestamps are
timezone-aware UTC.

**`routes/` — 4 blueprints, thin HTTP layer.**
- `songs.py` — `GET /songs/search?q=`, `GET /songs/<id>`, `POST /songs/<id>/rate`,
  `POST /songs/<id>/listen`.
- `playlists.py` — `POST /playlists/`, `GET /playlists/<id>`,
  `GET/POST /playlists/<id>/songs`.
- `users.py` — `GET /users/<id>`, `GET /users/<id>/streak`,
  `GET /users/<id>/notifications`, `POST /users/notifications/<id>/read`.
- `feed.py` — `GET /feed/<id>/listening-now`, `GET /feed/<id>/activity`.

**`services/` — 5 modules, all the business logic.**
- `streak_service.py` — records listening events and updates consecutive-day streaks.
- `feed_service.py` — "friends listening now" (recent) and "activity feed" (last N).
- `search_service.py` — song search by title/artist with tags.
- `notification_service.py` — creating/reading notifications; also owns
  `rate_song()` and `add_to_playlist()` (the two actions that *generate* notifications).
- `playlist_service.py` — playlist create/read, and ordered song retrieval.

**`seed_data.py`** — drops & recreates the DB and loads 5 users (with
friendships), 25 songs (deliberately with 0/1/3+ tags), 3 playlists, listening
events (some in the last 30 min, some days old), streaks, and one sample
notification. Run it once with `python seed_data.py` before hitting the API.

**`tests/`** — pytest suites for streaks, search, and playlists.

---

### Data flow trace #1 — adding a friend's song to a playlist triggers a notification

This is the clearest end-to-end chain in the app:

1. **HTTP** — `POST /playlists/<playlist_id>/songs` with JSON body
   `{"song_id": ..., "added_by": ...}`.
2. **Route** — [`add_song()` in routes/playlists.py](routes/playlists.py#L43)
   parses `song_id` and `added_by`, returns 400 if either is missing, then calls
   `notification_service.add_to_playlist(playlist_id, song_id, added_by)`.
3. **Service** — [`add_to_playlist()` in services/notification_service.py](services/notification_service.py#L35):
   - Loads the `Song`, the adder `User`, and the `Playlist` (raising `ValueError`
     if any is missing).
   - Appends the song to `playlist.songs` — because `Playlist.songs` goes through
     the `playlist_entries` association, this writes a new join-table row.
   - **The notification rule:** `if song.shared_by != added_by_user_id`, it calls
     `create_notification(user_id=song.shared_by, type="song_added_to_playlist", body=...)`.
     So the notification goes to the *original sharer* of the song, not the person
     who added it — and you don't get notified about your own action.
4. **Persistence** — `create_notification()` writes a `Notification` row and commits.
5. **Read back** — the sharer later calls `GET /users/<id>/notifications` →
   [`get_notifications()`](services/notification_service.py#L113), which returns
   their notifications newest-first.

### Data flow trace #2 — rating a song

`POST /songs/<id>/rate` → [`rate()` in routes/songs.py](routes/songs.py#L29) →
[`rate_song()` in notification_service.py](services/notification_service.py#L73).
`rate_song()` validates the score is 1–5, then does an **upsert**: if a `Rating`
already exists for this (user, song) it updates the score, otherwise it inserts a
new one. There is no separate rating model — the score lives on the `Rating` row.
Worth noting for later: rating and playlist-add both live in
`notification_service.py`, but only the playlist-add branch actually creates a
notification today.

---

### Patterns I noticed

1. **Thin routes, fat services.** Every route does the same three things: parse
   the request, call one service function, and format the JSON response. No
   business logic lives in `routes/`. All of it is in `services/`.

2. **`ValueError` is the error channel.** Services raise `ValueError` for
   "not found" and validation failures; routes catch it and translate to HTTP
   status codes — 404 for lookups (`GET /songs/<id>`), 400 for mutations
   (`rate`, `add_song`). The message string becomes the JSON `error` field.

3. **`to_dict()` everywhere.** Serialization is a method on each model, so
   services return plain dicts and routes just `jsonify` them. The API never
   leaks SQLAlchemy objects.

4. **The application-factory + blueprint structure.** One `db` instance in
   `app.py`, imported by models and services; one blueprint per resource,
   mounted under a prefix. This is why there is **no route at `/`** — hitting the
   bare root returns 404 by design; every real endpoint lives under a prefix.

5. **Ordering is explicit in playlists.** Because `playlist_entries` carries a
   `position` column, `get_playlist_songs()` sorts by it rather than relying on
   insertion order. Any feature that talks about "the Nth song" depends on this.

6. **Time is central and always UTC.** Streaks compare *calendar dates*, and the
   feeds compare against a *recency cutoff* — both derived from
   `datetime.now(timezone.utc)` and the `listened_at` / `last_listened_at`
   timestamps. The seed data intentionally spreads events across "just now,"
   "yesterday," and "days ago" to exercise these time boundaries.

---

### Where each feature lives (orientation for the bug-hunt phase)

Per the README's issue tracker, each open issue maps to one service. This table
is just a locator for the next milestone — no diagnosis yet:

| # | Symptom | Feature entry point | Service / function |
|---|---------|--------------------|--------------------|
| 1 | Streak keeps resetting | `POST /songs/<id>/listen`, `GET /users/<id>/streak` | `streak_service.update_listening_streak()` |
| 2 | "Listening Now" shows stale people | `GET /feed/<id>/listening-now` | `feed_service.get_friends_listening_now()` |
| 3 | Same song appears twice in search | `GET /songs/search` | `search_service.search_songs()` |
| 4 | No notification when a friend rates my song | `POST /songs/<id>/rate` | `notification_service.rate_song()` |
| 5 | Last song in a playlist never shows | `GET /playlists/<id>/songs` | `playlist_service.get_playlist_songs()` |


## Milestone 2: Reproduction

I chose issues **#1, #2, and #4**. For each, the "how I reproduced it" below
records the inputs, the action sequence, and the data condition that had to be
true to hit the code path.

### Issue #1 — Listening streak resets on Sundays (kenji)

**How I reproduced it:** the script [`repro_issue1.py`](repro_issue1.py).

The reset only fires on a Sunday, and the live `POST /songs/<id>/listen` endpoint
reads the real system clock — so I couldn't trigger it on demand from the browser
on a non-Sunday. Instead I drove the streak logic directly with a controlled
"now" (the same value the endpoint would pass in):

- **Data condition:** a user with `listening_streak = 12` and `last_listened_at`
  set to a **Saturday**.
- **Action:** call `update_listening_streak(user, now)` with `now` = the
  following **Sunday** morning — equivalent to kenji recording a listen on Sunday
  and then checking `GET /users/<id>/streak`.
- **Actual result:** streak dropped to **1** instead of incrementing to 13.
- **Control:** the same "listened yesterday" setup with `now` on a **Friday**
  (previous listen Thursday) correctly incremented to **13** — confirming only
  Sunday is affected.

Run and output:
```
$ ./.venv/Scripts/python.exe repro_issue1.py
SUNDAY listen (after Sat) -> streak = 1   (expected 13, bug produces 1)
FRIDAY listen (after Thu) -> streak = 13  (increments normally)
```
The script uses `db.session.rollback()`, so it never modifies the database.

### Issue #2 — "Friends Listening Now" shows people from yesterday (nova)

**How I reproduced it:** the live endpoint
`GET /feed/<user_id>/listening-now` for nova
(`/feed/680aa719-8d0b-4cd6-b8f6-965bde74dba4/listening-now`).

- **Action:** open nova's `listening-now` feed.
- **Actual result:** darius appears in the feed as "listening now," even though
  his last listen was the **previous evening** (nova knew this independently —
  he'd told her he played it at 11pm before bed and hadn't opened the app all
  morning). The feed is supposed to show who's listening *now* / today, so a
  friend whose last listen was last night should not be there.
- **Data condition that triggers it:** a friend whose most recent listen was on
  the previous calendar day but still **less than 24 hours ago** (e.g. 11pm
  yesterday, viewed at 9am today).

**Root of it:** `get_friends_listening_now()` filters on
`RECENT_THRESHOLD = timedelta(hours=24)` — a *rolling 24-hour* window rather than
"today." A late-yesterday listen stays inside that window until the same clock
time the next day, which is exactly the "hangs around until the same time the
next day" behavior nova described.

### Issue #4 — No notification when a friend rates my song (aaliya)

**How I reproduced it:** the script [`repro_issue4.py`](repro_issue4.py).

The bug isn't visible by inspecting the seed data: aaliya shared **0** songs, and
there are **0** ratings in the DB until the rate endpoint is actually called — so
there's nothing of hers to look at. The report reads as aaliya's story, but the
defect is that rating *any* song, for *any* sharer, produces no notification. So
I reproduced it by performing a rating and comparing notification counts:

- **Data condition:** a song actually shared by a user (I used nova's
  "Midnight Drive") and a friend to rate it (darius, one of nova's friends).
- **Actions:**
  1. Confirm the **working** path first: nova already has one
     `song_added_to_playlist` notification → the playlist path *does* notify.
  2. Have darius rate nova's song 5 stars (`POST /songs/<id>/rate` equivalent).
  3. Re-check nova's notifications (`GET /users/<id>/notifications`).
- **Actual result:** nova's notification count stayed at **1 → 1** — no rating
  notification was created — while the `Rating` row **was** saved. So the action
  succeeds; only the notification is missing.

Run and output:
```
$ ./.venv/Scripts/python.exe repro_issue4.py
nova has 1 'song_added_to_playlist' notification(s) -> the playlist path DOES notify
darius rates 'Midnight Drive' 5 stars: nova notifications 1 -> 1  (new notification? False)
rating was saved to the DB? True  (so the action worked - only the notification is missing)
```
The script deletes the rating it creates, so the database is left unchanged.