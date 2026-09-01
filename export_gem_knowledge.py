#!/usr/bin/env python3
"""Export CineSwarm knowledge file for direct Gemini Gem upload."""

import json
import os
import sqlite3
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_DB = os.environ.get("CINESWARM_CATALOG_DB", os.path.join(BASE_DIR, "cineswarm_catalog.db"))
OUTPUT_FILE = os.path.join(BASE_DIR, "cineswarm_gem_knowledge.json")


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

    knowledge = {
        "system": "CineSwarm Omni-Catalog Copilot",
        "catalog_summary": {
            "total_movies": movie_count,
            "total_series": series_count,
            "total_items": movie_count + series_count
        },
        "top_genres": dict(top_genres),
        "video_codecs": codec_counter,
        "recent_vault_additions_sample": recent_additions
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(knowledge, f, indent=2)

    print(f"Generated Gemini Gem knowledge file: {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE)} bytes)")
    return knowledge


if __name__ == "__main__":
    generate_gem_knowledge()
