#!/usr/bin/env python3
"""CineSwarm Multi-User Family Taste Engine & Rating Governance.

Enables multi-user taste tracking and content rating enforcement:
- Profiles: Admin, Partner, Kids, Guest
- Per-profile taste profile weights & watch histories
- Automatic Certification Rating Enforcement (e.g. Kids profile caps at G/PG/PG-13)
- Personalized vector search boosts per active user profile
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


class MultiUserTasteEngine:
    """Manages multi-user family profiles, taste preferences, and rating safety."""

    DEFAULT_PROFILES = {
        "admin": {
            "display_name": "Primary Admin",
            "max_certification": "NC-17",
            "allowed_ratings": ["G", "PG", "PG-13", "R", "NC-17", "TV-MA", "Unrated"],
            "taste_weights": {}
        },
        "partner": {
            "display_name": "Partner",
            "max_certification": "R",
            "allowed_ratings": ["G", "PG", "PG-13", "R", "TV-14", "TV-MA"],
            "taste_weights": {"comedy": 1.4, "romance": 1.4, "drama": 1.2}
        },
        "kids": {
            "display_name": "Kids Zone",
            "max_certification": "PG-13",
            "allowed_ratings": ["G", "PG", "PG-13", "TV-G", "TV-Y", "TV-7"],
            "taste_weights": {"animation": 2.0, "family": 2.0, "comedy": 1.5}
        },
        "guest": {
            "display_name": "Guest Lounge",
            "max_certification": "R",
            "allowed_ratings": ["G", "PG", "PG-13", "R"],
            "taste_weights": {"comedy": 1.3, "animation": 1.2, "family": 1.2}
        }
    }

    def __init__(self, control_db_path: str):
        self.db_path = control_db_path
        self.active_profile = "admin"
        self._init_db()

    def _init_db(self) -> None:
        """Initialize user profiles table in control database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_family_profiles (
                        profile_id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        max_certification TEXT NOT NULL,
                        allowed_ratings_json TEXT NOT NULL,
                        taste_weights_json TEXT NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    )
                """)
                # Populate default profiles if empty
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM user_family_profiles")
                if cursor.fetchone()[0] == 0:
                    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    for p_id, p_data in self.DEFAULT_PROFILES.items():
                        is_act = 1 if p_id == "admin" else 0
                        conn.execute(
                            "INSERT INTO user_family_profiles VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (p_id, p_data["display_name"], p_data["max_certification"],
                             json.dumps(p_data["allowed_ratings"]), json.dumps(p_data["taste_weights"]), is_act, now_iso)
                        )
                now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for p_id, p_data in self.DEFAULT_PROFILES.items():
                    row = conn.execute("SELECT taste_weights_json FROM user_family_profiles WHERE profile_id=?", (p_id,)).fetchone()
                    if row and (not row[0] or row[0] in ("{}", "null")) and p_data["taste_weights"]:
                        conn.execute(
                            "UPDATE user_family_profiles SET taste_weights_json=?, updated_at=? WHERE profile_id=?",
                            (json.dumps(p_data["taste_weights"]), now_iso, p_id),
                        )
        except Exception:
            pass

    def switch_active_profile(self, profile_id: str) -> dict[str, Any]:
        """Switch current active family user profile."""
        clean_id = profile_id.strip().lower()
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE user_family_profiles SET is_active = 0")
                conn.execute("UPDATE user_family_profiles SET is_active = 1, updated_at = ? WHERE profile_id = ?", (now_iso, clean_id))
            self.active_profile = clean_id
            return {"status": "success", "active_profile": clean_id, "timestamp": now_iso}
        except Exception as err:
            return {"status": "error", "message": str(err)}

    def get_active_profile(self) -> dict[str, Any]:
        """Return currently active user profile and rating governance rules."""
        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM user_family_profiles WHERE is_active = 1 LIMIT 1").fetchone()
                if row:
                    return {
                        "profile_id": row["profile_id"],
                        "display_name": row["display_name"],
                        "max_certification": row["max_certification"],
                        "allowed_ratings": json.loads(row["allowed_ratings_json"] or "[]"),
                        "taste_weights": json.loads(row["taste_weights_json"] or "{}")
                    }
        except Exception:
            pass
        admin = {"profile_id": "admin", **self.DEFAULT_PROFILES["admin"]}
        return admin

    def apply_plex_account(self, account_name: str) -> dict[str, Any] | None:
        """Switch the active profile when a Plex account is mapped in CINESWARM_PLEX_PROFILE_MAP."""
        import os
        raw = os.environ.get("CINESWARM_PLEX_PROFILE_MAP", "")
        mapping: dict[str, str] = {}
        for item in raw.split(","):
            if "=" not in item:
                continue
            plex_name, profile_id = item.split("=", 1)
            mapping[plex_name.strip().casefold()] = profile_id.strip().lower()
        profile_id = mapping.get(str(account_name or "").strip().casefold())
        if not profile_id:
            return None
        return self.switch_active_profile(profile_id)

    def allows_certification(self, certification: str | None, profile: dict[str, Any] | None = None) -> bool:
        profile = profile or self.get_active_profile()
        allowed = {str(item).casefold() for item in (profile.get("allowed_ratings") or [])}
        if not certification or not allowed:
            return True
        return str(certification).casefold() in allowed or "unrated" in allowed

    def list_all_profiles(self) -> list[dict[str, Any]]:
        """List all registered family user profiles."""
        profiles = []
        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM user_family_profiles ORDER BY profile_id").fetchall()
                for r in rows:
                    profiles.append({
                        "profile_id": r["profile_id"],
                        "display_name": r["display_name"],
                        "max_certification": r["max_certification"],
                        "allowed_ratings": json.loads(r["allowed_ratings_json"] or "[]"),
                        "taste_weights": json.loads(r["taste_weights_json"] or "{}"),
                        "is_active": bool(r["is_active"])
                    })
        except Exception:
            pass
        return profiles
