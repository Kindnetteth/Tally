"""Turn messy video file names into a title, year, season and episode.

    >>> parse("Show.Name.S02E05.1080p.WEB-DL.mkv")
    ParsedName(title='Show Name', kind='show', season=2, episode=5, ...)

Only the standard library is used. The parser looks at the file name first
and falls back to the parent folders ("Show/Season 2/05 - Pilot.mkv").
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, asdict
from pathlib import PurePosixPath, PureWindowsPath

VIDEO_EXTENSIONS = {
    "3g2", "3gp", "asf", "avi", "divx", "f4v", "flv", "m2ts", "m4v", "mk3d",
    "mkv", "mov", "mp4", "mpeg", "mpg", "mts", "ogm", "ogv", "rm", "rmvb",
    "ts", "vob", "webm", "wmv", "iso",
}

# Words that mark the end of the real title in a release name.
_JUNK = r"""
    \d{3,4}p | \d{3,4}i | 4k | uhd | 8k | hdr10\+? | hdr | dolby[ .-]?vision | sdr |
    blu[ .-]?ray | bdrip | brrip | bdremux | remux | web[ .-]?dl | web[ .-]?rip | webrip |
    hdtv | pdtv | sdtv | dvdrip | dvdscr | dvd | dvd5 | dvd9 | hdrip | hdcam | camrip | ts[ .-]?rip |
    telesync | screener | r5 | vhsrip | tvrip | satrip |
    x26[45] | h[ .]?26[45] | hevc | avc | av1 | vp9 | xvid | divx | mpeg2 | 10bit | 8bit | hi10p? |
    aac(?:2[ .]0|5[ .]1)? | ac3 | eac3 | dd[p+]?(?:2[ .]0|5[ .]1|7[ .]1)? | dts(?:-?hd)? | truehd | flac |
    5[ .]1 | 7[ .]1 | 2[ .]0 |
    proper | repack | rerip | internal | limited | extended | unrated | uncut | remastered |
    imax | directors[ .]?cut | theatrical | criterion | dubbed | subbed | dual[ .-]?audio |
    multisub | hardsub | softsub | amzn | dsnp | hmax | atvp | hulu | pcok | itunes
"""
_JUNK_RE = re.compile(r"(?<![a-z0-9])(?:" + _JUNK + r")(?![a-z0-9])", re.I | re.X)

_GENERIC_NAMES = {"movie", "video", "film", "sample", "trailer", "feature", "main", "title", "t00", "title_t00"}

_IGNORED_FOLDERS = {
    "downloads", "download", "desktop", "documents", "movies", "movie", "films", "videos", "video",
    "tv", "tv shows", "tvshows", "shows", "series", "anime", "media", "complete", "torrents",
    "users", "home", "volumes", "mnt", "media library", "plex", "jellyfin", "public", "new folder",
}

_SEASON_DIR_RE = re.compile(r"^(?:season|series|staffel|saison|temporada|s)[ ._-]*(\d{1,2})\b", re.I)
_SPECIALS_DIR_RE = re.compile(r"^(?:specials?|extras?|featurettes?|bonus)$", re.I)

_PATTERNS = [
    # S01E02, S01E02E03, S01E02-E03, S01E02-03, s01.e02, S01 E02
    ("se", re.compile(
        r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,4})(?:(?:[ ._-]?e|-)(\d{1,4}))?(?![0-9])", re.I)),
    # 1x02, 01x02-03
    ("x", re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?:-(\d{2,3}))?(?![0-9a-z])", re.I)),
    # Season 1 Episode 2, Season 1 - Ep 2, S1 Ep2
    ("words", re.compile(
        r"(?<![a-z0-9])(?:season|series|s)[ ._-]*(\d{1,2})[ ._,-]*(?:episode|ep|e)[ ._-]*(\d{1,4})(?![0-9])", re.I)),
    # 2024.03.15 daily shows
    ("date", re.compile(r"(?<![0-9])((?:19|20)\d\d)[ ._-](0[1-9]|1[0-2])[ ._-](0[1-9]|[12]\d|3[01])(?![0-9])")),
    # Episode 5, Ep 05, Ep.05, E05 (no season)
    ("ep", re.compile(r"(?<![a-z0-9])(?:episode|ep|e)[ ._-]?(\d{1,4})(?![0-9a-z])", re.I)),
    # Anime: "Title - 04", "Title - 04v2", "Title - 1050"
    ("dash", re.compile(r"\s-\s(\d{1,4})(?:v\d)?(?=\s|$)", re.I)),
]

_YEAR_RE = re.compile(r"(?<![0-9])((?:19|20)\d\d)(?![0-9])")


@dataclass
class ParsedName:
    title: str
    kind: str  # "show" or "movie"
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    air_date: str | None = None  # YYYY-MM-DD for daily shows
    episode_title: str | None = None
    filename: str = ""
    anime: bool = False  # "[Group] Title - 05" style release
    loose: bool = False  # episode number may be absolute (season not written next to it)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def key(self) -> str:
        """Stable lookup key so the same show from different files matches once."""
        return f"{self.kind}:{normalize(self.title)}:{self.year or ''}"


def normalize(text: str) -> str:
    """Lowercase, drop punctuation and a leading 'the' for comparing titles."""
    text = text.lower().replace("&", " and ")
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = re.sub(r"^the ", "", text)
    return text


def _split_path(path: str) -> list[str]:
    if "\\" in path or re.match(r"^[a-zA-Z]:", path):
        parts = PureWindowsPath(path).parts
    else:
        parts = PurePosixPath(path).parts
    return [p for p in parts if p not in ("/", "\\")]


def _strip_extension(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    if dot and ext.lower() in VIDEO_EXTENSIONS:
        return stem
    return name


def _to_spaces(name: str) -> str:
    """Release names use dots or underscores as spaces; real names use spaces."""
    name = name.replace("_", " ")
    dots = len(re.findall(r"\.(?=\S)", name))
    if dots > name.count(" "):
        # Keep dots in acronyms like S.H.I.E.L.D. but split Word.Word
        name = re.sub(r"(?<=[A-Za-z0-9]{2})\.|\.(?=[A-Za-z0-9]{2})", " ", name)
        name = re.sub(r"(?<=\d)\.(?=\d)", " ", name)  # 2024.03.15
    return re.sub(r"\s+", " ", name).strip()


def _remove_brackets(name: str) -> tuple[str, bool]:
    leading_group = bool(re.match(r"^\s*[\[【]", name))
    # "(CBB) Show - 01": a release group in parentheses, not part of the title
    m = re.match(r"^\s*\(([^)]{1,20})\)\s*(?=\S)", name)
    if m and not _YEAR_RE.fullmatch(m.group(1).strip()):
        name, leading_group = name[m.end():], True
    name = re.sub(r"\[[^\]]*\]|【[^】]*】|\{[^}]*\}", " ", name)
    return re.sub(r"\s+", " ", name).strip(), leading_group


def _remove_site_prefix(name: str) -> str:
    return re.sub(r"^(?:www\.)?[a-z0-9-]+\.(?:com|org|net|to|tv|me|cc|io|ws|se)\s*[-–]\s*", "", name, flags=re.I)


def _cut_junk(text: str) -> str:
    m = _JUNK_RE.search(text)
    if m and m.start() > 0:
        text = text[: m.start()]
    return text


# Edition words that aren't part of a show's name: "Re:ZERO ... Director's Cut"
_EDITION_RE = re.compile(r"\s*\b(?:director'?s cut|uncut|uncensored|extended(?: edition| cut)?|remastered|"
                         r"re-?edit(?:ed)?|special edition)\s*$", re.I)


def _clean_title(text: str) -> str:
    text = _EDITION_RE.sub("", text)
    text = re.sub(r"\((?:[^)]*)\)\s*$", lambda m: m.group(0) if _YEAR_RE.fullmatch(m.group(0)[1:-1].strip()) else "", text)
    text = text.replace("(", " ").replace(")", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" -–—_.,:;+~#")
    return text


def _clean_episode_title(text: str) -> str | None:
    text = _cut_junk(text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—_.,:;+~#")
    if not text or re.fullmatch(r"[\d\s]+", text) or len(text) < 2:
        return None
    # "-GROUP" release tags at the end
    if re.fullmatch(r"[A-Z0-9]{2,}", text) and len(text) <= 8:
        return None
    return text


def _split_year(title: str) -> tuple[str, int | None]:
    """Pull a trailing year out of a title: 'Doctor Who 2005' -> ('Doctor Who', 2005)."""
    m = re.search(r"[\s(]((?:19|20)\d\d)\)?$", title)
    if m and _plausible_year(int(m.group(1))) and m.start() > 0:
        return _clean_title(title[: m.start()]), int(m.group(1))
    return title, None


def _plausible_year(year: int) -> bool:
    return 1900 <= year <= _dt.date.today().year + 2


def _folder_context(parts: list[str]) -> tuple[str | None, int | None]:
    """Show name and season from parent folders, if they look like a library."""
    folders = parts[:-1]
    season = None
    show = None
    for i in range(len(folders) - 1, -1, -1):
        raw = folders[i]
        cleaned, _ = _remove_brackets(_to_spaces(raw))
        m = _SEASON_DIR_RE.match(cleaned)
        if m and season is None and i == len(folders) - 1:
            season = int(m.group(1))
            continue
        if _SPECIALS_DIR_RE.match(cleaned) and i == len(folders) - 1:
            season = 0
            continue
        # Complete-series packs number their season folders: "02. Tokyo Ghoul √A"
        m = re.match(r"^(\d{1,2})\.\s+\S", raw)
        if (m and season is None and i == len(folders) - 1 and i > 0
                and re.search(r"\b(?:complete|collection|batch|seasons)\b", folders[i - 1], re.I)):
            season = int(m.group(1))
            cleaned = cleaned[m.end() - 1:]
        candidate = _clean_title(_cut_junk(_remove_site_prefix(cleaned)))
        # Season packs: "Show.Name.S02.1080p" folders
        candidate = re.split(r"(?<![a-z0-9])s\d{1,2}(?![0-9])|\bseason\s*\d", candidate, flags=re.I)[0]
        candidate = _clean_title(candidate)
        if candidate and not re.fullmatch(r"\d+", candidate) and normalize(candidate) not in _IGNORED_FOLDERS:
            show = candidate
        break
    return show, season


_TITLE_SEASON_RE = re.compile(r"^(.+?)\s+(?:(\d{1,2})(?:st|nd|rd|th)\s+season|season\s+(\d{1,2}))$", re.I)


def _season_from_title(title: str) -> tuple[str, int | None]:
    """'Dandadan 2nd Season' -> ('Dandadan', 2), as anime releases often name later seasons."""
    m = _TITLE_SEASON_RE.match(title)
    if not m:
        return title, None
    return m.group(1), int(m.group(2) or m.group(3))


def _usable_title(title: str) -> bool:
    return bool(title) and normalize(title) not in _GENERIC_NAMES and not re.fullmatch(r"[\d\s]*", title)


def parse(path: str) -> ParsedName:
    parts = _split_path(path) or [path]
    filename = parts[-1]
    folder_show, folder_season = _folder_context(parts)

    name = _strip_extension(filename)
    name = _remove_site_prefix(name)
    name, anime_style = _remove_brackets(name)
    name = _to_spaces(name)

    for kind, pattern in _PATTERNS:
        if kind == "dash":
            matches = list(pattern.finditer(name))
            m = matches[-1] if matches else None
        else:
            m = pattern.search(name)
        if not m:
            continue
        # A lone "E05"/"Ep 5" needs a title before it to be trusted
        title = _clean_title(name[: m.start()])
        after = name[m.end():]
        season = episode = episode_end = None
        air_date = None
        if kind in ("se", "x"):
            season, episode = int(m.group(1)), int(m.group(2))
            episode_end = int(m.group(3)) if m.group(3) else None
        elif kind == "words":
            season, episode = int(m.group(1)), int(m.group(2))
        elif kind == "date":
            air_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        else:
            episode = int(m.group(1))
            season = folder_season
            if kind == "ep" and any(_plausible_year(int(y)) for y in _YEAR_RE.findall(after)):
                continue  # "Star Wars Episode 4 1977" is a movie
            if kind == "dash" and episode >= 1900 and _plausible_year(episode) and not anime_style:
                continue  # "Movie - 2010" is a year, not episode 2010
        title, year = _split_year(title)
        if not _usable_title(title):
            title, year = _split_year(folder_show) if folder_show else ("", None)
        if not title:
            continue
        loose = kind in ("ep", "dash")
        title, title_season = _season_from_title(title)
        if title_season is not None and season is None:
            season, loose = title_season, False  # the number counts within that season
        if episode_end is not None and episode_end <= (episode or 0):
            episode_end = None
        return ParsedName(
            title=title, kind="show", year=year, season=season, episode=episode,
            episode_end=episode_end, air_date=air_date,
            episode_title=_clean_episode_title(after), filename=filename, anime=anime_style,
            loose=loose,
        )

    # Anime batches without a dash: "[Group] Title 05 [1080p]"
    if anime_style:
        m = re.search(r"\s(\d{1,4})(?:v\d)?$", _cut_junk(name).strip())
        if m:
            title = _clean_title(_cut_junk(name)[: m.start()])
            if _usable_title(title):
                return ParsedName(title=title, kind="show", season=folder_season,
                                  episode=int(m.group(1)), filename=filename, anime=True, loose=True)

    # Inside a "Season 2" folder with a bare number: "05 - Pilot.mkv", "05.mkv"
    if folder_season is not None and folder_show:
        m = re.match(r"^(\d{1,3})(?![0-9])(?:\s*[-.]\s*|\s+|$)(.*)$", name)
        if m:
            show, show_year = _split_year(folder_show)
            return ParsedName(title=show, year=show_year, kind="show", season=folder_season,
                              episode=int(m.group(1)),
                              episode_title=_clean_episode_title(m.group(2)), filename=filename)

    # Movie: title is everything before the last plausible year
    year = None
    title = name
    years = [m for m in _YEAR_RE.finditer(name) if _plausible_year(int(m.group(1)))]
    years = [m for m in years if m.start() > 0]
    if years:
        m = years[-1]
        before = name[: m.start()]
        if _clean_title(before) and normalize(before) not in _GENERIC_NAMES:
            year = int(m.group(1))
            title = before
    title = _clean_title(_cut_junk(title))
    # Trailing "-GROUP" release tag when there was no junk word to cut at
    title = re.sub(r"\s*-\s*[A-Za-z0-9]+$", "", title) if re.search(r"\S-[A-Za-z0-9]+$", title) else title
    if (not title or normalize(title) in _GENERIC_NAMES) and folder_show:
        title, folder_year = _split_year(folder_show)
        year = year or folder_year
    return ParsedName(title=title or filename, kind="movie", year=year, filename=filename)


def is_video_path(path: str) -> bool:
    parts = _split_path(path) if path else []
    name = parts[-1] if parts else ""
    return "." in name and name.rsplit(".", 1)[1].lower() in VIDEO_EXTENSIONS


RELEASE_TAGS = re.compile(
    r"\b(?:480p|576p|720p|1080p|2160p|uhd|hdr|hdr10|blu ?ray|bdrip|brrip|bd|bdremux|remux|web ?dl|webrip|"
    r"hdtv|dvdrip|dcp|x264|x265|h ?264|h ?265|hevc|av1|xvid|10 ?bit|aac\d?|ddp?\d|flac\d?|dts|truehd|"
    r"dual ?audio|multi ?subs?|eng ?subs?)\b", re.I)
EPISODE_TAG = re.compile(r"\bs\d{1,2} ?e\d{1,3}\b", re.I)
# Anime-style numbering with no SxxExx: "Re Zero Season 02 - 01", "Show Season 2 Episode 5"
SEASON_EPISODE_TAG = re.compile(
    r"(?<![a-z0-9])season[ ._-]*\d{1,2}[^a-z0-9]*(?:episode|ep|e)?[ ._-]*\d{1,4}(?:v\d)?(?![0-9a-z])",
    re.I)
EXTRAS = re.compile(r"\b(?:nc ?op|nc ?ed|extras?|specials? features?|featurettes?|trailers?|samples?|bonus)\b", re.I)


def _words(text: str) -> str:
    return re.sub(r"[._\-\[\](){}+]+", " ", text)


def looks_like_release(path: str) -> bool:
    """True for a show or movie, False for music, your own clips and everything else.

    Downloaded shows and movies carry an S01E02 marker or release tags (1080p,
    x265, WEBRip...) in the file or folder name. A library named the Plex way,
    "Movie (2010).mkv" or "Show/Season 2/05.mkv", counts too.
    """
    p = PurePosixPath(path.replace("\\", "/"))
    name, folder = _words(p.stem), _words(p.parent.name)
    if EXTRAS.search(name) or EXTRAS.search(folder):
        return False  # creditless openings, trailers and other disc extras
    if (EPISODE_TAG.search(name) or SEASON_EPISODE_TAG.search(name)
            or RELEASE_TAGS.search(name) or RELEASE_TAGS.search(folder)):
        return True
    if re.search(r"\((?:19|20)\d\d\)", p.stem + " " + p.parent.name):
        return True
    # Bracket prefixes like "[Group] Show Season 2" still count as a season folder
    parent, _ = _remove_brackets(_to_spaces(p.parent.name))
    return bool(_SEASON_DIR_RE.match(parent) and re.match(r"\d{1,3}\b", p.stem))
