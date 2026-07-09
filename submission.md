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

**How I reproduced it:** two ways.

*Deterministic script — [`repro_issue2.py`](repro_issue2.py).* This is the
reliable reproduction. A fresh same-day seed hides the bug: every friend's most
recent listen is only a few hours old, so all of them are genuinely "today" and
the feed looks correct. The bug needs a specific **data condition** — a friend
whose most recent listen was on the **previous calendar day** but still **less
than 24 hours ago** (nova's story: darius played a song at 11pm last night, she
checked at 9am). So the script sets darius up with a single listen timestamped
11:59pm *yesterday* and nothing today, then compares the two window definitions
on that data:

```
OLD 24h window (the bug):   nova sees ['darius', 'simone', 'kenji']
  darius shown though he listened yesterday? True
TODAY-only (the fix):        nova sees []
```

The action mirrors `GET /feed/<user_id>/listening-now`. darius appears under the
24-hour window even though his listen was yesterday — the reported symptom.

*Live endpoint, after the date rolled over.* Because the seed timestamps are all
2026-07-08 and I was testing just after 00:00 UTC on 2026-07-09, every seeded
listen had become "yesterday" while still being <24h old. Hitting
`/feed/680aa719-8d0b-4cd6-b8f6-965bde74dba4/listening-now` on the running server
therefore listed darius/simone/kenji as "listening now" even though nobody had
listened on the new calendar day — the same bug, observed live.

**Root of it (confirmed at fix time):** `get_friends_listening_now()` built its
cutoff as `datetime.now(timezone.utc) - RECENT_THRESHOLD`, where
`RECENT_THRESHOLD = timedelta(hours=24)` — a *rolling 24-hour* window rather than
"today." A late-yesterday listen stays inside that window until the same clock
time the next day, exactly the "hangs around until the same time the next day"
behavior nova described.

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

---

## Milestone 3: Root Cause Analysis

### Issue #1 — My listening streak keeps resetting (kenji)

**How I reproduced it.** I ran [`repro_issue1.py`](repro_issue1.py), which drives
`update_listening_streak(user, now)` with a controlled clock: a user with
`listening_streak = 12` and `last_listened_at` on a **Saturday**, then a listen
on the following **Sunday**. Before the fix the streak dropped to **1** instead
of going to **13**. The control case (previous listen Thursday, new listen Friday)
correctly incremented, so the failure was specific to Sunday — matching kenji's
report that it only ever happened on Sundays.

**How I found the root cause.** I traced the call chain top-down from the action
kenji described. Recording a listen is `POST /songs/<id>/listen`, so I started in
[`routes/songs.py`](routes/songs.py#L43): the `listen()` route calls
`record_listening_event(user_id, song_id)`. That led me to
[`services/streak_service.py`](services/streak_service.py), where
`record_listening_event()` creates the event and then calls
`update_listening_streak(user, now)`. Reading that function, the streak math is a
three-way branch on `days_since_last`. The "consecutive day" branch was:

```python
elif days_since_last == 1 and today.weekday() != 6:
    user.listening_streak += 1
```

The `and today.weekday() != 6` clause is what made me confident this was the
exact cause, not just a suspicious area: it's the only part of the logic that
depends on *which* weekday it is, and kenji's bug was weekday-specific.

**The root cause.** Python's `datetime.date.weekday()` returns **6 for Sunday**
(Monday = 0 … Sunday = 6). The increment branch required both
`days_since_last == 1` *and* `today.weekday() != 6`. On a Sunday, a genuine
consecutive-day listen satisfies `days_since_last == 1` but fails
`today.weekday() != 6`, so execution fell through to the `else:` branch, which
sets `user.listening_streak = 1`. The effect: any streak update that landed on a
Sunday was treated as a skipped-day reset instead of an increment, wiping the
streak — exactly what kenji saw (12 → 1 on Sunday, then counting up again from
Monday). On every other weekday the extra condition was true, so the increment
worked normally, which is why the bug was invisible six days a week.

**My fix and side-effect check.** I removed the spurious `and today.weekday() != 6`
condition so the branch is simply:

```python
elif days_since_last == 1:
    user.listening_streak += 1
```

A consecutive-day listen now increments regardless of weekday, which is the
intended behavior. After the change I re-ran `repro_issue1.py` — the Sunday case
now returns **13** — and ran the streak test suite:

```
$ ./.venv/Scripts/python.exe -m pytest tests/test_streaks.py -v
5 passed
```

All five pass, including `test_streak_increments_on_sunday` (the direct
regression test for this bug) and `test_streak_resets_after_skipped_day`, which
checks the *other* side of the boundary — confirming the streak still resets when
a day is genuinely skipped, and I didn't over-correct.

### Issue #2 — Friends Listening Now shows people from yesterday (nova)

**How I reproduced it.** See the Milestone 2 entry for the full account. In short:
[`repro_issue2.py`](repro_issue2.py) gives darius a single listen at 11:59pm
*yesterday* and shows that the old 24-hour window still lists him as "listening
now" (`['darius', 'simone', 'kenji']`) while a today-only window drops him (`[]`).
I also saw it live: testing just after 00:00 UTC, the seeded 07-08 listens had all
become "yesterday" yet stayed <24h old, so the endpoint still showed them.

**How I found the root cause.** I started from the endpoint nova used,
`GET /feed/<id>/listening-now`, in [`routes/feed.py`](routes/feed.py#L9), which
calls `feed_service.get_friends_listening_now()`. In
[`services/feed_service.py`](services/feed_service.py) the first thing that
function does is build a time cutoff: `cutoff = datetime.now(timezone.utc) -
RECENT_THRESHOLD`, with `RECENT_THRESHOLD = timedelta(hours=24)`, then filter
`ListeningEvent.listened_at >= cutoff`. That single line is the whole bug — the
feed's notion of "now" was "any time in the last 24 hours."

Along the way I chased a **red herring**: darius's `User.last_listened_at`
(yesterday) doesn't match his most recent `ListeningEvent` (today) in the seed. I
ruled it out by reading the query — `get_friends_listening_now()` never reads
`last_listened_at`; it filters purely on `ListeningEvent.listened_at`. That
mismatch belongs to the streak feature (Issue #1), not the feed.

**The root cause.** "Listening now" was implemented as a **rolling 24-hour
window**, not the current calendar day. `cutoff = now - 24h` means an event
qualifies as "now" for a full 24 hours after it happens. So a listen at 11pm
yesterday is still within the window at 9am today (only 10 hours old) and is shown
as if the friend were listening now. The window only clears a given listen 24
hours later — hence nova's "stuff from yesterday evening hangs around until the
same time the next day."

**My fix and side-effect check.** I changed the cutoff from "24 hours ago" to
"the start of today (UTC midnight)" and kept the same `>=` comparison:

```python
cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
...
ListeningEvent.listened_at >= cutoff,
```

Now only listens from the current calendar day qualify, so yesterday-evening
listens are excluded the instant the date rolls over. I also removed the
now-unused `RECENT_THRESHOLD` constant. (`timedelta` is no longer used in this
file and could be dropped from the import as a follow-up cleanup.)

Side-effect checks:
- **Both sides of the boundary** (this is a boundary bug): via `repro_issue2.py`,
  an 11:59pm-*yesterday* listen is now excluded, and the live function returns the
  today-only set. A listen just after midnight *today* still qualifies.
- **The other function in the file**, `get_activity_feed()`, is intentionally
  *not* recency-filtered (it returns the most recent N events regardless of date),
  so it doesn't share this cutoff and was unaffected. There is no automated test
  suite for the feed, so I verified behavior with the repro script rather than
  pytest. The 2 failing tests in `pytest tests/` are the unrelated open Issue #5
  (playlist last song).