#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import io
import os
import re
from typing import Any

import discord

from cineswarm_control import AgentError, ServiceError, make_plane
from cineswarm_discovery import DiscoveryEngine
from cineswarm_worker import sd_notify


class DiscordService:
    def __init__(self, plane: Any | None = None):
        self.plane = plane or make_plane()
        self.guild_ids = self._ids("CINESWARM_DISCORD_GUILD_IDS")
        self.channel_ids = self._ids("CINESWARM_DISCORD_CHANNEL_IDS")
        self.user_ids = self._ids("CINESWARM_DISCORD_USER_IDS")
        self.setup_code = os.environ.get("CINESWARM_DISCORD_SETUP_CODE", "")
        self.require_mention = os.environ.get("CINESWARM_DISCORD_REQUIRE_MENTION", "true").lower() in {"1", "true", "yes", "on"}
        self.prefix = os.environ.get("CINESWARM_DISCORD_PREFIX", "!cine")

    def _ids(self, key: str) -> set[int]:
        store = getattr(self.plane, "store", None)
        raw = (store.get_policy(key) if store and hasattr(store, "get_policy") else None) or os.environ.get(key, "")
        return {int(value.strip()) for value in raw.split(",") if value.strip().isdigit()}

    def configured(self) -> bool:
        return bool(self.guild_ids and self.channel_ids and self.user_ids)

    def pair(self, code: str, user_id: int, guild_id: int | None, channel_id: int) -> str:
        if self.configured():
            return "CineSwarm Discord access is already paired."
        if not self.setup_code or code != self.setup_code or guild_id is None:
            return "Pairing failed. Check the one-time code and send it inside the intended server channel."
        values = {"CINESWARM_DISCORD_GUILD_IDS": str(guild_id), "CINESWARM_DISCORD_CHANNEL_IDS": str(channel_id), "CINESWARM_DISCORD_USER_IDS": str(user_id)}
        for key, value in values.items():
            self.plane.store.set_policy(key, value)
        self.guild_ids = {guild_id}
        self.channel_ids = {channel_id}
        self.user_ids = {user_id}
        self.plane.store.audit(f"discord:{user_id}", "discord_pair", "discord", "approval-gated", "completed", {"guild_id": guild_id, "channel_id": channel_id})
        return "CineSwarm paired successfully. This server, channel, and Discord user are now the only authorized chat identities. Try `!cine status`."

    def authorized(self, user_id: int, guild_id: int | None, channel_id: int) -> bool:
        return bool(guild_id in self.guild_ids and channel_id in self.channel_ids and user_id in self.user_ids)

    def move_channel(self, code: str, user_id: int, guild_id: int | None, channel_id: int) -> str:
        if not self.setup_code or code != self.setup_code or guild_id not in self.guild_ids or user_id not in self.user_ids:
            return "Channel move failed. Use the one-time setup code from the already-paired Discord user in the paired server."
        self.plane.store.set_policy("CINESWARM_DISCORD_CHANNEL_IDS", str(channel_id))
        self.channel_ids = {channel_id}
        self.plane.store.audit(f"discord:{user_id}", "discord_channel_move", "discord", "approval-gated", "completed", {"guild_id": guild_id, "channel_id": channel_id})
        return "CineSwarm moved successfully. This is now the only authorized Discord channel. Try `!cine status`."

    @staticmethod
    def _title_year(value: str) -> tuple[str, int | None]:
        match = re.fullmatch(r"\s*(.*?)\s*\((\d{4})\)\s*", value)
        return (match.group(1).strip(), int(match.group(2))) if match else (value.strip(), None)

    @staticmethod
    def _release_title_year(value: str) -> tuple[str, int | None]:
        match = re.match(r"^(.*?)[._ -]+((?:19|20)\d{2})(?:[._ -]|$)", value.strip())
        if not match:
            return DiscordService._title_year(value)
        title = re.sub(r"[._]+", " ", match.group(1))
        return re.sub(r"\s+", " ", title).strip(), int(match.group(2))

    @staticmethod
    def _looks_like_release(value: str) -> bool:
        return bool(re.search(r"(?:^|[._ -])(?:19|20)\d{2}(?:[._ -]|$)", value) and re.search(r"(?:2160p|1080p|720p|remux|bluray|web[-_. ]?dl)", value, re.I))

    def _release_status(self, value: str) -> str:
        title, year = self._release_title_year(value)
        matches = self.plane.agents.tools.search_catalog(title, "movie", 50)
        movie = next((item for item in matches if item.get("title", "").casefold() == title.casefold() and (year is None or item.get("year") == year)), None)
        if not movie:
            return f"I parsed this as **{title} ({year or 'unknown year'})**, but it is not currently managed by Radarr. Use `!cine add {title} ({year})` first."
        movie_id = movie["source_native_id"]
        queue = self.plane.planner.radarr.get("api/v3/queue", {"page": 1, "pageSize": 100, "includeUnknownMovieItems": "true"})
        queue_items = [item for item in queue.get("records", []) if item.get("movieId") == movie_id]
        queued = next((item for item in queue_items if item.get("title", "").casefold() == value.casefold()), queue_items[0] if queue_items else None)
        releases = self.plane.planner.radarr.get("api/v3/release", {"movieId": movie_id})
        release = next((item for item in releases if item.get("title", "").casefold() == value.casefold()), None)
        lines = [f"Movie: **{movie['title']} ({movie.get('year')})**", f"Radarr ID: `{movie_id}`"]
        if queued:
            size = int(queued.get("size") or 0)
            remaining = int(queued.get("sizeleft") or 0)
            progress = round((1 - remaining / size) * 100, 1) if size else 0
            lines.extend([f"Queue: **{queued.get('status', 'unknown')}** / `{queued.get('trackedDownloadState', 'unknown')}`", f"Progress: **{progress}%** ({remaining / (1024 ** 3):.1f} GiB remaining of {size / (1024 ** 3):.1f} GiB)"])
        elif release:
            lines.append("Queue: not currently queued")
        else:
            lines.append("Release: exact title not found in the current Radarr interactive search")
        if release:
            lines.append(f"Indexer: {release.get('indexer') or 'unknown'}")
            rejections = release.get("rejections") or []
            lines.append("Radarr flags: " + ("; ".join(rejections) if rejections else "none"))
        return "\n".join(lines)

    def _propose_grab(self, value: str, actor: str) -> str:
        title, year = self._release_title_year(value)
        matches = self.plane.agents.tools.search_catalog(title, "movie", 50)
        movie = next((item for item in matches if item.get("title", "").casefold() == title.casefold() and (year is None or item.get("year") == year)), None)
        if not movie:
            return f"I parsed this as **{title} ({year or 'unknown year'})**, but it is not currently managed by Radarr."
        movie_id = movie["source_native_id"]
        queue = self.plane.planner.radarr.get("api/v3/queue", {"page": 1, "pageSize": 100, "includeUnknownMovieItems": "true"})
        if any(item.get("movieId") == movie_id and item.get("title", "").casefold() == value.casefold() for item in queue.get("records", [])):
            return "That exact release is already in the Radarr queue. Use `!cine release RELEASE_NAME` to monitor it."
        releases = self.plane.planner.radarr.get("api/v3/release", {"movieId": movie_id})
        release = next((item for item in releases if item.get("title", "").casefold() == value.casefold()), None)
        if not release or not release.get("guid") or not release.get("indexerId"):
            return "That exact release is not currently available through Radarr interactive search."
        payload = {"movie_id": movie_id, "guid": release["guid"], "indexer_id": release["indexerId"], "title": release["title"]}
        task_id = self.plane.store.create_task("radarr_release_grab_request", actor, payload)
        size = int(release.get("size") or 0) / (1024 ** 3)
        rejections = release.get("rejections") or []
        flags = "; ".join(rejections) if rejections else "none"
        decision_id = self._record_decision(actor, "exact_release_grab", release["title"], "pending", {"indexer": release.get("indexer"), "size_gib": round(size, 1), "radarr_flags": rejections}, {"task_id": task_id})
        if self._full_autopilot():
            self._execute_task(task_id, actor, decision_id)
            return f"Autopilot grabbed **{release['title']}** from {release.get('indexer') or 'unknown'} ({size:.1f} GiB).\nRadarr flags overridden: {flags}\nDecision `{decision_id}`."
        return f"Proposed grabbing **{release['title']}** from {release.get('indexer') or 'unknown'} ({size:.1f} GiB).\nRadarr flags: {flags}\nDecision `{decision_id}`. Nothing has changed yet. Confirm this exact release with `!cine confirm {task_id}`."

    def _status(self) -> str:
        status = self.plane.store.status()
        services = status.get("services", [])
        srv_lines = []
        for item in services:
            st = item.get("status", "unknown")
            icon = "🟢" if st == "healthy" else "🔴"
            srv_lines.append(f"  {icon} **{item['service'].capitalize()}**: `{st.upper()}`")
        srv_str = "\n".join(srv_lines) if srv_lines else "  • No service telemetry"

        worker = status.get("worker") or {}
        worker_icon = "🟢" if worker.get("healthy") else "🟡"
        catalog = status.get("catalog") or {}
        movie_queue = self.plane.planner.queue("movie").get("total_records", 0) if self.plane.planner else 0
        series_queue = self.plane.planner.queue("series").get("total_records", 0) if self.plane.planner else 0

        return (
            "🛰️ **CineSwarm Operational Status**\n"
            "```text\n"
            f"Vault Catalog : {catalog.get('movie', 0)} Movies | {catalog.get('series', 0)} Series\n"
            f"Active Queues : {movie_queue} Radarr Downloads | {series_queue} Sonarr Downloads\n"
            f"Sentinel Status: {'HEALTHY' if worker.get('healthy') else 'STALE'} ({worker.get('status', 'unknown')})\n"
            "```\n"
            "**Service Health Telemetry:**\n"
            f"{srv_str}"
        )

    def _monitor(self) -> str:
        snapshot = self.plane.monitoring_snapshot() if hasattr(self.plane, "monitoring_snapshot") else {}
        overall = str(snapshot.get("overall_status") or "unknown").upper()
        icon = {"HEALTHY": "🟢", "DEGRADED": "🟡", "UNHEALTHY": "🔴"}.get(overall, "⚪")
        worker = snapshot.get("worker") or {}
        worker_label = "current" if worker.get("healthy") else "stale"
        downloads = snapshot.get("downloads") or {}
        services = snapshot.get("services") or []
        unhealthy = snapshot.get("unhealthy_services") or []
        failures = snapshot.get("recent_failures") or []
        srv_lines = []
        for item in services:
            st = item.get("status", "unknown")
            mark = "🟢" if st == "healthy" else "🔴"
            srv_lines.append(f"  {mark} **{str(item.get('service', '?')).capitalize()}**: `{str(st).upper()}`")
        top_issues = []
        for issue in failures[:5]:
            label = issue.get("type") or "issue"
            detail = issue.get("action") or issue.get("error") or issue.get("status") or ""
            top_issues.append(f"• `{label}` {detail}".strip())
        if not top_issues and unhealthy:
            top_issues = [f"• Unhealthy: {', '.join(unhealthy)}"]
        if not top_issues:
            top_issues = ["• No recent actionable failures"]
        stop = "ACTIVE" if snapshot.get("emergency_stop") else "off"
        return (
            f"{icon} **CineSwarm Live Monitor — {overall}**\n"
            "```text\n"
            f"Downloads     : {downloads.get('movies', 0)} movies | {downloads.get('series', 0)} series\n"
            f"Pending approve: {snapshot.get('pending_approvals', 0)}\n"
            f"Worker        : {worker_label} ({worker.get('status', 'unknown')}, age {worker.get('age_seconds', '?')}s)\n"
            f"Emergency stop: {stop}\n"
            "```\n"
            "**Services:**\n"
            + ("\n".join(srv_lines) if srv_lines else "  • No service telemetry")
            + "\n\n**Top issues:**\n"
            + "\n".join(top_issues)
        )


    def _full_autopilot(self) -> bool:
        emergency = self.plane.store.get_policy("CINESWARM_AUTO_EMERGENCY_STOP") or os.environ.get("CINESWARM_AUTO_EMERGENCY_STOP", "false")
        value = self.plane.store.get_policy("CINESWARM_FULL_AUTOPILOT") or "false"
        return emergency.lower() not in {"1", "true", "yes", "on"} and value.lower() in {"1", "true", "yes", "on"}

    def _record_decision(self, actor: str, category: str, subject: str, decision: str, reasons: Any = None, outcome: Any = None) -> str:
        return self.plane.store.record_decision(actor, category, subject, decision, reasons or {}, outcome or {})

    def _execute_task(self, task_id: str, actor: str, decision_id: str) -> dict[str, Any]:
        try:
            result = self.plane.approve_task(task_id, actor)
            follow_up = result.get("follow_up")
            follow_result = self.plane.approve_task(follow_up["task_id"], actor) if follow_up else None
            outcome = {"task_id": task_id, "result": result.get("result") or {}, "follow_up": follow_result}
            self.plane.store.update_decision(decision_id, "executed", outcome)
            return {"result": result, "follow_result": follow_result}
        except Exception as exc:
            self.plane.store.update_decision(decision_id, "failed", {"error": str(exc), "task_id": task_id})
            raise

    def _queue_details(self) -> str:
        sections = []
        for name, media_type, client in (("Radarr", "movie", self.plane.planner.radarr), ("Sonarr", "series", self.plane.planner.sonarr)):
            include_key = "includeUnknownMovieItems" if media_type == "movie" else "includeUnknownSeriesItems"
            queue = client.get("api/v3/queue", {"page": 1, "pageSize": 100, include_key: "true"})
            records = queue.get("records", [])
            lines = [f"**{name} — {queue.get('totalRecords', len(records))} item(s)**"]
            if not records:
                lines.append("No active queue items.")
            for index, item in enumerate(records, 1):
                size = int(item.get("size") or 0)
                remaining = int(item.get("sizeleft") or 0)
                progress = round((1 - remaining / size) * 100, 1) if size else 0
                state = item.get("trackedDownloadState") or item.get("status") or "unknown"
                lines.append(f"{index}. `{item.get('title') or 'Untitled release'}`")
                lines.append(f"   {item.get('status') or 'unknown'} / {state} — {progress}% — {remaining / (1024 ** 3):.1f} GiB remaining")
                if item.get("timeleft") and item.get("timeleft") != "00:00:00":
                    lines.append(f"   Time left: {item['timeleft']}")
                if item.get("errorMessage"):
                    lines.append(f"   Error: {item['errorMessage']}")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    def _discovery(self) -> str:
        queue = self.plane.discovery_queue()[:10]
        if not queue:
            return "The discovery queue is empty. Run `!cine discover refresh` to generate candidates now, or wait for the daily worker."
        lines = [f"**Discovery queue — {len(queue)} shown**"]
        lines.extend(f"ID `{item['id']}` — **{item['title']} ({item.get('year') or 'n.d.'})** — score {item['score']}\nUse `!cine acquire {item['id']}` to create an add proposal." for item in queue)
        return "\n\n".join(lines)

    def _acquire_discovery(self, value: str, actor: str) -> str:
        try:
            candidate_id = int(value.strip())
        except ValueError:
            return "Usage: `!cine acquire CANDIDATE_ID`. Get IDs from `!cine discover`."
        candidate = self.plane.discovery.candidate(candidate_id) if self.plane.discovery else None
        if not candidate:
            return "Discovery candidate not found or already present in the library."
        result = self.plane.discovery_action(candidate_id, "approve", actor)
        subject = f"{candidate.get('title')} ({candidate.get('year') or 'n.d.'})"
        decision_id = self._record_decision(actor, "discovery_acquire", subject, "executed" if self._full_autopilot() else "pending", {"candidate_id": candidate_id, "score": candidate.get("score")}, {"task_id": result["task_id"]})
        if self._full_autopilot() or result.get("status") == "approved_and_executed":
            return f"Autopilot acquired and searched **{subject}**. Decision `{decision_id}`.\nMark it with `!cine feedback {decision_id} good` or `bad` so discovery learns."
        return f"Proposed discovery candidate `{candidate_id}`. Decision `{decision_id}`. Confirm the add with `!cine confirm {result['task_id']}`."

    def _propose_movie(self, value: str, actor: str) -> str:
        title, year = self._title_year(value)
        if not title:
            return "Usage: `!cine add Movie Title (Year)`"
        matches = self.plane.planner.radarr.get("api/v3/movie/lookup", {"term": title})
        candidates = [item for item in matches if isinstance(item, dict) and item.get("tmdbId")]
        candidate = DiscoveryEngine._select_match(candidates, title, year)
        if not candidate:
            return "I could not find an exact Radarr title/year match. Include the release year, for example `!cine add Heat (1995)`."
        stable_id = f"tmdb:{candidate['tmdbId']}"
        existing = self.plane.agents.tools.search_catalog(candidate.get("title", title), "movie", 50)
        if any(item.get("source_id") == stable_id for item in existing):
            return f"{candidate.get('title')} ({candidate.get('year')}) is already in Radarr."
        roots = self.plane.planner.radarr.get("api/v3/rootfolder")
        profiles = self.plane.planner.radarr.get("api/v3/qualityprofile")
        preferred = self.plane.store.get_policy("CINESWARM_AUTO_REQUIRED_QUALITY_PROFILE") or "HD-1080p"
        profile = next((item for item in profiles if item.get("name") == preferred), None)
        if not roots or not profile:
            return f"Radarr is missing the configured root folder or `{preferred}` quality profile."
        payload = {"media_type": "movie", "candidate": self.plane.planner._candidate(candidate, "movie"), "root_folder_path": roots[0]["path"], "quality_profile_id": profile["id"]}
        task_id = self.plane.store.create_task("radarr_add_request", actor, payload)
        decision_id = self._record_decision(actor, "discord_add", f"{candidate.get('title')} ({candidate.get('year')})", "pending", {"quality_profile": preferred, "tmdb_id": candidate.get("tmdbId")}, {"task_id": task_id})
        if self._full_autopilot():
            executed = self._execute_task(task_id, actor, decision_id)
            follow_result = executed.get("follow_result") or {}
            return f"Autopilot added and searched **{candidate.get('title')} ({candidate.get('year')})**. Decision `{decision_id}`; Radarr command `{follow_result.get('result', {}).get('id', 'submitted')}`."
        return f"Proposed adding **{candidate.get('title')} ({candidate.get('year')})** with `{preferred}`. Decision `{decision_id}`. Nothing has changed yet. Confirm with `!cine confirm {task_id}`."

    def _confirm(self, task_id: str, actor: str) -> str:
        task = self.plane.store.task(task_id)
        if not task:
            return "Task not found."
        if task.get("requested_by") != actor:
            return "That task was not created by your Discord identity."
        result = self.plane.approve_task(task_id, actor)
        follow_up = result.get("follow_up")
        if follow_up:
            return f"Add completed. Search is still pending and requires a second confirmation: `!cine confirm {follow_up['task_id']}`."
        return f"Task `{task_id}` completed successfully."

    def _decision_history(self) -> str:
        decisions = self.plane.store.decisions(10)
        if not decisions:
            return "No CineSwarm decisions have been recorded yet."
        lines = []
        for item in decisions:
            mark = (item.get("feedback") or {}).get("sentiment") or "none"
            lines.append(f"`{item['decision_id']}`\n**{item['decision'].upper()}** — {item['category']} — {item['subject']}\nFeedback: {mark} · {item['created_at']}")
        lines.append("Mark the latest with `!cine feedback last good` or `!cine feedback last bad`.")
        return "\n\n".join(lines)

    def _decision_detail(self, decision_id: str) -> str:
        item = self.plane.store.decision(decision_id.strip())
        if not item:
            return "Decision not found. Use `!cine decisions` to copy the full decision ID."
        feedback = next((entry.get("feedback") for entry in self.plane.store.decisions(100) if entry["decision_id"] == item["decision_id"]), None)
        return f"Decision `{item['decision_id']}`\nActor: {item['actor']}\nCategory: {item['category']}\nSubject: {item['subject']}\nDecision: **{item['decision']}**\nReasons: `{item['reasons']}`\nOutcome: `{item['outcome']}`\nFeedback: `{feedback or 'none'}`"

    def _profile(self, value: str, actor: str) -> str:
        profiles = getattr(self.plane, "user_profiles", None)
        if not profiles:
            return "Family profiles are not available."
        if not value:
            rows = profiles.list_all_profiles()
            active = next((row for row in rows if row.get("is_active")), {})
            lines = [f"**Active profile:** {active.get('display_name') or active.get('profile_id') or 'admin'}"]
            for row in rows:
                mark = " (active)" if row.get("is_active") else ""
                lines.append(f"- `{row.get('profile_id')}` {row.get('display_name')} · max {row.get('max_certification')}{mark}")
            lines.append("Switch with `!cine profile admin|partner|kids|guest`.")
            return "\n".join(lines)
        result = profiles.switch_active_profile(value)
        if result.get("status") != "success":
            return f"Could not switch profile. Use `admin`, `partner`, `kids`, or `guest`. {result.get('message') or ''}".strip()
        current = profiles.get_active_profile()
        return f"Active family profile is now **{current.get('display_name') or value}** (max {current.get('max_certification')}). Discovery and watch picks will use this rating ceiling."

    def _feedback(self, value: str, actor: str) -> str:
        parts = value.split(maxsplit=2)
        if len(parts) < 2 or parts[1].casefold() not in ("good", "bad"):
            return "Usage: `!cine feedback DECISION_ID good|bad optional note`"
        decision_id, sentiment = parts[0], parts[1].casefold()
        note = parts[2] if len(parts) > 2 else ""
        if decision_id.casefold() in {"last", "latest"}:
            recent = self.plane.store.decisions(1)
            if not recent:
                return "No decisions are available to mark yet."
            decision_id = recent[0]["decision_id"]
        if hasattr(self.plane, "record_decision_feedback"):
            saved = self.plane.record_decision_feedback(decision_id, sentiment, note, actor=actor)
        else:
            saved = self.plane.store.add_decision_feedback(decision_id, actor, sentiment, note)
        if not saved:
            return "Feedback was not saved. Check the decision ID and use `good` or `bad`."
        return f"Feedback saved for decision `{decision_id}`. Open discovery candidates were re-scored for this {sentiment} outcome."

    def handle(self, content: str, user_id: int) -> str:
        actor = f"discord:{user_id}"
        text = content.strip()
        lowered = text.casefold()
        if lowered in ("help", "commands"):
            return "Commands: `status`, `health`, `monitor`, `queue`, `discover`, `discover refresh`, `acquire ID`, `release RELEASE_NAME`, `grab RELEASE_NAME`, `add Movie Title (Year)`, `profile`, `profile admin|partner|kids|guest`, `decisions`, `decision ID`, `feedback last|ID good|bad NOTE`, `digest`, `scan_health`. Full autopilot executes paired-user actions immediately and logs every decision."
        if lowered in ("status", "health"):
            return self._status()
        if lowered in ("monitor", "ops", "live_monitor"):
            return self._monitor()
        if lowered == "decisions":
            return self._decision_history()
        if lowered.startswith("decision "):
            return self._decision_detail(text[9:].strip())
        if lowered.startswith("feedback "):
            return self._feedback(text[9:].strip(), actor)
        if lowered == "profile" or lowered.startswith("profile "):
            return self._profile(text[7:].strip(), actor)
        if lowered == "queue":
            return self._queue_details()
        if lowered == "discover refresh":
            result = self.plane.discovery_run(actor)
            return f"Discovery generated {result.get('generated', 0)} suggestion(s) and inserted {result.get('inserted', 0)} verified candidate(s).\n\n{self._discovery()}"
        if lowered in ("discover", "recommendations"):
            return self._discovery()
        if lowered.startswith("acquire "):
            return self._acquire_discovery(text[8:].strip(), actor)
        if lowered.startswith("release "):
            return self._release_status(text[8:].strip())
        if lowered.startswith("grab "):
            return self._propose_grab(text[5:].strip(), actor)
        if lowered.startswith("add "):
            return self._propose_movie(text[4:], actor)
        if lowered.startswith("confirm "):
            return self._confirm(text[8:].strip(), actor)
        if self._looks_like_release(text):
            return self._release_status(text)
        if lowered.startswith("plan "):
            query = text[5:].strip()
            plan = self.plane.planner.plan("movie", query) if self.plane.planner else {}
            if plan.get("found"):
                return f"**Target Acquisition Plan:** {plan.get('title')} ({plan.get('year')})\nTMDB ID: `{plan.get('tmdb_id')}`\nRoot Folder: `{plan.get('root_folders', [{}])[0].get('path')}`\nQuality Profile: `{plan.get('quality_profiles', [{}])[0].get('name')}`"
            return f"No target acquisition plan found for query `{query}`."
        if lowered == "franchise":
            result = self.plane.discovery.discover_missing_franchise_items(limit=5) if self.plane.discovery else {}
            return f"Franchise completeness scan finished: generated {result.get('generated', 0)}, inserted {result.get('inserted', 0)} missing franchise entry(ies).\n\n{self._discovery()}"
        if lowered in ("scan_plex", "scan_library", "refresh_plex", "plex_scan"):
            task_id = self.plane.store.create_task("plex_library_refresh", actor, {"reason": "Discord user requested Plex library scan"})
            res = self.plane.approve_task(task_id, actor)
            libs = ", ".join([f"**{lib['title']}**" for lib in res.get("result", {}).get("libraries", [])])
            return f"📡 **Plex Library Scan Initiated!**\nScanning libraries: {libs if libs else 'Flix, Shows'}\nPlex is currently parsing disk folders for new files & playlists."
        if lowered in ("scan_health", "health_scan", "check_health"):
            res = self.plane.scan_media_health(limit=30, actor=actor)
            return f"🛡️ **Media Health Integrity Scan Finished!**\nScanned: `{res['scanned']}` files | Healthy: `{res['healthy']}` | Corrupt/Missing: `{res['corrupt_count']}`"
        if lowered in ("sync_collections", "build_collections", "collections_sync"):
            res = self.plane.sync_native_collections(min_items=2, actor=actor)
            return f"📚 **Plex Native Franchise Collection Sync Complete!**\nBuilt **{res['collections_built']} collections** across **{res['items_tagged']} films** in your Plex library."
        if lowered.startswith("curate "):
            theme = text[7:].strip()
            res = self.plane.curate_collection(theme, limit=50, mode="collection", actor=actor)
            items_str = "\n".join([f"• **{item['title']}** ({item.get('year') or 'n.d.'})" for item in res.get("items", [])[:15]])
            more_count = max(0, len(res.get("items", [])) - 15)
            more_msg = f"\n*...and {more_count} more titles tagged in Plex!*" if more_count > 0 else ""
            return f"🎬 **Plex AI Collection Created: {res['collection_title']}**\n*{res['summary']}*\n\n**Tagged {res['tagged_count']} items in Plex:**\n{items_str if items_str else 'No direct library matches found.'}{more_msg}"
        if lowered.startswith("playlist "):
            theme = text[9:].strip()
            res = self.plane.curate_collection(theme, limit=50, mode="playlist", actor=actor)
            items_str = "\n".join([f"• **{item['title']}** ({item.get('year') or 'n.d.'})" for item in res.get("items", [])[:15]])
            more_count = max(0, len(res.get("items", [])) - 15)
            more_msg = f"\n*...and {more_count} more titles added to Plex Playlist!*" if more_count > 0 else ""
            return f"🎶 **Plex AI Playlist Created: {res['collection_title']}**\n*{res['summary']}*\n\n**Added {res['tagged_count']} items to Plex Playlist:**\n{items_str if items_str else 'No direct library matches found.'}{more_msg}"
        if lowered.startswith("watch"):
            args = text[5:].strip()
            max_mins = 120
            genre = ""
            m_mins = re.search(r"\b(\d+)\b", args)
            if m_mins:
                max_mins = int(m_mins.group(1))
                genre = re.sub(r"\b\d+\b", "", args).strip()
            else:
                genre = args
            res = self.plane.watch_recommendations(max_minutes=max_mins, genre=genre, actor=actor)
            recs = res.get("recommendations", [])
            lines = [f"🍿 **Gemini Movie Concierge Picks (Under {max_mins} mins{f' • {genre.title()}' if genre else ''}):**\n"]
            for i, m in enumerate(recs, 1):
                lines.append(f"**#{i} {m['title']} ({m.get('year') or 'n.d.'})** — `{m.get('duration_mins', 90)} mins`\n*{m.get('reason') or m.get('overview') or 'Excellent choice for tonight.'}*")
            return "\n\n".join(lines) if recs else "No matching movies found in your vault under those time/genre constraints."
        if lowered == "digest":
            res = self.plane.watch_recommendations(max_minutes=150, limit=3, actor=actor)
            counts = self.plane.store.catalog_counts()
            recs = res.get("recommendations", [])
            lines = [f"📰 **CineSwarm Daily Library Digest**\nVault Status: `{counts.get('movie', 0)} Movies` | `{counts.get('series', 0)} TV Series`\n\n**🎬 Top Picks for Today:**"]
            for i, m in enumerate(recs, 1):
                lines.append(f"• **{m['title']} ({m.get('year') or 'n.d.'})** ({m.get('duration_mins')}m)\n  *{m.get('reason') or m.get('overview') or 'Vault Spotlight.'}*")
            return "\n".join(lines)
        if lowered.startswith("filmography "):
            person = text[12:].strip()
            res = self.plane.discovery.scan_filmography_gaps(person, role="director") if self.plane.discovery else {}
            cands = res.get("candidates", [])
            if not cands:
                return f"✓ Filmography complete! No missing titles found for {person}."
            lines = [f"🎥 **Missing Filmography Titles for {person}:**"]
            for m in cands[:5]:
                lines.append(f"• **{m['title']} ({m.get('year') or 'n.d.'})** - {m.get('overview', '')[:100]}")
            return "\n".join(lines)
        if lowered.startswith("upgrade"):
            media_type = "movie"
            if "series" in lowered or "tv" in lowered:
                media_type = "series"
            res = self.plane.quality_analysis(media_type, actor=actor)
            return f"🎬 **Quality Analysis Summary ({media_type.upper()}):**\nManaged: `{res.get('managed', 0)}` | Upgrade Candidates: `{res.get('upgrade_candidate_count', 0)}`\nUse dashboard or `!cine plan TITLE` to trigger upgrades."
        result = self.plane.agents.ask(text, actor)
        return result.get("answer") or "CineSwarm returned no answer."




class TaskApprovalView(discord.ui.View):
    def __init__(self, service: DiscordService, task_id: str, author_id: int):
        super().__init__(timeout=86400)
        self.service = service
        self.task_id = task_id
        self.author_id = author_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="task_approve_btn")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.service.user_ids:
            await interaction.response.send_message("Unauthorized identity.", ephemeral=True)
            return
        await interaction.response.defer()
        actor = f"discord:{interaction.user.id}"
        try:
            response = await asyncio.to_thread(self.service._confirm, self.task_id, actor)
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(content=response, view=self)
        except Exception as exc:
            await interaction.followup.send(f"Approval failed: {exc}", ephemeral=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="task_reject_btn")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.service.user_ids:
            await interaction.response.send_message("Unauthorized identity.", ephemeral=True)
            return
        await interaction.response.defer()
        actor = f"discord:{interaction.user.id}"
        self.service.plane.store.update_task(self.task_id, "rejected")
        self.service.plane.store.audit(actor, "reject_task", "control-plane", "approval-gated", "rejected", {"task_id": self.task_id})
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(content=f"Task `{self.task_id}` rejected.", view=self)


class DiscoveryView(discord.ui.View):
    def __init__(self, service: DiscordService, queue_items: list[dict[str, Any]], author_id: int):
        super().__init__(timeout=86400)
        self.service = service
        self.queue_items = queue_items
        self.author_id = author_id

        if queue_items:
            options = [
                discord.SelectOption(
                    label=f"{item['title'][:80]} ({item.get('year') or 'n.d.'})",
                    description=f"Score: {item['score']} | {item.get('media_type', 'movie').upper()}",
                    value=str(item["id"]),
                )
                for item in queue_items[:25]
            ]
            select = discord.ui.Select(placeholder="Select candidate to approve & acquire...", options=options, custom_id="discovery_select")
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if interaction.user.id not in self.service.user_ids:
            await interaction.followup.send("Unauthorized identity.", ephemeral=True)
            return
        candidate_id = interaction.data["values"][0]
        actor = f"discord:{interaction.user.id}"
        try:
            response = await asyncio.to_thread(self.service._acquire_discovery, str(candidate_id), actor)
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(content=response, view=self)
        except Exception as exc:
            await interaction.followup.send(f"Acquisition failed: {exc}", ephemeral=True)


class CineSwarmClient(discord.Client):
    def __init__(self, service: DiscordService):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.service = service
        self.tree = discord.app_commands.CommandTree(self)
        self.watchdog_task: asyncio.Task[Any] | None = None
        self._register_slash_commands()

    def _register_slash_commands(self) -> None:
        @self.tree.command(name="status", description="Get real-time CineSwarm system, worker, and database status")
        async def slash_status(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "status", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="monitor", description="Live ops digest: overall health, queues, worker, and top issues")
        async def slash_monitor(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "monitor", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="budget", description="View current weekly acquisition budget telemetry")
        async def slash_budget(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "budget", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="search", description="Search catalog or plan new media acquisition")
        @discord.app_commands.describe(query="Title or search keywords to locate or acquire")
        async def slash_search(interaction: discord.Interaction, query: str):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, f"plan {query}", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="discover", description="Query Gemini neural discovery queue for recommendations")
        async def slash_discover(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "discover", interaction.user.id)
            queue_items = self.service.plane.discovery_queue()[:10]
            view = DiscoveryView(self.service, queue_items, interaction.user.id) if queue_items else None
            await interaction.followup.send(resp, view=view)

        @self.tree.command(name="franchise", description="Run AI franchise completeness scan to discover missing sequels/prequels")
        async def slash_franchise(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "franchise", interaction.user.id)
            queue_items = self.service.plane.discovery_queue()[:10]
            view = DiscoveryView(self.service, queue_items, interaction.user.id) if queue_items else None
            await interaction.followup.send(resp, view=view)

        @self.tree.command(name="curate", description="Create an AI-curated thematic Plex Collection automatically")
        @discord.app_commands.describe(theme="Theme or mood (e.g., '90s Cyberpunk Thrillers', 'Tarantino Masterpieces')")
        async def slash_curate(interaction: discord.Interaction, theme: str):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, f"curate {theme}", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="playlist", description="Create an AI-curated thematic Plex Playlist automatically")
        @discord.app_commands.describe(theme="Theme or mood (e.g., 'Friday Night Action', 'Mind-Bending Sci-Fi')")
        async def slash_playlist(interaction: discord.Interaction, theme: str):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, f"playlist {theme}", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="sync_collections", description="Automatically build native Plex Collections for all film sagas in your library")
        async def slash_sync_colls(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "sync_collections", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="commentary", description="Generate AI Director Commentary & Trivia subtitle track for a movie")
        @discord.app_commands.describe(title="Movie title to generate trivia overlay for")
        async def slash_commentary(interaction: discord.Interaction, title: str):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            actor = f"discord:{interaction.user.id}"
            res = await asyncio.to_thread(self.service.plane.generate_movie_commentary, title, actor=actor)
            sample_markers = "\n".join([f"`{m.get('timestamp', '00:00')}` **[{m.get('category', 'Trivia')}]** {m.get('commentary')}" for m in res.get("markers", [])[:3]])
            saved_note = f"\n\n📁 **Auto-injected into Plex media folder:** `{res['saved_path']}`" if res.get("saved_path") else ""
            msg = f"🎙️ **AI Director Commentary Generated: {res['movie_title']}**\nDirector: `{res.get('director')}` | Total Trivia Markers: `{res.get('marker_count')}`\n\n**Sample Markers:**\n{sample_markers}{saved_note}\n\n*Also attached as a `.srt` file below to download manually if desired!*"
            
            srt_bytes = io.BytesIO(res.get("srt", "").encode("utf-8"))
            filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', res['movie_title'])}.en.trivia.srt"
            file = discord.File(fp=srt_bytes, filename=filename)
            await interaction.followup.send(msg, file=file)

        @self.tree.command(name="chapters", description="Generate AI narrative chapter markers and scene summaries for a movie")
        @discord.app_commands.describe(title="Movie title to generate chapter breakdown for")
        async def slash_chapters(interaction: discord.Interaction, title: str):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            actor = f"discord:{interaction.user.id}"
            res = await asyncio.to_thread(self.service.plane.generate_movie_chapters, title, actor=actor)
            ch_list = "\n".join([f"`{ch.get('start_timestamp', '00:00:00')}` **{ch.get('title')}**\n*{ch.get('summary')}*" for ch in res.get("chapters", [])[:4]])
            saved_note = f"\n\n📁 **Auto-injected into Plex media folder:** `{res['saved_path']}`" if res.get("saved_path") else ""
            msg = f"📑 **Plex Smart Chapter Summaries Generated: {res['movie_title']}**\nTotal Chapters: `{res.get('chapter_count')}`\n\n**Sample Narrative Chapters:**\n{ch_list}{saved_note}\n\n*Also attached as a `.vtt` file below to download manually if desired!*"
            
            vtt_bytes = io.BytesIO(res.get("vtt", "").encode("utf-8"))
            filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', res['movie_title'])}.en.chapters.vtt"
            file = discord.File(fp=vtt_bytes, filename=filename)
            await interaction.followup.send(msg, file=file)

        @self.tree.command(name="watch", description="Get Gemini AI movie recommendations filtered by duration & genre")
        @discord.app_commands.describe(max_minutes="Maximum runtime in minutes (e.g., 90, 100, 120)", genre="Genre or theme (e.g., Action, Sci-Fi, Comedy)")
        async def slash_watch(interaction: discord.Interaction, max_minutes: int = 120, genre: str = ""):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            cmd_text = f"watch {max_minutes} {genre}".strip()
            resp = await asyncio.to_thread(self.service.handle, cmd_text, interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="digest", description="Post daily CineSwarm library digest & spotlight recommendations")
        async def slash_digest(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "digest", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="filmography", description="Scan TMDB for missing movies in a director or actor's filmography")
        @discord.app_commands.describe(name="Director or actor name (e.g., Edgar Wright, Quentin Tarantino)", role="Role (director or actor)")
        async def slash_filmography(interaction: discord.Interaction, name: str, role: str = "director"):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            actor_id = f"discord:{interaction.user.id}"
            res = await asyncio.to_thread(self.service.plane.scan_filmography, name, role, actor=actor_id)
            cands = res.get("candidates", [])
            if not cands:
                await interaction.followup.send(f"✓ **Filmography Complete:** No missing titles found in your vault for **{name}** ({role}).")
                return
            lines = [f"🎥 **Missing Filmography Titles for {name} ({role}):**\nFound `{res.get('missing_count')}` missing title(s) in TMDB.\n"]
            for m in cands[:8]:
                lines.append(f"• **{m['title']} ({m.get('year') or 'n.d.'})** — TMDB ID: `{m['tmdbId']}`\n  *{m.get('overview') or 'No summary available.'}*")
            lines.append("\n*Use `!cine add Movie Title (Year)` or dashboard to queue acquisition.*")
            await interaction.followup.send("\n".join(lines))

        @self.tree.command(name="scan_plex", description="Trigger immediate Plex library media refresh scan across Flix and Shows")
        async def slash_scan_plex(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "scan_plex", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="scan_health", description="Run video container & stream health integrity scan across library")
        async def slash_scan_health(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "scan_health", interaction.user.id)
            await interaction.followup.send(resp)

        @self.tree.command(name="decisions", description="View recent autonomous decisions logged by CineSwarm")
        async def slash_decisions(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            decs = await asyncio.to_thread(self.service.plane.get_recent_decisions, 8)
            if not decs:
                await interaction.followup.send("📜 **No recent autonomous decisions logged yet.**")
                return
            lines = ["📜 **Recent Autonomous Decisions & Reinforcement Feedback Log:**\n"]
            for d in decs:
                fb = f" [Feedback: **{d['sentiment'].upper()}**]" if d.get("sentiment") else ""
                lines.append(f"• `{d['created_at']}` **[{d.get('category', 'action').upper()}]** {d['subject']}\n  Decision: `{d['decision']}`{fb}")
            lines.append("\n*Use Web Dashboard to submit 👍 upvote or 👎 downvote feedback.*")
            await interaction.followup.send("\n".join(lines))

        @self.tree.command(name="emergency_stop", description="Toggle CineSwarm autonomous emergency halt")
        async def slash_stop(interaction: discord.Interaction):
            if not self.service.authorized(interaction.user.id, interaction.guild.id if interaction.guild else None, interaction.channel_id):
                await interaction.response.send_message("Unauthorized Discord identity.", ephemeral=True)
                return
            await interaction.response.defer()
            resp = await asyncio.to_thread(self.service.handle, "emergency_stop", interaction.user.id)
            await interaction.followup.send(resp)

    async def setup_hook(self) -> None:
        self.watchdog_task = asyncio.create_task(self._watchdog())
        if self.service.guild_ids:
            for guild_id in self.service.guild_ids:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def _watchdog(self) -> None:
        while not self.is_closed():
            sd_notify("WATCHDOG=1")
            await asyncio.sleep(30)

    async def on_ready(self) -> None:
        sd_notify("READY=1\nSTATUS=CineSwarm Discord bot connected")
        self.service.plane.store.audit("discord-bot", "discord_ready", "discord", "read-only", "connected", {"guild_count": len(self.guilds)})
        print(f"CineSwarm Discord bot connected as {self.user}", flush=True)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        content = message.content.strip()
        mentioned = bool(self.user and self.user.mentioned_in(message))
        prefixed = content.casefold().startswith(self.service.prefix.casefold())
        if prefixed:
            content = content[len(self.service.prefix):].strip()
        if self.user:
            content = re.sub(rf"<@!?{self.user.id}>", "", content).strip()
        if content.casefold().startswith("pair ") and (prefixed or mentioned):
            response = self.service.pair(content[5:].strip(), message.author.id, message.guild.id if message.guild else None, message.channel.id)
            await message.channel.send(response, allowed_mentions=discord.AllowedMentions.none())
            return
        if content.casefold().startswith("move ") and (prefixed or mentioned):
            response = self.service.move_channel(content[5:].strip(), message.author.id, message.guild.id if message.guild else None, message.channel.id)
            await message.channel.send(response, allowed_mentions=discord.AllowedMentions.none())
            return
        if not self.service.authorized(message.author.id, message.guild.id if message.guild else None, message.channel.id):
            return
        if self.service.require_mention and not mentioned and not prefixed:
            return
        if not content:
            content = "help"
        async with message.channel.typing():
            try:
                response = await asyncio.to_thread(self.service.handle, content, message.author.id)
            except (AgentError, ServiceError, ValueError) as exc:
                response = f"Request failed: {exc}"
            except Exception as exc:
                self.service.plane.store.audit(f"discord:{message.author.id}", "discord_request", "discord", "read-only", "error", {"error": str(exc)})
                response = "The request failed unexpectedly. Check the CineSwarm audit log."

        view = None
        match = re.search(r"Confirm with `!cine confirm ([\w-]+)`", response)
        if match:
            task_id = match.group(1)
            view = TaskApprovalView(self.service, task_id, message.author.id)
        else:
            match_disc = re.search(r"Use `!cine acquire (\d+)`", response)
            if match_disc:
                cand_id = int(match_disc.group(1))
                view = DiscoveryView(self.service, cand_id, message.author.id)

        file = None
        if content.casefold().startswith("commentary "):
            t = content[11:].strip()
            try:
                res = self.service.plane.generate_movie_commentary(t, actor=f"discord:{message.author.id}")
                srt_bytes = io.BytesIO(res.get("srt", "").encode("utf-8"))
                filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', res['movie_title'])}.en.trivia.srt"
                file = discord.File(fp=srt_bytes, filename=filename)
            except Exception:
                pass

        for start in range(0, len(response), 1900):
            chunk = response[start:start + 1900]
            if start + 1900 >= len(response):
                await message.channel.send(chunk, view=view, file=file, allowed_mentions=discord.AllowedMentions.none())
            else:
                await message.channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())


    async def close(self) -> None:
        sd_notify("STOPPING=1")
        if self.watchdog_task:
            self.watchdog_task.cancel()
        await super().close()


def main() -> None:
    token = os.environ.get("CINESWARM_DISCORD_BOT_TOKEN", "")
    service = DiscordService()
    if not token:
        raise SystemExit("CINESWARM_DISCORD_BOT_TOKEN is not configured")
    if not service.configured() and not service.setup_code:
        raise SystemExit("Configure Discord IDs or CINESWARM_DISCORD_SETUP_CODE for one-time pairing")
    CineSwarmClient(service).run(token, log_handler=None)


if __name__ == "__main__":
    main()
