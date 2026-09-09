#!/usr/bin/env python3
"""Collection-aware Gemini discovery queue with Radarr/Sonarr validation."""

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
        """Backfill component scores for existing discovery candidates."""
        profile = self.taste_profile()
        updated_count = 0
        with sqlite3.connect(CONTROL_DB) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT id, raw_json, rationale, edition, overall_score, score FROM discovery_candidates WHERE watch_affinity_score=0 AND (overall_score>0 OR score>0)").fetchall()
            for row in rows:
                try:
                    candidate = json.loads(row["raw_json"] or "{}")
                except json.JSONDecodeError:
                    candidate = {}
                candidate["edition"] = row["edition"]
                candidate["reason"] = row["rationale"]
                scores = self._score_components(candidate, profile)
                overall = row["overall_score"] or row["score"] or scores["overall_score"]
                connection.execute(
                    """
                    UPDATE discovery_candidates
                    SET watch_affinity_score=?, collection_significance_score=?, rarity_preservation_score=?, storage_cost_score=?, acquisition_confidence_score=?, overall_score=?, score_reasons_json=?
                    WHERE id=?
                    """,
                    (scores["watch_affinity_score"], scores["collection_significance_score"], scores["rarity_preservation_score"], scores["storage_cost_score"], scores["acquisition_confidence_score"], overall, json.dumps(scores["score_reasons"], sort_keys=True), row["id"])
                )
                updated_count += 1
        return updated_count



    def _decision_feedback(self) -> list[dict[str, Any]]:
        try:
            with sqlite3.connect(f"file:{CONTROL_DB}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute("SELECT f.sentiment, f.note, d.category, d.subject, d.decision, f.created_at FROM decision_feedback f JOIN decision_log d ON d.decision_id=f.decision_id ORDER BY f.created_at DESC LIMIT 50").fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            return []

    def taste_profile(self) -> dict[str, Any]:
        # Try to use playback history for weighted taste profile
        if self.playback_history:
            try:
                pb = self.playback_history.build_taste_profile()
                if pb.get("total_watched", 0) > 0:
                    titles = []
                    with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as connection:
                        titles = [f"{row[0]} ({row[1] or 'n.d.'})" for row in connection.execute("SELECT title, year FROM catalog_items WHERE present=1 ORDER BY title LIMIT 150")]
                    return {**pb, "existing_titles_sample": titles, "decision_feedback": self._decision_feedback(), "source": "playback_history"}
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
        return {"top_genres": genres.most_common(12), "top_decades": decades.most_common(8), "existing_titles_sample": titles, "decision_feedback": self._decision_feedback(), "source": "catalog"}

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

    def _score_components(self, candidate: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        genres = [str(value) for value in (candidate.get("genres") or [])]
        top_genres = dict(profile.get("top_genres", []))
        year = candidate.get("year")
        genre_points = min(45.0, sum(float(top_genres.get(genre, 0) or 0) for genre in genres) * 2.5)
        decade_points = 0.0
        if isinstance(year, int):
            decade_points = min(15.0, float(dict(profile.get("top_decades", [])).get(f"{year // 10 * 10}s", 0) or 0) * 1.5)
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
        for item in feedback:
            subject = str(item.get("subject", "")).casefold()
            note = str(item.get("note", "")).casefold()
            relevant = bool(title and (title in subject or title in note)) or bool(title_tokens & set(re.findall(r"[a-z0-9]+", subject)))
            if relevant:
                feedback_matches += 1
                feedback_delta += 8.0 if item.get("sentiment") == "good" else -8.0
        feedback_delta = max(-20.0, min(20.0, feedback_delta))
        watch_affinity = self._bounded(20.0 + genre_points + decade_points + director_points + actor_points + studio_points + recent_points + feedback_delta)

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
        overall = self._bounded(
            watch_affinity * 0.35
            + collection_significance * 0.20
            + rarity_preservation * 0.20
            + storage_cost * 0.10
            + acquisition_confidence * 0.15
        )
        reasons = [
            {"component": "watch_affinity", "score": watch_affinity, "detail": f"playback genre/decade/creator fit; {feedback_matches} relevant feedback record(s), adjustment {feedback_delta:+.0f}"},
            {"component": "collection_significance", "score": collection_significance, "detail": "franchise/collection and series completeness metadata"},
            {"component": "rarity_preservation", "score": rarity_preservation, "detail": f"preservation markers: {', '.join(rarity_matches) if rarity_matches else 'none'}"},
            {"component": "storage_cost", "score": storage_cost, "detail": f"higher is lower estimated storage cost ({estimated_cost:.1f} GB-equivalent)"},
            {"component": "acquisition_confidence", "score": acquisition_confidence, "detail": f"validated ids/metadata; theatrical feature bias +{theatrical_bonus:.0f}"},
            {"component": "overall", "score": overall, "detail": "weighted 35/20/20/10/15 component blend"},
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
        if not self.model.configured:
            raise AgentError("Gemini is not configured for discovery")
        profile = self.taste_profile()
        already_suggested = self._existing_candidate_titles()
        prompt = {
            "role": "You are a careful film and television discovery curator.",
            "instruction": "Return only JSON. Suggest 15 genuinely distinctive, fresh, less-obvious titles that are unlikely to already be in this very large collection. Avoid famous default recommendations, avoid every title in existing_titles_sample, and DO NOT suggest any title in do_not_suggest_titles. Keep media types separate and include a concise reason.",
            "schema": [{"title": "string", "year": 2020, "media_type": "movie", "reason": "string"}],
            "do_not_suggest_titles": already_suggested,
            "taste_profile": profile,
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
                
                candidate = {key: match.get(key) for key in ("tmdbId", "imdbId", "tvdbId", "title", "year", "overview", "genres", "status", "titleSlug", "seriesType", "seasons", "runtime", "studio", "director", "actors", "collection", "collectionTitle", "size_gb", "releaseTitle", "sourceTitle") if key in match}
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
        return {"generated": len(generated), "inserted": len(inserted), "candidates": inserted}

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
        if not self.model.configured:
            raise AgentError("Gemini model is not configured")
        
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
                
                candidate = {key: match.get(key) for key in ("tmdbId", "imdbId", "tvdbId", "title", "year", "overview", "genres", "status", "titleSlug", "seriesType", "seasons", "runtime", "studio", "director", "actors", "collection", "collectionTitle", "size_gb", "releaseTitle", "sourceTitle") if key in match}
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
        if not self.model.configured:
            raise AgentError("Gemini model is not configured")

        existing_titles = self._existing_titles()
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

