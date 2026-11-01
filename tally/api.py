"""Read models for the web page: turn rows into what each screen shows."""

from __future__ import annotations

import datetime as dt
import time

from .db import Database

RECENT_DAYS = 120


def _progress(w) -> float | None:
    if w["completed"]:
        return 1.0
    pos = w["max_position"] or w["position"]
    if pos and w["duration"]:
        return max(0.0, min(1.0, pos / w["duration"]))
    return None


def title_dict(t) -> dict:
    return {
        "id": t["id"], "kind": t["kind"], "name": t["name"], "year": t["year"],
        "overview": t["overview"], "status": t["status"], "matched": bool(t["matched"]),
        "source": t["source"], "source_id": t["source_id"], "imdb_id": t["imdb_id"],
        "runtime": t["runtime"],
        "poster": f"/poster/{t['id']}?v={int(t['lookup_at'] or 0)}" if t["has_poster"] else None,
    }


_TITLE_COLS = ("t.id, t.kind, t.name, t.year, t.overview, t.status, t.matched, t.source, t.source_id, "
               "t.imdb_id, t.runtime, t.lookup_at, t.poster IS NOT NULL AS has_poster")

_WATCH_SQL = f"""
    SELECT w.*, e.name AS episode_name, {_TITLE_COLS.replace('t.id,', 't.id AS tid,')}
    FROM watches w
    LEFT JOIN titles t ON t.id = w.title_id
    LEFT JOIN episodes e ON e.title_id = w.title_id AND e.season = w.season AND e.number = w.episode
"""


def watch_dict(w) -> dict:
    d = {
        "id": w["id"], "title_id": w["title_id"], "season": w["season"], "episode": w["episode"],
        "episode_end": w["episode_end"], "episode_name": w["episode_name"], "filename": w["filename"],
        "path": w["path"], "player": w["player"], "started_at": w["started_at"],
        "last_seen_at": w["last_seen_at"], "position": w["max_position"] or w["position"],
        "duration": w["duration"], "watched_seconds": w["watched_seconds"],
        "progress": _progress(w), "completed": bool(w["completed"]), "estimated": bool(w["estimated"]),
        "manual": bool(w["manual"]),
        "title": title_dict({**dict(w), "id": w["tid"]}) if w["tid"] else None,
    }
    return d


class Api:
    def __init__(self, db: Database):
        self.db = db

    # ---- episodes helpers ----------------------------------------------

    def _episode_list(self, title_id: int) -> list:
        return self.db.query("SELECT season, number, name, airdate, runtime FROM episodes "
                             "WHERE title_id = ? ORDER BY season = 0, season, number", (title_id,))

    def _next_episode(self, title_id: int, season: int | None, episode: int | None) -> dict | None:
        """The episode after (season, episode), or None if caught up."""
        if episode is None:
            return None
        eps = [e for e in self._episode_list(title_id) if e["season"] > 0]
        if not eps:
            return {"season": season, "episode": episode + 1, "name": None, "airdate": None, "guess": True}
        s = season if season is not None else 1
        for e in eps:
            if (e["season"], e["number"]) > (s, episode):
                return {"season": e["season"], "episode": e["number"], "name": e["name"],
                        "airdate": e["airdate"], "guess": False}
        return None

    def _show_state(self, title_id: int) -> dict | None:
        """Where you are in a show: resume a partial episode, or the next one."""
        # Latest real viewing; a manual mark only counts if it's further along
        w = self.db.one(_WATCH_SQL + " WHERE w.title_id = ? AND w.episode IS NOT NULL AND w.manual = 0 "
                        "ORDER BY w.last_seen_at DESC LIMIT 1", (title_id,))
        marked = self.db.one(_WATCH_SQL + " WHERE w.title_id = ? AND w.episode IS NOT NULL AND w.manual = 1 "
                             "ORDER BY w.season DESC, w.episode DESC LIMIT 1", (title_id,))
        if marked and (w is None or (marked["season"] or 0, marked["episode"]) > (w["season"] or 0, w["episode"])):
            w = marked
        if w is None:
            return None
        last = watch_dict(w)
        if not w["completed"]:
            return {"state": "resume", "season": w["season"], "episode": w["episode"],
                    "episode_name": w["episode_name"], "progress": last["progress"],
                    "position": last["position"], "duration": last["duration"],
                    "last_watched": w["last_seen_at"], "watch": last}
        nxt = self._next_episode(title_id, w["season"], w["episode_end"] or w["episode"])
        if nxt is None:
            return {"state": "caught_up", "season": w["season"], "episode": w["episode"],
                    "episode_name": w["episode_name"], "last_watched": w["last_seen_at"], "watch": last}
        today = dt.date.today().isoformat()
        upcoming = bool(nxt["airdate"] and nxt["airdate"] > today)
        return {"state": "upcoming" if upcoming else "next", "season": nxt["season"], "episode": nxt["episode"],
                "episode_name": nxt["name"], "airdate": nxt["airdate"], "guess": nxt["guess"],
                "last_watched": w["last_seen_at"], "watch": last}

    # ---- screens --------------------------------------------------------

    def home(self, now_playing: list[dict], week_start: float | None = None) -> dict:
        now = time.time()
        if week_start is None:
            today = dt.date.today()
            monday = today - dt.timedelta(days=today.weekday())
            week_start = dt.datetime.combine(monday, dt.time()).timestamp()

        playing = []
        for np in now_playing:
            w = self.db.one(_WATCH_SQL + " WHERE w.id = ?", (np["watch_id"],))
            if w:
                playing.append({**watch_dict(w), "paused": np["paused"], "exact": np["exact"]})

        cont = []
        cutoff = now - RECENT_DAYS * 86400
        for t in self.db.query(f"SELECT {_TITLE_COLS}, MAX(w.last_seen_at) AS last FROM titles t "
                               "JOIN watches w ON w.title_id = t.id WHERE w.last_seen_at > ? "
                               "GROUP BY t.id ORDER BY last DESC", (cutoff,)):
            if t["kind"] == "show":
                st = self._show_state(t["id"])
                if st and st["state"] in ("resume", "next", "upcoming"):
                    cont.append({"title": title_dict(t), **st})
            else:
                w = self.db.one(_WATCH_SQL + " WHERE w.title_id = ? ORDER BY w.last_seen_at DESC LIMIT 1", (t["id"],))
                p = _progress(w) if w else None
                if w and not w["completed"] and p is not None and 0.02 <= p < 0.9:
                    wd = watch_dict(w)
                    cont.append({"title": title_dict(t), "state": "resume", "progress": p,
                                 "position": wd["position"], "duration": wd["duration"],
                                 "last_watched": w["last_seen_at"], "watch": wd})

        # "Mark as watched" clicks aren't viewing, so they stay out of the weekly view
        week = [watch_dict(w) for w in self.db.query(
            _WATCH_SQL + " WHERE w.last_seen_at >= ? AND w.manual = 0 ORDER BY w.started_at DESC", (week_start,))]
        stats = self.db.one(
            "SELECT COALESCE(SUM(watched_seconds), 0) AS secs, "
            "COUNT(DISTINCT CASE WHEN w.episode IS NOT NULL AND w.completed THEN w.title_id || ':' || w.season || ':' || w.episode END) AS eps, "
            "COUNT(DISTINCT CASE WHEN t.kind = 'movie' AND w.completed THEN w.title_id END) AS movies "
            "FROM watches w LEFT JOIN titles t ON t.id = w.title_id WHERE w.last_seen_at >= ? AND w.manual = 0",
            (week_start,))
        total = self.db.one("SELECT COALESCE(SUM(watched_seconds), 0) AS secs, COUNT(*) AS n, "
                            "COUNT(DISTINCT title_id) AS titles, MIN(started_at) AS first FROM watches")
        return {
            "now_playing": playing, "continue": cont[:24], "week": week, "week_start": week_start,
            "stats": {"week_seconds": stats["secs"], "week_episodes": stats["eps"], "week_movies": stats["movies"],
                      "total_seconds": total["secs"], "total_watches": total["n"], "total_titles": total["titles"],
                      "first_watch": total["first"]},
        }

    def shows(self) -> list[dict]:
        out = []
        for t in self.db.query(
                f"SELECT {_TITLE_COLS}, MAX(w.last_seen_at) AS last, "
                "COUNT(DISTINCT CASE WHEN w.completed THEN w.season || ':' || w.episode END) AS done, "
                "(SELECT COUNT(*) FROM episodes e WHERE e.title_id = t.id AND e.season > 0) AS total "
                "FROM titles t JOIN watches w ON w.title_id = t.id WHERE t.kind = 'show' "
                "GROUP BY t.id ORDER BY last DESC"):
            out.append({"title": title_dict(t), "last_watched": t["last"], "watched_episodes": t["done"],
                        "total_episodes": t["total"], "current": self._show_state(t["id"])})
        return out

    def movies(self) -> list[dict]:
        out = []
        for t in self.db.query(
                f"SELECT {_TITLE_COLS}, MAX(w.last_seen_at) AS last, SUM(w.completed) AS times, "
                "MAX(CASE WHEN w.duration > 0 THEN MIN(1.0, COALESCE(w.max_position, w.position, 0) / w.duration) END) AS best "
                "FROM titles t JOIN watches w ON w.title_id = t.id WHERE t.kind = 'movie' "
                "GROUP BY t.id ORDER BY last DESC"):
            out.append({"title": title_dict(t), "last_watched": t["last"], "times_finished": t["times"] or 0,
                        "progress": 1.0 if t["times"] else t["best"]})
        return out

    def title(self, title_id: int) -> dict | None:
        t = self.db.one(f"SELECT {_TITLE_COLS} FROM titles t WHERE t.id = ?", (title_id,))
        if t is None:
            return None
        history = [watch_dict(w) for w in self.db.query(
            _WATCH_SQL + " WHERE w.title_id = ? ORDER BY w.started_at DESC", (title_id,))]
        result = {"title": title_dict(t), "history": history}
        if t["kind"] == "show":
            by_ep: dict[tuple, dict] = {}
            for w in reversed(history):  # oldest first so the newest wins
                if w["episode"] is None:
                    continue
                key = (w["season"] if w["season"] is not None else -1, w["episode"])
                cur = by_ep.setdefault(key, {"count": 0, "completed": False, "progress": 0.0, "last": 0})
                cur["count"] += 1
                cur["completed"] = cur["completed"] or w["completed"]
                cur["progress"] = max(cur["progress"], w["progress"] or 0)
                cur["last"] = max(cur["last"], w["last_seen_at"])
                for extra in range((w["episode_end"] or w["episode"]) - w["episode"]):
                    by_ep[(key[0], w["episode"] + extra + 1)] = dict(cur)
            seasons: dict[int, list] = {}
            known = set()
            for e in self._episode_list(title_id):
                known.add((e["season"], e["number"]))
                seasons.setdefault(e["season"], []).append(self._ep_entry(
                    e["season"], e["number"], e["name"], e["airdate"], e["runtime"], by_ep))
            for (s, n) in sorted(by_ep):
                if (s, n) not in known:
                    seasons.setdefault(s, []).append(self._ep_entry(s, n, None, None, None, by_ep))
            for eps in seasons.values():
                eps.sort(key=lambda e: e["episode"])
            result["seasons"] = [{"season": s, "episodes": seasons[s]}
                                 for s in sorted(seasons, key=lambda s: (s <= 0, s))]
            result["current"] = self._show_state(title_id)
        return result

    @staticmethod
    def _ep_entry(season, number, name, airdate, runtime, by_ep) -> dict:
        w = by_ep.get((season, number), {})
        return {"season": season, "episode": number, "name": name, "airdate": airdate, "runtime": runtime,
                "watched": bool(w.get("completed")), "progress": w.get("progress") or 0,
                "times": w.get("count", 0), "last_watched": w.get("last")}

    def history(self, q: str = "", limit: int = 100, before: float | None = None,
                since: float | None = None) -> list[dict]:
        where, args = [], []
        if q:
            where.append("(t.name LIKE ? OR w.filename LIKE ? OR e.name LIKE ?)")
            args += [f"%{q}%"] * 3
        if before:
            where.append("w.started_at < ?")
            args.append(before)
        if since:
            where.append("w.started_at >= ?")
            args.append(since)
        sql = _WATCH_SQL + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY w.started_at DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))
        return [watch_dict(w) for w in self.db.query(sql, args)]

    # ---- edits ----------------------------------------------------------

    def delete_watch(self, watch_id: int) -> None:
        self.db.execute("DELETE FROM watches WHERE id = ?", (watch_id,))

    def set_completed(self, watch_id: int, completed: bool) -> None:
        self.db.execute("UPDATE watches SET completed = ? WHERE id = ?", (int(completed), watch_id))

    def mark_episode(self, title_id: int, season: int, episode: int, watched: bool) -> None:
        if watched:
            if self.db.one("SELECT 1 FROM watches WHERE title_id=? AND season=? AND episode=? AND completed=1",
                           (title_id, season, episode)):
                return
            runtime = self.db.runtime_seconds(title_id, season, episode)
            now = time.time()
            self.db.execute(
                "INSERT INTO watches(path, filename, title_id, season, episode, player, started_at, last_seen_at, "
                "position, duration, max_position, watched_seconds, completed, manual) "
                "VALUES('', '', ?, ?, ?, 'Marked', ?, ?, ?, ?, ?, 0, 1, 1)",
                (title_id, season, episode, now, now, runtime, runtime, runtime))
        else:
            self.db.execute("DELETE FROM watches WHERE title_id=? AND season=? AND episode=? AND manual=1",
                            (title_id, season, episode))
            self.db.execute("UPDATE watches SET completed = 0 WHERE title_id=? AND season=? AND episode=?",
                            (title_id, season, episode))

    def mark_through(self, title_id: int, season: int, episode: int) -> int:
        """Mark every episode up to and including this one as watched (catching up)."""
        count = 0
        for e in self._episode_list(title_id):
            if e["season"] > 0 and (e["season"], e["number"]) <= (season, episode):
                if not self.db.one("SELECT 1 FROM watches WHERE title_id=? AND season=? AND episode=? AND completed=1",
                                   (title_id, e["season"], e["number"])):
                    self.mark_episode(title_id, e["season"], e["number"], True)
                    count += 1
        return count

    def delete_title(self, title_id: int) -> None:
        with self.db.write_lock:
            self.db.execute("DELETE FROM watches WHERE title_id = ?", (title_id,))
            self.db.execute("DELETE FROM titles WHERE id = ?", (title_id,))

    def export(self) -> dict:
        titles = [dict(r) for r in self.db.query(
            "SELECT id, kind, name, year, overview, runtime, source, source_id, imdb_id, status FROM titles")]
        watches = [dict(r) for r in self.db.query("SELECT * FROM watches ORDER BY started_at")]
        episodes = [dict(r) for r in self.db.query("SELECT * FROM episodes")]
        return {"version": 1, "exported_at": time.time(), "titles": titles, "watches": watches, "episodes": episodes}
