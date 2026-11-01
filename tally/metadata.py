"""Look up posters, years and descriptions.

Shows come from TVmaze (free, no key). Movies come from TMDB when you add a
free API key in Settings, otherwise from IMDb's public search plus Wikipedia.
"""

from __future__ import annotations

import difflib
import html
import json
import logging
import re
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import paths
from .parser import normalize

log = logging.getLogger(__name__)

USER_AGENT = "Tally/1.0 (personal watch history; https://github.com/)"
TIMEOUT = 12


@dataclass
class Candidate:
    source: str
    source_id: str
    kind: str
    name: str
    year: int | None = None
    overview: str | None = None
    poster_url: str | None = None
    imdb_id: str | None = None
    runtime: int | None = None
    status: str | None = None
    score: float = 0.0
    anime: bool = False
    episodes: list[tuple] = field(default_factory=list)  # (season, number, name, airdate, runtime)

    def summary(self) -> dict:
        return {"source": self.source, "source_id": self.source_id, "kind": self.kind,
                "name": self.name, "year": self.year, "overview": (self.overview or "")[:300],
                "poster_url": self.poster_url}


_ssl_lock = threading.Lock()
_ssl_ctx: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """A context with trusted roots, even on python.org builds that ship without any."""
    global _ssl_ctx
    with _ssl_lock:
        if _ssl_ctx is not None:
            return _ssl_ctx
        ctx = ssl.create_default_context()
        if not ctx.cert_store_stats().get("x509_ca"):
            try:
                import certifi  # type: ignore
                ctx.load_verify_locations(certifi.where())
            except ImportError:
                if sys.platform == "darwin":
                    pem = paths.data_dir() / "system-roots.pem"
                    try:
                        out = subprocess.run(
                            ["security", "find-certificate", "-a", "-p",
                             "/System/Library/Keychains/SystemRootCertificates.keychain"],
                            capture_output=True, check=True, timeout=30).stdout
                        pem.write_bytes(out)
                    except (OSError, subprocess.SubprocessError) as exc:
                        log.warning("could not read macOS root certificates: %s", exc)
                    if pem.exists():
                        ctx.load_verify_locations(str(pem))
        _ssl_ctx = ctx
        return ctx


def _open(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl_context())


def _get(url: str, headers: dict | None = None) -> bytes:
    with _open(url, headers) as resp:
        return resp.read()


def _json(url: str, headers: dict | None = None):
    return json.loads(_get(url, headers).decode("utf-8"))


def download_image(url: str) -> tuple[bytes, str] | None:
    try:
        with _open(url) as resp:
            ctype = resp.headers.get_content_type() or "image/jpeg"
            return resp.read(), ctype
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.info("poster download failed for %s: %s", url, exc)
        return None


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip() or None


def _year(date: str | None) -> int | None:
    if date and re.match(r"^\d{4}", date):
        return int(date[:4])
    return None


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _score(query: str, year: int | None, cand: Candidate, rank: int = 0) -> float:
    # "Frieren" should match "Frieren: Beyond Journey's End"
    s = max(similarity(query, cand.name), similarity(query, re.split(r"[:(]", cand.name)[0]) - 0.02)
    s += max(0.0, 0.04 - 0.01 * rank)  # the source's own ranking breaks ties
    if year and cand.year:
        if year == cand.year:
            s += 0.25
        elif abs(year - cand.year) == 1:
            s += 0.1
        else:
            s -= 0.2
    return s


# ---- TVmaze -------------------------------------------------------------

def tvmaze_search(query: str) -> list[Candidate]:
    data = _json("https://api.tvmaze.com/search/shows?q=" + urllib.parse.quote(query))
    return [_tvmaze_candidate(item["show"]) for item in data]


def _tvmaze_candidate(show: dict) -> Candidate:
    image = show.get("image") or {}
    return Candidate(
        source="tvmaze", source_id=str(show["id"]), kind="show", name=show.get("name") or "",
        year=_year(show.get("premiered")), overview=_strip_html(show.get("summary")),
        poster_url=image.get("original") or image.get("medium"),
        imdb_id=(show.get("externals") or {}).get("imdb"),
        runtime=show.get("averageRuntime") or show.get("runtime"), status=show.get("status"),
        anime=show.get("type") == "Animation" and show.get("language") == "Japanese",
    )


def tvmaze_details(source_id: str) -> Candidate:
    show = _json(f"https://api.tvmaze.com/shows/{source_id}?embed=episodes")
    cand = _tvmaze_candidate(show)
    for ep in (show.get("_embedded") or {}).get("episodes", []):
        if ep.get("season") is None or ep.get("number") is None:
            continue
        cand.episodes.append((ep["season"], ep["number"], ep.get("name"), ep.get("airdate"), ep.get("runtime")))
    return cand


# ---- TMDB (optional, needs a free key) ----------------------------------

TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"


def _tmdb(path: str, key: str, params: dict | None = None):
    params = dict(params or {})
    headers = {}
    if len(key) > 40:  # v4 read access token
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    url = f"https://api.themoviedb.org/3{path}?{urllib.parse.urlencode(params)}"
    return _json(url, headers)


def tmdb_search_movie(query: str, year: int | None, key: str) -> list[Candidate]:
    params = {"query": query, "include_adult": "false"}
    results = _tmdb("/search/movie", key, {**params, "year": year} if year else params)["results"]
    if year and not results:
        results = _tmdb("/search/movie", key, params)["results"]
    out = []
    for r in results[:10]:
        out.append(Candidate(
            source="tmdb", source_id=str(r["id"]), kind="movie", name=r.get("title") or "",
            year=_year(r.get("release_date")), overview=r.get("overview") or None,
            poster_url=TMDB_IMAGE + r["poster_path"] if r.get("poster_path") else None,
        ))
    return out


def tmdb_details(source_id: str, key: str) -> Candidate:
    r = _tmdb(f"/movie/{source_id}", key)
    return Candidate(
        source="tmdb", source_id=str(r["id"]), kind="movie", name=r.get("title") or "",
        year=_year(r.get("release_date")), overview=r.get("overview") or None,
        poster_url=TMDB_IMAGE + r["poster_path"] if r.get("poster_path") else None,
        imdb_id=r.get("imdb_id"), runtime=r.get("runtime") or None, status=r.get("status"),
    )


def tmdb_key_works(key: str) -> bool:
    try:
        _tmdb("/configuration", key)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ---- IMDb public suggestions + Wikipedia (no key) -----------------------

_IMDB_KINDS = {"movie": {"movie", "tvMovie", "video"}, "show": {"tvSeries", "tvMiniSeries"}}


def imdb_search(query: str, kind: str) -> list[Candidate]:
    q = re.sub(r"[^\w\s-]", " ", query.lower()).strip()
    if not q:
        return []
    first = q[0] if q[0].isalnum() else "x"
    data = _json(f"https://v3.sg.media-imdb.com/suggestion/{first}/{urllib.parse.quote(q)}.json")
    out = []
    for item in data.get("d", []):
        if not str(item.get("id", "")).startswith("tt"):
            continue
        if item.get("qid") not in _IMDB_KINDS[kind]:
            continue
        image = (item.get("i") or {}).get("imageUrl")
        if image:
            image = re.sub(r"\._V1_.*\.jpg$", "._V1_SX600.jpg", image)
        out.append(Candidate(source="imdb", source_id=item["id"], kind=kind, name=item.get("l") or "",
                             year=item.get("y"), poster_url=image, imdb_id=item["id"]))
    return out


def wikipedia_for_imdb(imdb_id: str) -> tuple[str | None, int | None]:
    """Description and runtime (minutes) via Wikidata's IMDb id property."""
    try:
        found = _json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": f"haswbstatement:P345={imdb_id}",
            "format": "json", "srlimit": 1}))
        hits = found.get("query", {}).get("search", [])
        if not hits:
            return None, None
        qid = hits[0]["title"]
        ent = _json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
            "action": "wbgetentities", "ids": qid, "props": "sitelinks|claims",
            "sitefilter": "enwiki", "format": "json"}))["entities"][qid]
        runtime = None
        for claim in ent.get("claims", {}).get("P2047", []):
            try:
                runtime = int(float(claim["mainsnak"]["datavalue"]["value"]["amount"]))
                break
            except (KeyError, ValueError, TypeError):
                continue
        page = ent.get("sitelinks", {}).get("enwiki", {}).get("title")
        if not page:
            return None, runtime
        summary = _json("https://en.wikipedia.org/api/rest_v1/page/summary/"
                        + urllib.parse.quote(page.replace(" ", "_"), safe=""))
        return summary.get("extract") or None, runtime
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        log.info("wikipedia lookup failed for %s: %s", imdb_id, exc)
        return None, None


def imdb_details(cand: Candidate) -> Candidate:
    overview, runtime = wikipedia_for_imdb(cand.source_id)
    cand.overview = cand.overview or overview
    cand.runtime = cand.runtime or runtime
    return cand


# ---- public API ---------------------------------------------------------

def search(kind: str, query: str, year: int | None = None, tmdb_key: str | None = None) -> list[Candidate]:
    """Candidates from every available source, best guess first."""
    results: list[Candidate] = []
    sources = []
    if kind == "show":
        sources = [lambda: tvmaze_search(query), lambda: imdb_search(query, "show")]
    else:
        if tmdb_key:
            sources.append(lambda: tmdb_search_movie(query, year, tmdb_key))
        sources.append(lambda: imdb_search(query, "movie"))
    for source in sources:
        try:
            found = source()
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            log.info("search failed for %r: %s", query, exc)
            continue
        for rank, c in enumerate(found):
            c.score = _score(query, year, c, rank)
        results.extend(found)
        if any(c.score >= 0.9 for c in found):
            break  # first source already has a confident match
    results.sort(key=lambda c: c.score, reverse=True)
    return results


def details(source: str, source_id: str, kind: str, tmdb_key: str | None = None,
            hint: Candidate | None = None) -> Candidate:
    if source == "tvmaze":
        return tvmaze_details(source_id)
    if source == "tmdb":
        if not tmdb_key:
            raise ValueError("TMDB key needed")
        return tmdb_details(source_id, tmdb_key)
    if source == "imdb":
        cand = hint
        if cand is None:
            for c in imdb_search(source_id, kind):
                if c.source_id == source_id:
                    cand = c
                    break
        if cand is None:
            cand = Candidate(source="imdb", source_id=source_id, kind=kind, name=source_id, imdb_id=source_id)
        return imdb_details(cand)
    raise ValueError(f"unknown source {source}")


def query_variants(query: str) -> list[str]:
    """'Dr. STONE - Stone Wars' -> also try 'Dr. STONE'; 'The Office US' -> 'The Office'."""
    out = [query]
    if " - " in query:
        out.append(query.split(" - ")[0])
    if ":" in query:
        out.append(query.split(":")[0])
    m = re.match(r"^(.*)\s(?:US|UK|AU|NZ|CA)$", query)
    if m:
        out.append(m.group(1))
    stripped = re.sub(r"\s+(?:season|part|cour)\s*\d+$", "", query, flags=re.I)
    if stripped != query:
        out.append(stripped)
    seen, uniq = set(), []
    for q in out:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return uniq


MIN_SCORE = 0.72


def _has_episode(cand: Candidate, season: int | None, episode: int | None) -> bool:
    if episode is None:
        return True
    if season is not None:
        return any(e[0] == season and e[1] == episode for e in cand.episodes)
    return len([e for e in cand.episodes if e[0] > 0]) >= episode


def best_match(kind: str, query: str, year: int | None = None, tmdb_key: str | None = None,
               season: int | None = None, episode: int | None = None,
               anime: bool = False) -> Candidate | None:
    """Pick the best title. For shows, prefer one that actually has the episode we saw."""
    found: dict[tuple, Candidate] = {}
    for variant in query_variants(query):
        for cand in search(kind, variant, year, tmdb_key)[:5]:
            if anime and cand.anime:
                cand.score += 0.05
            key = (cand.source, cand.source_id)
            if key not in found or cand.score > found[key].score:
                found[key] = cand
        if any(c.score >= 0.95 for c in found.values()):
            break
    ranked = sorted(found.values(), key=lambda c: c.score, reverse=True)
    ranked = [c for c in ranked if c.score >= MIN_SCORE]
    if not ranked:
        return None
    close = [c for c in ranked if c.score >= ranked[0].score - 0.1][:4]
    if kind == "show" and episode is not None and len(close) > 1:
        fallback = None
        for cand in close:
            try:
                full = details(cand.source, cand.source_id, kind, tmdb_key, hint=cand)
            except (urllib.error.URLError, OSError, ValueError, KeyError):
                continue
            if fallback is None:
                fallback = full
            if full.episodes and _has_episode(full, season, episode):
                return full
        return fallback
    best = ranked[0]
    return details(best.source, best.source_id, kind, tmdb_key, hint=best)
