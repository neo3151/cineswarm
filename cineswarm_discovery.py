#!/usr/bin/env python3
"""Collection-aware discovery queue with Radarr/Sonarr validation.

Default path is Gemini when CINESWARM_MODEL_PROVIDER=gemini. Watch-taste
Radarr lookups top up the queue if Gemini is unset, denied, or thin.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from typing import Any

from cineswarm_agents import AgentError, HostedModelClient, PlaybackHistory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTROL_DB = os.environ.get("CINESWARM_CONTROL_DB", os.path.join(BASE_DIR, "cineswarm_control.db"))
CATALOG_DB = os.environ.get("CINESWARM_CATALOG_DB", os.path.join(BASE_DIR, "cineswarm_catalog.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id INTEGER PRIMARY KEY,
    candidate_key TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL CHECK(media_type IN ('movie','series')),
    title TEXT NOT NULL,
    year INTEGER,
    external_ids_json TEXT NOT NULL DEFAULT '{}',
    genres_json TEXT NOT NULL DEFAULT '[]',
    score REAL NOT NULL DEFAULT 0,
    watch_affinity_score REAL NOT NULL DEFAULT 0,
    collection_significance_score REAL NOT NULL DEFAULT 0,
    rarity_preservation_score REAL NOT NULL DEFAULT 0,
    storage_cost_score REAL NOT NULL DEFAULT 0,
    acquisition_confidence_score REAL NOT NULL DEFAULT 0,
    overall_score REAL NOT NULL DEFAULT 0,
    score_reasons_json TEXT NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    source TEXT NOT NULL DEFAULT 'gemini',
    raw_json TEXT NOT NULL DEFAULT '{}',
    edition TEXT,
    quality TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discovery_queue ON discovery_candidates(status, score DESC);
"""


def timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


BOXSET_TITLE_RE = re.compile(
    r"\bdisc\s*\d|\bdisk\s*\d|\bbox[\s-]?set\b|\bcollection volume\b|\bvolume\s+\d+\s+disc\b|\bthe mike judge collection\b",
    re.I,
)


def is_boxset_title(title: str | None) -> bool:
    """True for disc/volume box-set listings that should not enter discovery or recovery."""
    return bool(BOXSET_TITLE_RE.search(str(title or "")))


JUNK_SERIES_TITLE_RE = re.compile(
    r"\b(complete\s+collection|blu-?ray|dvd(\s+set)?|season\s+pack|the\s+complete\s+series|discography)\b",
    re.I,
)

TV_GROWTH_PREFERRED_GENRES = {"comedy", "animation", "sitcom"}
TV_GROWTH_PREFERRED_NETWORKS = (
    "adult swim",
    "cartoon network",
    "comedy central",
    "fox",
    "fxx",
    "fx",
)
TV_GROWTH_TASTE_SEEDS = (
    "Rick and Morty",
    "American Dad!",
    "King of the Hill",
    "Bob's Burgers",
    "The Simpsons",
    "South Park",
    "Archer",
    "Solar Opposites",
    "Robot Chicken",
    "Aqua Teen Hunger Force",
    "The Venture Bros.",
    "Home Movies",
    "Metalocalypse",
    "Harvey Birdman, Attorney at Law",
    "Squidbillies",
    "Moral Orel",
    "Superjail!",
    "Sealab 2021",
    "The Boondocks",
    "Mike Tyson Mysteries",
    "Smiling Friends",
    "Primal",
    "Harley Quinn",
    "Invincible",
    "BoJack Horseman",
    "F Is for Family",
    "Disenchantment",
    "Close Enough",
    "Tuca & Bertie",
    "Clone High",
    "Daria",
    "Beavis and Butt-Head",
    "It's Always Sunny in Philadelphia",
    "Community",
    "Parks and Recreation",
    "What We Do in the Shadows",
    "Atlanta",
    "Barry",
    "Curb Your Enthusiasm",
    "Arrested Development",
    "30 Rock",
    "Veep",
    "Silicon Valley",
    "Workaholics",
    "Broad City",
    "I Think You Should Leave with Tim Robinson",
    "Trailer Park Boys",
    "The League",
    "Eastbound & Down",
    "Abbott Elementary",
    "Hacks",
    "The Good Place",
    "Schitt's Creek",
    "Brooklyn Nine-Nine",
    "The Office",
    "Mythic Quest",
    "Reservation Dogs",
    "Joe Pera Talks With You",
    "The Cleveland Show",
    "Futurama",
    "Family Guy",
)


def is_junk_series_title(title: str | None) -> bool:
    """True for disc/box-set/complete-collection listings that should not be added as series."""
    text = str(title or "")
    return is_boxset_title(text) or bool(JUNK_SERIES_TITLE_RE.search(text))


def series_matches_tv_taste(genres: Any = None, network: str | None = None, studio: str | None = None) -> bool:
    names = {str(genre).strip().lower() for genre in (genres or []) if genre}
    if names & TV_GROWTH_PREFERRED_GENRES:
        return True
    blob = f"{network or ''} {studio or ''}".lower()
    return any(token in blob for token in TV_GROWTH_PREFERRED_NETWORKS)


def uses_taste_backend() -> bool:
    backend = (os.environ.get("CINESWARM_DISCOVERY_BACKEND") or os.environ.get("CINESWARM_MODEL_PROVIDER") or "gemini").strip().lower()
    return backend in {"taste", "local", "radarr", "catalog", "off", "none", "disabled"}


def parse_json(text: str) -> list[dict[str, Any]]:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", candidate, re.S)
        if not match:
            raise AgentError("Discovery model returned no JSON candidate list")
        parsed = json.loads(match.group(0))
    if isinstance(parsed, dict):
        for val in parsed.values():
            if isinstance(val, list):
                parsed = val
                break
    if not isinstance(parsed, list):
        raise AgentError("Discovery model response must be a JSON list")
    return [item for item in parsed if isinstance(item, dict)]


class DiscoveryEngine:
    def __init__(self, store: Any, planner: Any, tools: Any, plex_url: str = "", plex_token: str = ""):
        self.store = store
        self.planner = planner
        self.tools = tools
        self.model = HostedModelClient()
        self.playback_history = None
        if plex_url and plex_token:
            self.playback_history = PlaybackHistory(plex_url, plex_token)
        with sqlite3.connect(CONTROL_DB) as connection:
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(discovery_candidates)")}
            migrations = {
                "edition": "TEXT",
                "quality": "TEXT",
                "watch_affinity_score": "REAL NOT NULL DEFAULT 0",
                "collection_significance_score": "REAL NOT NULL DEFAULT 0",
                "rarity_preservation_score": "REAL NOT NULL DEFAULT 0",
                "storage_cost_score": "REAL NOT NULL DEFAULT 0",
                "acquisition_confidence_score": "REAL NOT NULL DEFAULT 0",
                "overall_score": "REAL NOT NULL DEFAULT 0",
                "score_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    connection.execute(f"ALTER TABLE discovery_candidates ADD COLUMN {column} {definition}")
            connection.execute("UPDATE discovery_candidates SET overall_score=score WHERE overall_score=0 AND score!=0")
        try:
            self.backfill_scores()
        except Exception:
            pass


    def backfill_scores(self) -> int:
        """Backfill component scores for candidates missing affinity scores."""
        return self.rescore_open_candidates(only_missing_affinity=True)

    def rescore_open_candidates(self, only_missing_affinity: bool = False, statuses: tuple[str, ...] = ("new", "approved")) -> int:
        """Recompute scores for open discovery candidates from the live taste profile."""
        profile = self.taste_profile()
        updated_count = 0
        status_clause = ",".join("?" for _ in statuses) or "'new'"
        with sqlite3.connect(CONTROL_DB) as connection:
            connection.row_factory = sqlite3.Row
            query = f"SELECT id, raw_json, rationale, edition, overall_score, score, status FROM discovery_candidates WHERE status IN ({status_clause})"
            params: list[Any] = list(statuses)
            if only_missing_affinity:
                query += " AND watch_affinity_score=0 AND (overall_score>0 OR score>0)"
            rows = connection.execute(query, params).fetchall()
            for row in rows:
                try:
                    candidate = json.loads(row["raw_json"] or "{}")
                except json.JSONDecodeError:
                    candidate = {}
                candidate["edition"] = row["edition"]
                candidate["reason"] = row["rationale"]
                scores = self._score_components(candidate, profile)
                overall = scores["overall_score"]
                if only_missing_affinity:
                    overall = row["overall_score"] or row["score"] or overall
                connection.execute(
                    """
                    UPDATE discovery_candidates
                    SET score=?, watch_affinity_score=?, collection_significance_score=?, rarity_preservation_score=?, storage_cost_score=?, acquisition_confidence_score=?, overall_score=?, score_reasons_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (overall, scores["watch_affinity_score"], scores["collection_significance_score"], scores["rarity_preservation_score"], scores["storage_cost_score"], scores["acquisition_confidence_score"], overall, json.dumps(scores["score_reasons"], sort_keys=True), timestamp(), row["id"]),
                )
                updated_count += 1
        return updated_count

    def _decision_feedback(self) -> list[dict[str, Any]]:
        try:
            with sqlite3.connect(f"file:{CONTROL_DB}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute("SELECT f.sentiment, f.note, d.category, d.subject, d.decision, d.reasons_json, f.created_at FROM decision_feedback f JOIN decision_log d ON d.decision_id=f.decision_id ORDER BY f.created_at DESC LIMIT 50").fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            return []

    def _active_family_profile(self) -> dict[str, Any]:
        try:
            with sqlite3.connect(f"file:{CONTROL_DB}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute("SELECT * FROM user_family_profiles WHERE is_active=1 LIMIT 1").fetchone()
            if not row:
                return {}
            return {
                "profile_id": row["profile_id"],
                "allowed_ratings": json.loads(row["allowed_ratings_json"] or "[]"),
                "taste_weights": {str(key).casefold(): float(value) for key, value in json.loads(row["taste_weights_json"] or "{}").items()},
            }
        except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError):
            return {}

    def _learned_taste_profile(self) -> dict[str, float]:
        """Load Plex-webhook RL genre weights from user_taste_memory."""
        try:
            with sqlite3.connect(f"file:{CONTROL_DB}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT value_json FROM user_taste_memory WHERE key='learned_taste_profile'"
                ).fetchone()
            if not row or not row[0]:
                return {}
            payload = json.loads(row[0])
            if not isinstance(payload, dict):
                return {}
            learned: dict[str, float] = {}
            for key, value in payload.items():
                try:
                    learned[str(key).strip().casefold()] = float(value)
                except (TypeError, ValueError):
                    continue
            return learned
        except (sqlite3.Error, json.JSONDecodeError, TypeError):
            return {}

    def taste_profile(self) -> dict[str, Any]:
        learned = self._learned_taste_profile()
        # Try to use playback history for weighted taste profile
        if self.playback_history:
            try:
                pb = self.playback_history.build_taste_profile()
                if pb.get("total_watched", 0) > 0:
                    titles = []
                    with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
                        titles = [f"{row[0]} ({row[1] or 'n.d.'})" for row in connection.execute("SELECT title, year FROM catalog_items WHERE present=1 ORDER BY title LIMIT 150")]
                    profile = {**pb, "existing_titles_sample": titles, "decision_feedback": self._decision_feedback(), "learned_taste_profile": {**learned, **self._learned_taste_profile()}, "family_profile": self._active_family_profile(), "source": "playback_history"}
                    self._persist_watch_first(profile)
                    profile["learned_taste_profile"] = self._learned_taste_profile() or profile["learned_taste_profile"]
                    profile["decision_feedback"] = self._decision_feedback()
                    return profile
            except Exception:
                pass  # Fall back to catalog-based

        # Fallback: catalog-based taste profile
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            genres = Counter()
            decades = Counter()
            for genres_json, year in connection.execute("SELECT genres_json, year FROM catalog_items WHERE present=1"):
                try:
                    for genre in json.loads(genres_json or "[]"):
                        genres[genre] += 1
                except json.JSONDecodeError:
                    pass
                if year:
                    decades[f"{year // 10 * 10}s"] += 1
        titles = []
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            titles = [f"{row[0]} ({row[1] or 'n.d.'})" for row in connection.execute("SELECT title, year FROM catalog_items WHERE present=1 ORDER BY title LIMIT 150")]
        return {"top_genres": genres.most_common(12), "top_decades": decades.most_common(8), "existing_titles_sample": titles, "decision_feedback": self._decision_feedback(), "learned_taste_profile": learned, "family_profile": self._active_family_profile(), "source": "catalog"}
    def _existing_keys(self) -> set[str]:
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            keys = {row[0] for row in connection.execute("SELECT source_id FROM catalog_items WHERE present=1")}
        store = getattr(self, "store", None)
        snapshot = store.snapshot_payload("plex") if store else None
        for item in (snapshot or {}).get("items", []):
            provider_ids = {str(key).lower(): value for key, value in (item.get("ProviderIds") or {}).items()}
            if item.get("Type") == "Movie" and provider_ids.get("tmdb"):
                keys.add(f"tmdb:{provider_ids['tmdb']}")
            elif item.get("Type") == "Series" and provider_ids.get("tvdb"):
                keys.add(f"tvdb:{provider_ids['tvdb']}")
        return keys

    def _existing_titles(self) -> set[str]:
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            titles = {str(row[0]).strip().casefold() for row in connection.execute("SELECT title FROM catalog_items WHERE present=1") if row[0]}
        store = getattr(self, "store", None)
        snapshot = store.snapshot_payload("plex") if store else None
        titles.update(str(item.get("Name")).strip().casefold() for item in (snapshot or {}).get("items", []) if item.get("Name"))
        return titles

    @staticmethod
    def _stable_key(media_type: str, external_ids: dict[str, Any]) -> str | None:
        if media_type == "movie":
            value = external_ids.get("tmdbId") or external_ids.get("tmdb")
            return f"tmdb:{value}" if value else None
        value = external_ids.get("tvdbId") or external_ids.get("tvdb")
        return f"tvdb:{value}" if value else None

    @staticmethod
    def _select_match(matches: list[dict[str, Any]], title: str, year: Any = None) -> dict[str, Any] | None:
        normalized_title = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
        exact_title = [item for item in matches if re.sub(r"[^a-z0-9]+", " ", str(item.get("title", "")).casefold()).strip() == normalized_title]
        if year not in (None, ""):
            try:
                expected_year = int(year)
            except (TypeError, ValueError):
                return None
            return next((item for item in exact_title if item.get("year") == expected_year), None)
        return exact_title[0] if exact_title else None
    
    def _existing_edition_keys(self) -> set[str]:
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT source_id, raw_json FROM catalog_items WHERE present=1").fetchall()
            result = set()
            for row in rows:
                raw = json.loads(row["raw_json"] or "{}")
                from cineswarm_agents import ReadOnlyTools
                edition = ReadOnlyTools._extract_edition(raw) or ReadOnlyTools._extract_quality(raw)
                if edition:
                    result.add(f"{row['source_id']}|{str(edition).strip().casefold()}")
            return result
    
    def _extract_edition(self, item: dict[str, Any]) -> str | None:
        """Extract edition/cut information from title or edition field."""
        title = item.get("title", "") or item.get("Title", "") or ""
        edition_field = item.get("edition", "") or item.get("Edition", "") or item.get("EditionName", "") or ""
        
        edition_patterns = [
            r"\b(director['\s]?s?\s+cut)\b",
            r"\b(extended\s+(?:edition|cut))\b",
            r"\b(theatrical\s+(?:edition|cut))\b",
            r"\b(remastered)\b",
            r"\b(anniversary\s+edition)\b",
            r"\b(collector['\s]?s?\s+edition)\b",
            r"\b(special\s+edition)\b",
            r"\b(ultimate\s+edition)\b",
            r"\b(uncut)\b",
            r"\b(unrated)\b",
            r"\b(4k|uhd)\b",
            r"\b(1080p)\b",
            r"\b(720p)\b",
            r"\b(hdr)\b",
            r"\b(dolby\s+vision)\b",
        ]
        
        import re
        found = []
        for pattern in edition_patterns:
            matches = re.findall(pattern, title, re.IGNORECASE)
            if matches:
                found.extend(matches)
        if edition_field:
            found.append(edition_field)
        
        if found:
            for f in found:
                if f:
                    return f.strip()
        return None
    
    def _extract_quality(self, item: dict[str, Any]) -> str | None:
        quality = item.get("Quality", {}) or {}
        quality_name = quality.get("quality", {}).get("name", "") if isinstance(quality.get("quality"), dict) else ""
        movie_file = item.get("movieFile", {}) or {}
        mf_quality = movie_file.get("quality", {}) or {}
        mf_quality_name = mf_quality.get("quality", {}).get("name", "") if isinstance(mf_quality.get("quality"), dict) else ""
        return quality_name or mf_quality_name or None

    @staticmethod
    def _bounded(value: Any) -> float:
        try:
            return round(max(0.0, min(100.0, float(value))), 2)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _relative_weights(pairs: Any) -> dict[str, float]:
        weights: dict[str, float] = {}
        for item in pairs or []:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            key = str(item[0] or "").strip()
            if not key:
                continue
            try:
                value = float(item[1])
            except (TypeError, ValueError):
                continue
            if value > 0:
                weights[key] = weights.get(key, 0.0) + value
        total = sum(weights.values()) or 1.0
        return {key: value / total for key, value in weights.items()}

    def _persist_watch_first(self, profile: dict[str, Any]) -> None:
        """Store compact Plex-watch preferences and seed learned genre weights."""
        if profile.get("source") != "playback_history":
            return
        top_genres = list(profile.get("top_genres") or [])[:8]
        shares = self._relative_weights(top_genres)
        now = timestamp()
        compact = {
            "source": "playback_history",
            "top_genres": top_genres,
            "top_decades": list(profile.get("top_decades") or [])[:6],
            "top_directors": list(profile.get("top_directors") or [])[:5],
            "total_watched": profile.get("total_watched"),
            "updated_at": now,
        }
        try:
            with sqlite3.connect(CONTROL_DB) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS user_taste_memory (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT OR REPLACE INTO user_taste_memory (key, value_json, updated_at) VALUES (?, ?, ?)",
                    ("watch_first_profile", json.dumps(compact, sort_keys=True), now),
                )
                row = connection.execute("SELECT value_json FROM user_taste_memory WHERE key='learned_taste_profile'").fetchone()
                learned = {}
                if row and row[0]:
                    try:
                        parsed = json.loads(row[0])
                        if isinstance(parsed, dict):
                            learned = parsed
                    except json.JSONDecodeError:
                        learned = {}
                changed = False
                for genre, share in shares.items():
                    key = genre.casefold()
                    target = round(1.0 + min(1.8, share * 3.0), 2)
                    current = float(learned.get(key) or learned.get(genre) or 1.0)
                    if target > current:
                        learned[key] = target
                        changed = True
                if changed:
                    connection.execute(
                        "INSERT OR REPLACE INTO user_taste_memory (key, value_json, updated_at) VALUES (?, ?, ?)",
                        ("learned_taste_profile", json.dumps(learned, sort_keys=True), now),
                    )
        except sqlite3.Error:
            return
        self._seed_implicit_watch_feedback(profile)

    def _seed_implicit_watch_feedback(self, profile: dict[str, Any]) -> None:
        """One durable good-feedback row from Plex watch genres so scoring is not catalog-shaped."""
        if self._decision_feedback():
            return
        genres = [str(item[0]) for item in (profile.get("top_genres") or [])[:4] if item]
        if not genres:
            return
        now = timestamp()
        decision_id = "implicit-watch-taste"
        note = "Prefer " + ", ".join(genres) + " from Plex watch history over owned-library majority"
        reasons = {"genres": genres, "source": "plex_watch_history", "implicit": True}
        try:
            with sqlite3.connect(CONTROL_DB) as connection:
                connection.execute(
                    """INSERT OR IGNORE INTO decision_log (decision_id, actor, category, subject, decision, reasons_json, outcome_json, created_at, updated_at)
                       VALUES (?, 'playback', 'taste_seed', 'Plex watch-history taste', 'implicit_good', ?, '{}', ?, ?)""",
                    (decision_id, json.dumps(reasons, sort_keys=True), now, now),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO decision_feedback (decision_id, actor, sentiment, note, created_at)
                       VALUES (?, 'playback', 'good', ?, ?)""",
                    (decision_id, note, now),
                )
        except sqlite3.Error:
            return

    def _score_components(self, candidate: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        genres = [str(value) for value in (candidate.get("genres") or [])]
        genre_keys = {genre.casefold() for genre in genres}
        year = candidate.get("year")
        genre_shares = {key.casefold(): value for key, value in self._relative_weights(profile.get("top_genres", [])).items()}
        genre_points = min(45.0, sum(genre_shares.get(genre.casefold(), 0.0) * 90.0 for genre in genres))
        family = profile.get("family_profile") or {}
        family_weights = family.get("taste_weights") if isinstance(family, dict) else {}
        learned = dict(profile.get("learned_taste_profile") or {})
        if isinstance(family_weights, dict):
            for key, value in family_weights.items():
                try:
                    learned[str(key).casefold()] = max(float(learned.get(str(key).casefold()) or 0), float(value))
                except (TypeError, ValueError):
                    continue
        learned_points = 0.0
        if isinstance(learned, dict) and genre_keys:
            learned_points = min(12.0, sum(max(-2.0, min(4.0, float(learned.get(genre, 0) or 0) - 1.0)) * 3.0 for genre in genre_keys))
        certification = str(candidate.get("certification") or candidate.get("contentRating") or "").strip()
        allowed_ratings = {str(item).casefold() for item in (family.get("allowed_ratings") or []) if item}
        rating_penalty = 0.0
        if certification and allowed_ratings and certification.casefold() not in allowed_ratings and "unrated" not in allowed_ratings:
            rating_penalty = 25.0
        decade_points = 0.0
        if isinstance(year, int):
            decade_shares = self._relative_weights(profile.get("top_decades", []))
            decade_points = min(15.0, float(decade_shares.get(f"{year // 10 * 10}s", 0) or 0) * 25.0)
        director_points = min(12.0, float(dict(profile.get("top_directors", [])).get(str(candidate.get("director", "")).strip(), 0) or 0) * 2.0)
        actor_points = min(12.0, sum(float(dict(profile.get("top_actors", [])).get(actor, 0) or 0) for actor in (candidate.get("actors") or [])))
        studio_points = min(6.0, float(dict(profile.get("top_studios", [])).get(str(candidate.get("studio", "")), 0) or 0))
        recent_points = 0.0
        if isinstance(year, int) and year in {item.get("year") for item in profile.get("recent_activity", [])[:10]}:
            recent_points = 5.0
        feedback = profile.get("decision_feedback", []) or []
        title = str(candidate.get("title", "")).strip().casefold()
        title_tokens = {token for token in re.findall(r"[a-z0-9]+", title) if len(token) > 2}
        feedback_delta = 0.0
        feedback_matches = 0
        feedback_transfer = 0.0
        for item in feedback:
            subject = str(item.get("subject", "")).casefold()
            note = str(item.get("note", "")).casefold()
            polarity = 8.0 if item.get("sentiment") == "good" else -8.0
            relevant = bool(title and (title in subject or title in note)) or bool(title_tokens & set(re.findall(r"[a-z0-9]+", subject)))
            if relevant:
                feedback_matches += 1
                feedback_delta += polarity
                continue
            # Genre / decade transfer learning from feedback subjects (not title-only).
            reasons = item.get("reasons") if isinstance(item.get("reasons"), dict) else {}
            try:
                reasons = reasons or json.loads(item.get("reasons_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                reasons = {}
            fb_genres = {str(g).casefold() for g in (reasons.get("genres") or []) if g}
            if not fb_genres:
                fb_genres = {token for token in re.findall(r"[a-z0-9]+", f"{subject} {note}") if token in genre_keys}
            if fb_genres & genre_keys:
                feedback_transfer += 3.0 if polarity > 0 else -3.0
            decade_match = False
            if isinstance(year, int):
                decade_label = f"{year // 10 * 10}s"
                if decade_label.casefold() in subject or decade_label.casefold() in note:
                    decade_match = True
                year_hit = re.search(r"\b(19|20)\d{2}\b", subject)
                if year_hit and int(year_hit.group(0)) // 10 * 10 == year // 10 * 10:
                    decade_match = True
            if decade_match:
                feedback_transfer += 1.5 if polarity > 0 else -1.5
        feedback_delta = max(-20.0, min(20.0, feedback_delta + feedback_transfer))
        watch_affinity = self._bounded(20.0 + genre_points + learned_points + decade_points + director_points + actor_points + studio_points + recent_points + feedback_delta)
        collection_name = candidate.get("collection") or candidate.get("collectionTitle") or candidate.get("franchise_name")
        collection_significance = self._bounded(35 + (35 if collection_name else 0) + (15 if candidate.get("status") == "continuing" else 0) + (10 if candidate.get("seasons") else 0))
        rarity_text = " ".join(str(candidate.get(key, "")) for key in ("title", "overview", "edition", "reason")).casefold()
        rarity_markers = ("rare", "restor", "archive", "out of print", "limited edition", "collector", "lost film", "director's cut")
        rarity_matches = [marker for marker in rarity_markers if marker in rarity_text]
        rarity_preservation = self._bounded(20 + 15 * len(rarity_matches) + (15 if candidate.get("rarity") or candidate.get("rarity_flag") else 0))
        size_gb = candidate.get("size_gb")
        runtime = candidate.get("runtime") or candidate.get("runtime_minutes") or 0
        estimated_cost = float(size_gb) if isinstance(size_gb, (int, float)) else (max(0.0, float(runtime)) / 30.0 if isinstance(runtime, (int, float)) else 0.0)
        if candidate.get("seriesType") or candidate.get("seasons"):
            estimated_cost += min(50.0, len(candidate.get("seasons") or []) * 4.0)
        storage_cost = self._bounded(90.0 - estimated_cost)
        stable_id = self._stable_key("series" if candidate.get("tvdbId") else "movie", candidate)
        theatrical_bonus = 0.0
        if isinstance(runtime, (int, float)) and runtime >= 70 and not (candidate.get("seriesType") or candidate.get("seasons")):
            theatrical_bonus += 8.0
        if candidate.get("director") or (candidate.get("actors") or []):
            theatrical_bonus += 4.0
        if collection_name:
            theatrical_bonus += 3.0
        acquisition_confidence = self._bounded(35 + (45 if stable_id else 0) + (10 if candidate.get("title") else 0) + (10 if year else 0) + theatrical_bonus)
        watch_first = str(profile.get("source") or "") == "playback_history"
        weights = (0.50, 0.12, 0.13, 0.10, 0.15) if watch_first else (0.35, 0.20, 0.20, 0.10, 0.15)
        overall = self._bounded(
            watch_affinity * weights[0]
            + collection_significance * weights[1]
            + rarity_preservation * weights[2]
            + storage_cost * weights[3]
            + acquisition_confidence * weights[4]
            - rating_penalty
        )
        blend = "50/12/13/10/15 watch-first" if watch_first else "35/20/20/10/15 component blend"
        reasons = [
            {"component": "watch_affinity", "score": watch_affinity, "detail": f"relative genre/decade/creator fit; learned_taste {learned_points:+.1f}; {feedback_matches} title feedback match(es); feedback_delta {feedback_delta:+.0f}"},
            {"component": "collection_significance", "score": collection_significance, "detail": "franchise/collection and series completeness metadata"},
            {"component": "rarity_preservation", "score": rarity_preservation, "detail": f"preservation markers: {', '.join(rarity_matches) if rarity_matches else 'none'}"},
            {"component": "storage_cost", "score": storage_cost, "detail": f"higher is lower estimated storage cost ({estimated_cost:.1f} GB-equivalent)"},
            {"component": "acquisition_confidence", "score": acquisition_confidence, "detail": f"validated ids/metadata; theatrical feature bias +{theatrical_bonus:.0f}"},
            {"component": "overall", "score": overall, "detail": f"weighted {blend}"},
        ]
        return {
            "watch_affinity_score": watch_affinity,
            "collection_significance_score": collection_significance,
            "rarity_preservation_score": rarity_preservation,
            "storage_cost_score": storage_cost,
            "acquisition_confidence_score": acquisition_confidence,
            "overall_score": overall,
            "score_reasons": reasons,
        }

    def _score(self, candidate: dict[str, Any], profile: dict[str, Any]) -> float:
        return self._score_components(candidate, profile)["overall_score"]

    def _existing_candidate_titles(self) -> list[str]:
        with sqlite3.connect(CONTROL_DB) as connection:
            rows = connection.execute("SELECT title FROM discovery_candidates ORDER BY id DESC LIMIT 100").fetchall()
            return [row[0] for row in rows]

    def run(self, limit: int = 15) -> dict[str, Any]:
        profile = self.taste_profile()
        if uses_taste_backend() or not self.model.configured:
            inserted = self.taste_lookup_fallback(profile, limit=limit)
            return {"status": "taste_lookup", "generated": 0, "inserted": len(inserted), "candidates": inserted, "fallback_inserted": len(inserted)}
        already_suggested = self._existing_candidate_titles()
        watch_genres = [item[0] for item in (profile.get("top_genres") or [])[:6]]
        watch_decades = [item[0] for item in (profile.get("top_decades") or [])[:4]]
        recently_watched = [item.get("title") for item in (profile.get("recent_activity") or [])[:12] if item.get("title")]
        prompt = {
            "role": "You are a careful film and television discovery curator.",
            "instruction": "Return only JSON. Suggest distinctive theatrical movies that match watched taste, not the owned-library majority. Prefer prefer_genres and prefer_decades. Deprioritize filling Drama gaps unless the title also matches Comedy, Animation, or recent watch activity. Avoid famous default recommendations and every title in do_not_suggest_titles. Keep media types separate and include a concise reason.",
            "schema": [{"title": "string", "year": 2020, "media_type": "movie", "reason": "string"}],
            "do_not_suggest_titles": already_suggested,
            "taste": {
                "source": profile.get("source"),
                "prefer_genres": watch_genres,
                "prefer_decades": watch_decades,
                "prefer_directors": [item[0] for item in (profile.get("top_directors") or [])[:5]],
                "recently_watched": recently_watched,
                "learned_taste_profile": profile.get("learned_taste_profile") or {},
            },
            "count": limit,
        }
        response = self.model.complete([{"role": "system", "content": prompt["role"]}, {"role": "user", "content": json.dumps(prompt)}], temperature=0.75)
        generated = parse_json(response)
        existing = self._existing_keys()
        existing_titles = self._existing_titles()
        existing_editions = self._existing_edition_keys()
        inserted = []
        now = timestamp()
        with sqlite3.connect(CONTROL_DB) as connection:
            for raw in generated[:limit * 2]:
                media_type = raw.get("media_type", "movie")
                if media_type not in ("movie", "series") or not raw.get("title"):
                    continue
                client = self.planner.radarr if media_type == "movie" else self.planner.sonarr
                lookup_path = "api/v3/movie/lookup" if media_type == "movie" else "api/v3/series/lookup"
                matches = client.get(lookup_path, {"term": raw["title"].strip()})
                id_key = "tmdbId" if media_type == "movie" else "tvdbId"
                valid_matches = [item for item in matches if isinstance(item, dict) and item.get(id_key)]
                match = self._select_match(valid_matches, raw["title"].strip(), raw.get("year"))
                if not match or not match.get(id_key):
                    continue
                
                stable_id = self._stable_key(media_type, match)
                edition = self.tools._extract_edition(match) or self.tools._extract_quality(match)
                title_exists = str(match.get("title", "")).strip().casefold() in existing_titles
                edition_key = f"{stable_id}|{str(edition).strip().casefold()}" if edition else None
                if (stable_id in existing or title_exists) and (not edition_key or edition_key in existing_editions):
                    continue
                
                candidate = {key: match.get(key) for key in ("tmdbId", "imdbId", "tvdbId", "title", "year", "overview", "genres", "status", "titleSlug", "seriesType", "seasons", "runtime", "studio", "director", "actors", "collection", "collectionTitle", "size_gb", "releaseTitle", "sourceTitle", "certification", "contentRating") if key in match}
                candidate["edition"] = edition
                candidate["reason"] = raw.get("reason", "")
                scores = self._score_components(candidate, profile)
                score = scores["overall_score"]
                key_material = f"{media_type}:{stable_id}" if not edition else f"{media_type}:{stable_id}:{str(edition).strip().casefold()}"
                candidate_key = hashlib.sha256(key_material.encode()).hexdigest()
                quality = self.tools._extract_quality(match)
                connection.execute(
                    """
                    INSERT INTO discovery_candidates (candidate_key, media_type, title, year, external_ids_json, genres_json, score, watch_affinity_score, collection_significance_score, rarity_preservation_score, storage_cost_score, acquisition_confidence_score, overall_score, score_reasons_json, rationale, raw_json, edition, quality, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_key) DO UPDATE SET score=excluded.score, watch_affinity_score=excluded.watch_affinity_score, collection_significance_score=excluded.collection_significance_score, rarity_preservation_score=excluded.rarity_preservation_score, storage_cost_score=excluded.storage_cost_score, acquisition_confidence_score=excluded.acquisition_confidence_score, overall_score=excluded.overall_score, score_reasons_json=excluded.score_reasons_json, rationale=excluded.rationale, edition=excluded.edition, quality=excluded.quality, updated_at=excluded.updated_at
                    """,
                    (candidate_key, media_type, candidate["title"], candidate.get("year"), json.dumps({id_key: match[id_key]}), json.dumps(candidate.get("genres", [])), score, scores["watch_affinity_score"], scores["collection_significance_score"], scores["rarity_preservation_score"], scores["storage_cost_score"], scores["acquisition_confidence_score"], scores["overall_score"], json.dumps(scores["score_reasons"], sort_keys=True), raw.get("reason", "Collection fit"), json.dumps(candidate), edition, quality, now, now),
                )
                inserted.append({"id": connection.execute("SELECT id FROM discovery_candidates WHERE candidate_key=?", (candidate_key,)).fetchone()[0], "title": candidate["title"], "media_type": media_type, "score": score})
        fallback: list[dict[str, Any]] = []
        if len(inserted) < max(5, limit // 2):
            fallback = self.taste_lookup_fallback(profile, limit=max(5, limit - len(inserted)))
            inserted.extend(fallback)
        return {"generated": len(generated), "inserted": len(inserted), "candidates": inserted, "fallback_inserted": len(fallback)}

    def _ingest_match(self, match: dict[str, Any], media_type: str, profile: dict[str, Any], reason: str, existing: set[str], existing_titles: set[str], existing_editions: set[str]) -> dict[str, Any] | None:
        id_key = "tmdbId" if media_type == "movie" else "tvdbId"
        if not match.get(id_key):
            return None
        if is_boxset_title(match.get("title")):
            return None
        stable_id = self._stable_key(media_type, match)
        edition = self.tools._extract_edition(match) or self.tools._extract_quality(match)
        title_exists = str(match.get("title", "")).strip().casefold() in existing_titles
        edition_key = f"{stable_id}|{str(edition).strip().casefold()}" if edition else None
        if (stable_id in existing or title_exists) and (not edition_key or edition_key in existing_editions):
            return None
        candidate = {key: match.get(key) for key in ("tmdbId", "imdbId", "tvdbId", "title", "year", "overview", "genres", "status", "titleSlug", "seriesType", "seasons", "runtime", "studio", "director", "actors", "collection", "collectionTitle", "size_gb", "releaseTitle", "sourceTitle", "certification", "contentRating") if key in match}
        candidate["edition"] = edition
        candidate["reason"] = reason
        scores = self._score_components(candidate, profile)
        score = scores["overall_score"]
        key_material = f"{media_type}:{stable_id}" if not edition else f"{media_type}:{stable_id}:{str(edition).strip().casefold()}"
        candidate_key = hashlib.sha256(key_material.encode()).hexdigest()
        quality = self.tools._extract_quality(match)
        now = timestamp()
        with sqlite3.connect(CONTROL_DB) as connection:
            connection.execute(
                """
                INSERT INTO discovery_candidates (candidate_key, media_type, title, year, external_ids_json, genres_json, score, watch_affinity_score, collection_significance_score, rarity_preservation_score, storage_cost_score, acquisition_confidence_score, overall_score, score_reasons_json, rationale, raw_json, edition, quality, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_key) DO UPDATE SET score=excluded.score, watch_affinity_score=excluded.watch_affinity_score, collection_significance_score=excluded.collection_significance_score, rarity_preservation_score=excluded.rarity_preservation_score, storage_cost_score=excluded.storage_cost_score, acquisition_confidence_score=excluded.acquisition_confidence_score, overall_score=excluded.overall_score, score_reasons_json=excluded.score_reasons_json, rationale=excluded.rationale, edition=excluded.edition, quality=excluded.quality, updated_at=excluded.updated_at
                """,
                (candidate_key, media_type, candidate["title"], candidate.get("year"), json.dumps({id_key: match[id_key]}), json.dumps(candidate.get("genres", [])), score, scores["watch_affinity_score"], scores["collection_significance_score"], scores["rarity_preservation_score"], scores["storage_cost_score"], scores["acquisition_confidence_score"], scores["overall_score"], json.dumps(scores["score_reasons"], sort_keys=True), reason, json.dumps(candidate), edition, quality, now, now),
            )
            row_id = connection.execute("SELECT id FROM discovery_candidates WHERE candidate_key=?", (candidate_key,)).fetchone()[0]
        existing.add(stable_id)
        existing_titles.add(str(candidate.get("title") or "").strip().casefold())
        return {"id": row_id, "title": candidate["title"], "media_type": media_type, "score": score}

    def taste_lookup_fallback(self, profile: dict[str, Any] | None = None, limit: int = 8) -> list[dict[str, Any]]:
        """Fill the discovery queue from Radarr lookups of watched directors and recent titles when Gemini is thin or down."""
        profile = profile or self.taste_profile()
        if not self.planner or not getattr(self.planner, "radarr", None):
            return []
        terms: list[str] = []
        for director, _count in (profile.get("top_directors") or [])[:4]:
            if director:
                terms.append(str(director))
        for item in (profile.get("recent_activity") or [])[:6]:
            title = str(item.get("title") or "").strip()
            if title:
                terms.append(title)
        for genre, _count in (profile.get("top_genres") or [])[:3]:
            if genre:
                terms.append(str(genre))
        existing = self._existing_keys()
        existing_titles = self._existing_titles()
        existing_editions = self._existing_edition_keys()
        inserted: list[dict[str, Any]] = []
        seen_terms: set[str] = set()
        for term in terms:
            clean = term.strip()
            if not clean or clean.casefold() in seen_terms:
                continue
            seen_terms.add(clean.casefold())
            try:
                matches = self.planner.radarr.get("api/v3/movie/lookup", {"term": clean})
            except Exception:
                continue
            valid = [item for item in matches if isinstance(item, dict) and item.get("tmdbId")]
            for match in valid[:8]:
                row = self._ingest_match(match, "movie", profile, f"Watch-taste fallback from '{clean}'", existing, existing_titles, existing_editions)
                if not row:
                    continue
                inserted.append(row)
                if len(inserted) >= limit:
                    return inserted
        return inserted

    def queue(self, limit: int = 50) -> list[dict[str, Any]]:
        existing = self._existing_keys()
        existing_titles = self._existing_titles()
        with sqlite3.connect(CONTROL_DB) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM discovery_candidates WHERE status='new' ORDER BY score DESC, id DESC LIMIT ?", (limit * 5,)).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            external_ids = json.loads(item.pop("external_ids_json") or "{}")
            if "score_reasons_json" in item:
                item["score_reasons"] = json.loads(item.pop("score_reasons_json") or "[]")
            owned = self._stable_key(item["media_type"], external_ids) in existing or str(item.get("title", "")).strip().casefold() in existing_titles
            if owned and not item.get("edition"):
                continue
            if is_boxset_title(item.get("title")):
                continue
            results.append(item)
            if len(results) >= limit:
                break
        return results

    def candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(CONTROL_DB) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM discovery_candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        external_ids = json.loads(result.get("external_ids_json") or "{}")
        owned = self._stable_key(result["media_type"], external_ids) in self._existing_keys() or str(result.get("title", "")).strip().casefold() in self._existing_titles()
        if owned and not result.get("edition"):
            return None
        result["candidate"] = json.loads(result.pop("raw_json") or "{}")
        result["score_reasons"] = json.loads(result.pop("score_reasons_json", "[]") or "[]")
        return result

    def set_status(self, candidate_id: int, status: str) -> None:
        if status not in ("new", "approved", "rejected", "ignored"):
            raise AgentError("Invalid discovery candidate status")
        with sqlite3.connect(CONTROL_DB) as connection:
            connection.execute("UPDATE discovery_candidates SET status=?, updated_at=? WHERE id=?", (status, timestamp(), candidate_id))

    def discover_missing_franchise_items(self, limit: int = 10) -> dict[str, Any]:
        """Analyze existing collection for film/tv franchises and propose missing sequels/prequels."""
        if uses_taste_backend() or not self.model.configured:
            inserted = self.taste_lookup_fallback(limit=limit)
            return {"status": "taste_lookup", "generated": 0, "inserted": len(inserted), "franchise_candidates": inserted}
        
        # Sample 30 existing titles from catalog to identify franchises
        with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT title, year FROM catalog_items WHERE present=1 ORDER BY RANDOM() LIMIT 30").fetchall()
            sample_titles = [f"{r[0]} ({r[1] or 'n.d.'})" for r in rows]
            
        prompt = {
            "role": "You are a movie and TV franchise completeness analyst.",
            "instruction": "Examine the sample of titles from a user's movie library. Identify major franchises (such as Godzilla, James Bond, Star Trek, Marvel, DC, Disney Animation, Dragon Ball, Pokémon, Fast & Furious, Horror Sagas). Suggest 10 sequels, prequels, or spin-offs in major franchises that are obscure, rare, or released recently (2020-2025) that are missing from a large 10,000 title collection.",
            "library_sample": sample_titles,
            "schema": [{"title": "exact movie title", "year": 2024, "media_type": "movie", "franchise_name": "string", "reason": "string"}],
            "count": limit,
        }
        response = self.model.complete([{"role": "system", "content": prompt["role"]}, {"role": "user", "content": json.dumps(prompt)}], temperature=0.7)
        generated = parse_json(response)
        profile = self.taste_profile()
        existing = self._existing_keys()
        existing_titles = self._existing_titles()
        inserted = []
        now = timestamp()
        with sqlite3.connect(CONTROL_DB) as connection:
            for raw in generated[:limit * 2]:
                media_type = raw.get("media_type", "movie")
                if media_type not in ("movie", "series") or not raw.get("title"):
                    continue
                client = self.planner.radarr if media_type == "movie" else self.planner.sonarr
                lookup_path = "api/v3/movie/lookup" if media_type == "movie" else "api/v3/series/lookup"
                matches = client.get(lookup_path, {"term": raw["title"].strip()})
                id_key = "tmdbId" if media_type == "movie" else "tvdbId"
                valid_matches = [item for item in matches if isinstance(item, dict) and item.get(id_key)]
                match = self._select_match(valid_matches, raw["title"].strip(), raw.get("year"))
                if not match or not match.get(id_key):
                    continue
                
                stable_id = self._stable_key(media_type, match)
                if stable_id in existing or str(match.get("title", "")).strip().casefold() in existing_titles:
                    continue
                
                candidate = {key: match.get(key) for key in ("tmdbId", "imdbId", "tvdbId", "title", "year", "overview", "genres", "status", "titleSlug", "seriesType", "seasons", "runtime", "studio", "director", "actors", "collection", "collectionTitle", "size_gb", "releaseTitle", "sourceTitle", "certification", "contentRating") if key in match}
                candidate["franchise_name"] = raw.get("franchise_name")
                candidate["reason"] = raw.get("reason", "")
                scores = self._score_components(candidate, profile)
                score = scores["overall_score"]
                candidate_key = hashlib.sha256(f"{media_type}:{stable_id}".encode()).hexdigest()
                rationale = f"Franchise Completion ({raw.get('franchise_name', 'Franchise')}): {raw.get('reason', 'Missing sequel/prequel')}"
                connection.execute(
                    """
                    INSERT INTO discovery_candidates (candidate_key, media_type, title, year, external_ids_json, genres_json, score, watch_affinity_score, collection_significance_score, rarity_preservation_score, storage_cost_score, acquisition_confidence_score, overall_score, score_reasons_json, rationale, status, raw_json, edition, quality, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_key) DO UPDATE SET score=excluded.score, watch_affinity_score=excluded.watch_affinity_score, collection_significance_score=excluded.collection_significance_score, rarity_preservation_score=excluded.rarity_preservation_score, storage_cost_score=excluded.storage_cost_score, acquisition_confidence_score=excluded.acquisition_confidence_score, overall_score=excluded.overall_score, score_reasons_json=excluded.score_reasons_json, status='new', rationale=excluded.rationale, updated_at=excluded.updated_at
                    """,
                    (candidate_key, media_type, candidate["title"], candidate.get("year"), json.dumps({id_key: match[id_key]}), json.dumps(candidate.get("genres", [])), score, scores["watch_affinity_score"], scores["collection_significance_score"], scores["rarity_preservation_score"], scores["storage_cost_score"], scores["acquisition_confidence_score"], scores["overall_score"], json.dumps(scores["score_reasons"], sort_keys=True), rationale, json.dumps(candidate), "", "", now, now),
                )
                inserted.append({"id": connection.execute("SELECT id FROM discovery_candidates WHERE candidate_key=?", (candidate_key,)).fetchone()[0], "title": candidate["title"], "franchise": raw.get("franchise_name"), "media_type": media_type, "score": score})
        return {"generated": len(generated), "inserted": len(inserted), "franchise_candidates": inserted}

    def scan_filmography_gaps(self, person_name: str, role: str = "director", limit: int = 10) -> dict[str, Any]:
        """Scan filmography for a specific director or actor to find missing catalog titles."""
        existing_titles = self._existing_titles()
        if uses_taste_backend() or not self.model.configured:
            if not self.planner or not getattr(self.planner, "radarr", None):
                return {"person": person_name, "role": role, "total_scanned": 0, "missing_count": 0, "candidates": [], "status": "taste_lookup"}
            try:
                matches = self.planner.radarr.get("api/v3/movie/lookup", {"term": person_name})
            except Exception:
                matches = []
            missing = []
            for item in matches:
                if not isinstance(item, dict) or not item.get("title"):
                    continue
                title = str(item.get("title", "")).strip()
                if title and title.casefold() not in existing_titles and not is_boxset_title(title):
                    missing.append({"title": title, "year": item.get("year"), "media_type": "movie", "overview": item.get("overview") or ""})
            return {"person": person_name, "role": role, "total_scanned": len(matches) if isinstance(matches, list) else 0, "missing_count": len(missing), "candidates": missing[:limit], "status": "taste_lookup"}
        prompt = {
            "role": "You are a filmography completeness specialist.",
            "instruction": f"List the top key works directed by or starring '{person_name}' (role: {role}). Return only JSON.",
            "person": person_name,
            "role_type": role,
            "schema": [{"title": "exact title", "year": 2020, "media_type": "movie", "overview": "short synopsis"}],
            "count": limit,
        }
        response = self.model.complete([{"role": "system", "content": prompt["role"]}, {"role": "user", "content": json.dumps(prompt)}], temperature=0.3)
        generated = parse_json(response)
        missing = []
        for item in generated:
            title = str(item.get("title", "")).strip()
            if title and title.casefold() not in existing_titles:
                missing.append(item)

        return {
            "person": person_name,
            "role": role,
            "total_scanned": len(generated),
            "missing_count": len(missing),
            "candidates": missing[:limit]
        }

