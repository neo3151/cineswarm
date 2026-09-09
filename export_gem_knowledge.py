#!/usr/bin/env python3
"""Export CineSwarm knowledge file for direct Gemini Gem upload."""

import json
import os
import sqlite3
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_DB = os.environ.get("CINESWARM_CATALOG_DB", os.path.join(BASE_DIR, "cineswarm_catalog.db"))
CONTROL_DB = os.environ.get("CINESWARM_CONTROL_DB", os.path.join(BASE_DIR, "cineswarm_control.db"))
OUTPUT_FILE = os.path.join(BASE_DIR, "cineswarm_gem_knowledge.json")

POLICY_KEYS = (
    "CINESWARM_FULL_AUTOPILOT",
    "CINESWARM_AUTO_ADD_SEARCH",
    "CINESWARM_DISCOVERY_ENABLED",
    "CINESWARM_AUTO_EMERGENCY_STOP",
    "CINESWARM_AUTO_BUDGET_MODE",
    "CINESWARM_AUTO_MIN_SCORE",
    "CINESWARM_AUTO_MAX_CONCURRENT_DOWNLOADS",
    "CINESWARM_AUTO_MIN_FREE_SPACE_GB",
    "CINESWARM_AUTO_REQUIRED_QUALITY_PROFILE",
    "CINESWARM_AUTO_NEAR_MISS_FLOOR",
)


def _safe_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _control_memory() -> dict[str, Any]:
    if not os.path.exists(CONTROL_DB):
        return {}
    memory: dict[str, Any] = {
        "recent_decisions": [],
        "feedback_polarity": {"good": 0, "bad": 0},
        "learned_taste_top": {},
        "playback_top_genres": [],
        "playback_top_directors": [],
        "active_policies": {},
    }
    try:
        with sqlite3.connect(f"file:{CONTROL_DB}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "decision_log" in tables:
                for row in conn.execute(
                    "SELECT decision_id, category, subject, decision, created_at FROM decision_log ORDER BY created_at DESC LIMIT 20"
                ):
                    memory["recent_decisions"].append(dict(row))
            if "decision_feedback" in tables:
                for row in conn.execute("SELECT sentiment, COUNT(*) AS n FROM decision_feedback GROUP BY sentiment"):
                    sentiment = str(row["sentiment"] or "").lower()
                    if sentiment in memory["feedback_polarity"]:
                        memory["feedback_polarity"][sentiment] = int(row["n"] or 0)
            if "user_taste_memory" in tables:
                learned = conn.execute(
                    "SELECT value_json FROM user_taste_memory WHERE key='learned_taste_profile'"
                ).fetchone()
                if learned and learned[0]:
                    weights = _safe_json(learned[0], {})
                    if isinstance(weights, dict):
                        ranked = sorted(
                            ((str(k), float(v)) for k, v in weights.items() if isinstance(v, (int, float))),
                            key=lambda item: item[1],
                            reverse=True,
                        )[:15]
                        memory["learned_taste_top"] = dict(ranked)
                summary = conn.execute(
                    "SELECT value_json FROM user_taste_memory WHERE key='user_profile_summary'"
                ).fetchone()
                if summary and summary[0]:
                    profile = _safe_json(summary[0], {})
                    if isinstance(profile, dict):
                        memory["playback_top_genres"] = list(profile.get("top_genres") or [])[:12]
                        memory["playback_top_directors"] = list(profile.get("top_directors") or [])[:12]
            if "autonomous_policy" in tables:
                placeholders = ",".join("?" for _ in POLICY_KEYS)
                rows = conn.execute(
                    f"SELECT key, value FROM autonomous_policy WHERE key IN ({placeholders})",
                    POLICY_KEYS,
                ).fetchall()
                memory["active_policies"] = {row["key"]: row["value"] for row in rows}
    except Exception:
        return memory
    return memory


def generate_gem_knowledge() -> dict[str, Any]:
    if not os.path.exists(CATALOG_DB):
        return {"error": "Catalog database not found"}

    with sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        
        # Summary counts
        movie_count = conn.execute("SELECT COUNT(*) FROM catalog_items WHERE present=1 AND media_type='movie'").fetchone()[0]
        series_count = conn.execute("SELECT COUNT(*) FROM catalog_items WHERE present=1 AND media_type='series'").fetchone()[0]

        # Recent additions
        recent_rows = conn.execute("SELECT title, year, media_type FROM catalog_items WHERE present=1 ORDER BY id DESC LIMIT 50").fetchall()
        recent_additions = [f"{r['title']} ({r['year'] or 'n.d.'}) [{r['media_type']}]" for r in recent_rows]

        # Top genres
        genre_counter = {}
        for row in conn.execute("SELECT genres_json FROM catalog_items WHERE present=1"):
            try:
                for g in json.loads(row["genres_json"] or "[]"):
                    genre_counter[g] = genre_counter.get(g, 0) + 1
            except Exception:
                pass
        top_genres = sorted(genre_counter.items(), key=lambda x: x[1], reverse=True)[:15]

        # Codec breakdown
        codec_counter = {}
        for row in conn.execute("SELECT video_codec FROM catalog_items WHERE present=1 AND video_codec IS NOT NULL"):
            codec = row["video_codec"]
            codec_counter[codec] = codec_counter.get(codec, 0) + 1

    swarm_memory = _control_memory()
    knowledge = {
        "system": "CineSwarm Omni-Catalog Copilot",
        "catalog_summary": {
            "total_movies": movie_count,
            "total_series": series_count,
            "total_items": movie_count + series_count
        },
        "top_genres": dict(top_genres),
        "video_codecs": codec_counter,
        "recent_vault_additions_sample": recent_additions,
        "swarm_memory": swarm_memory,
        "growth_gates": {
            "min_discovery_score": swarm_memory.get("active_policies", {}).get("CINESWARM_AUTO_MIN_SCORE", "70"),
            "budget_mode": swarm_memory.get("active_policies", {}).get("CINESWARM_AUTO_BUDGET_MODE", "queue_only"),
            "max_concurrent_downloads": swarm_memory.get("active_policies", {}).get("CINESWARM_AUTO_MAX_CONCURRENT_DOWNLOADS", "2"),
        },
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(knowledge, f, indent=2)

    print(f"Generated Gemini Gem knowledge file: {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE)} bytes)")
    return knowledge


if __name__ == "__main__":
    generate_gem_knowledge()
