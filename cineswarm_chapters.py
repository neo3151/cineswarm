#!/usr/bin/env python3
"""Plex Smart Chapter & Scene Summarizer for CineSwarm using Gemini AI."""

from __future__ import annotations

import json
import re
from typing import Any

from cineswarm_agents import AgentError, HostedModelClient


class ChapterSummarizer:
    def __init__(self, model: HostedModelClient | None = None):
        self.model = model or HostedModelClient()

    def generate(self, title: str, year: int | None = None) -> dict[str, Any]:
        if not self.model.configured:
            raise AgentError("Gemini model is not configured")

        prompt = {
            "role": "You are a master film editor and story analyst creating precise narrative chapter markers and scene summaries for a feature film.",
            "film_title": title,
            "release_year": year,
            "instruction": "Break down the movie into 6 to 10 distinct narrative chapters (e.g. Prologue, Act I Climax, Falling Action, Epilogue) with exact timestamps and 2-sentence scene summaries.",
            "schema": {
                "movie_title": title,
                "chapter_count": 8,
                "chapters": [
                    {
                        "chapter_number": 1,
                        "title": "Prologue: The Arrival",
                        "start_timestamp": "00:00:00",
                        "start_seconds": 0,
                        "summary": "Summary of what takes place in this chapter."
                    }
                ]
            }
        }

        messages = [
            {"role": "system", "content": prompt["role"]},
            {"role": "user", "content": json.dumps(prompt)}
        ]
        response = self.model.complete(messages, temperature=0.7)

        fenced = re.search(r"```(?:json)?\s*(.*?)```", response, re.S | re.I)
        cand_str = fenced.group(1) if fenced else response
        try:
            parsed = json.loads(cand_str)
        except Exception:
            parsed = {}

        if isinstance(parsed, list) and parsed:
            parsed = parsed[0]
        if not isinstance(parsed, dict):
            parsed = {}

        chapters = parsed.get("chapters", [])
        vtt_content = self.export_vtt(chapters)
        return {
            "movie_title": parsed.get("movie_title", title),
            "chapter_count": len(chapters),
            "chapters": chapters,
            "vtt": vtt_content
        }

    @staticmethod
    def export_vtt(chapters: list[dict[str, Any]]) -> str:
        lines = ["WEBVTT", ""]
        for idx, ch in enumerate(chapters):
            start_sec = ch.get("start_seconds", idx * 900)
            next_ch = chapters[idx + 1] if idx + 1 < len(chapters) else None
            end_sec = next_ch.get("start_seconds", start_sec + 900) if next_ch else start_sec + 900

            def fmt(sec: int) -> str:
                h = sec // 3600
                m = (sec % 3600) // 60
                s = sec % 60
                return f"{h:02d}:{m:02d}:{s:02d}.000"

            lines.append(f"{fmt(start_sec)} --> {fmt(end_sec)}")
            lines.append(f"{ch.get('title', f'Chapter {idx+1}')}: {ch.get('summary', '')}")
            lines.append("")
        return "\n".join(lines)
