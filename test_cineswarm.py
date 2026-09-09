import json
import os
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import patch

from cineswarm_agents import AcquisitionPlanner, PlaybackHistory, ReadOnlyTools
from cineswarm_control import ControlPlane, ControlStore, LOCAL_ENV_KEYS, PlexConnector, Policy, service_error_message
from cineswarm_discovery import DiscoveryEngine
from cineswarm_discord import DiscordService
from cineswarm_preservation import collect_mount_health, collect_storage_events, mapped_path, online_backup, preservation_scan, resolve_physical_path, restore_database, rotate_backups
from cineswarm_worker import Worker, WorkerStore


class FakeArrClient:
    def __init__(self, queue=None):
        self.queue = queue or {"records": []}
        self.put_calls = []
        self.post_calls = []

    def get(self, path, params=None):
        if path == "api/v3/queue":
            return self.queue
        if path == "api/v3/qualityprofile":
            return [{"id": 1, "name": "Current"}, {"id": 2, "name": "Fallback"}]
        if path.startswith("api/v3/movie/"):
            return {"id": 42, "qualityProfileId": 1}
        raise AssertionError(path)

    def put_json(self, path, payload):
        self.put_calls.append((path, payload.copy()))
        return payload

    def post_json(self, path, payload):
        self.post_calls.append((path, payload.copy()))
        return {"id": 99}


class FakeControlStore:
    def __init__(self):
        self.tasks = []

    def task_exists(self, task_type, parent_task_id):
        return any(task[0] == task_type and task[2]["parent_task_id"] == parent_task_id for task in self.tasks)

    def create_task(self, task_type, actor, payload):
        self.tasks.append((task_type, actor, payload))
        return f"task-{len(self.tasks)}"

    def get_policy(self, key):
        return None


class PlexTests(unittest.TestCase):
    def test_connector_normalizes_modern_and_legacy_guids(self):
        element = ET.fromstring("""<Video title="Example" year="2020" type="movie" guid="com.plexapp.agents.themoviedb://123?lang=en"><Guid id="imdb://tt123"/><Genre tag="Drama"/><Media><Part file="/media/Example.mkv"/></Media></Video>""")

        item = PlexConnector._item(element, "movie")

        self.assertEqual(item["ProviderIds"], {"tmdb": "123", "imdb": "tt123"})
        self.assertEqual(item["Path"], "/media/Example.mkv")
        self.assertEqual(item["Genres"], ["Drama"])

    def test_playback_history_builds_profile_from_plex_xml(self):
        history = PlaybackHistory("http://plex", "token")
        history.get_watched_items = lambda limit=200: ET.fromstring("""<MediaContainer><Video type="movie" title="Example" year="2020" viewCount="2" lastViewedAt="1" studio="Studio"><Genre tag="Drama"/><Director tag="Director"/><Role tag="Actor"/></Video></MediaContainer>""")

        profile = history.build_taste_profile()

        self.assertEqual(profile["total_watched"], 1)
        self.assertEqual(profile["total_plays"], 2)
        self.assertEqual(profile["top_genres"], [("Drama", 2)])



    def test_refresh_libraries_refreshes_matching_and_falls_back(self):
        from cineswarm_control import PlexWriteClient, ServiceConfig

        calls = []

        class FakeWriter(PlexWriteClient):
            def __init__(self):
                self.config = ServiceConfig(name="plex", url="http://plex", api_key="t", header="X-Plex-Token")
                self.timeout = 1

            def get(self, path, params=None):
                root = ET.Element("MediaContainer")
                movie = ET.SubElement(root, "Directory", key="1", title="Flix", type="movie")
                ET.SubElement(movie, "Location", path="/data/Movies")
                show = ET.SubElement(root, "Directory", key="2", title="Shows", type="show")
                ET.SubElement(show, "Location", path="/data/TV")
                return root

            def post(self, path, params=None):
                calls.append(path)
                return ET.Element("MediaContainer")

        writer = FakeWriter()
        matched = writer.refresh_libraries("/data/Movies/Foo (2020)")
        self.assertEqual(matched["refreshed_count"], 1)
        self.assertEqual(calls, ["library/sections/1/refresh"])

        calls.clear()
        fallback = writer.refresh_libraries("/media/Movies/Foo (2020)")
        self.assertEqual(fallback["refreshed_count"], 2)
        self.assertEqual(
            calls,
            ["library/sections/1/refresh", "library/sections/2/refresh"],
        )

        calls.clear()
        all_sections = writer.refresh_libraries(None)
        self.assertEqual(all_sections["refreshed_count"], 2)


class AcquisitionPlannerTests(unittest.TestCase):
    def test_list_shaped_service_errors_are_preserved(self):
        message = service_error_message([{"errorMessage": "Movie already exists"}, {"message": "Duplicate TMDB ID"}])

        self.assertEqual(message, "Movie already exists; Duplicate TMDB ID")

    def test_edition_keys_support_set_reconciliation(self):
        keys = ReadOnlyTools._edition_key({"tmdbId": 123, "title": "Example Director's Cut"}, "movie")

        self.assertIsInstance(keys, set)
        self.assertEqual(keys & {("tmdb", "123")}, {("tmdb", "123")})
        self.assertIn(("edition_key", "edition:Director's Cut"), keys)

    def test_plex_provider_ids_match_arr_ids(self):
        plex_keys = ReadOnlyTools._plex_ids({"ProviderIds": {"Tmdb": "123"}}, "movie")
        radarr_keys = ReadOnlyTools._edition_key({"tmdbId": 123}, "movie")

        self.assertEqual(plex_keys & radarr_keys, {("tmdb", "123")})

    def test_monitor_search_treats_queue_item_as_active_download(self):
        class MonitorClient:
            def get(self, path, params=None):
                if path == "api/v3/command/9":
                    return {"status": "completed", "result": "successful"}
                if path == "api/v3/movie/42":
                    return {"id": 42, "title": "Downloading", "hasFile": False}
                if path == "api/v3/queue":
                    return {"records": [{"movieId": 42, "status": "downloading", "trackedDownloadState": "downloading"}]}
                raise AssertionError(path)

        result = AcquisitionPlanner(MonitorClient(), MonitorClient(), None).monitor_search({"media_type": "movie", "service_id": 42}, {"id": 9})

        self.assertEqual(result["state"], "download_active")
        self.assertEqual(result["queue_status"], "downloading")

    def test_failed_downloads_returns_only_failed_records_with_service_ids(self):
        queue = {
            "records": [
                {"id": 10, "movieId": 42, "title": "Failed", "status": "failed", "qualityProfileId": 3},
                {"id": 11, "movieId": 43, "title": "Downloading", "status": "downloading"},
            ]
        }
        planner = AcquisitionPlanner(FakeArrClient(queue), FakeArrClient(), None)

        self.assertEqual(
            planner.get_failed_downloads("movie"),
            [{
                "media_type": "movie",
                "service_id": 42,
                "queue_id": 10,
                "title": "Failed",
                "status": "failed",
                "tracked_download_state": None,
                "error_message": None,
                "quality_profile_id": 3,
                "quality": None,
            }],
        )

    def test_add_uses_tools_for_duplicate_keys(self):
        candidate = {"title": "Missing", "year": 2020, "tmdbId": 123}

        class AddClient:
            def get(self, path, params=None):
                if path == "api/v3/rootfolder":
                    return [{"path": "/movies"}]
                if path == "api/v3/qualityprofile":
                    return [{"id": 1}]
                if path == "api/v3/movie/lookup":
                    return [candidate]
                raise AssertionError(path)

            def post_json(self, path, payload):
                return {"id": 42, **payload}

        tools = SimpleNamespace(search_catalog=lambda *args: [], _edition_key=ReadOnlyTools._edition_key)
        result = AcquisitionPlanner(AddClient(), AddClient(), tools).add({"media_type": "movie", "candidate": candidate, "root_folder_path": "/movies", "quality_profile_id": 1})

        self.assertEqual(result["id"], 42)
        self.assertFalse(result["addOptions"]["searchForMovie"])

    def test_release_grab_revalidates_and_posts_minimal_payload(self):
        calls = []

        class ReleaseClient:
            def get(self, path, params=None):
                if path == "api/v3/movie/42":
                    return {"id": 42, "title": "Movie"}
                if path == "api/v3/queue":
                    return {"records": []}
                if path == "api/v3/release":
                    return [{"title": "Movie.2020.2160p.REMUX", "guid": "guid", "indexerId": 4, "indexer": "Indexer", "size": 100, "rejections": ["warning"]}]
                raise AssertionError(path)

            def post_json(self, path, payload):
                calls.append((path, payload))
                return {"accepted": True}

        planner = AcquisitionPlanner(ReleaseClient(), ReleaseClient(), None)
        result = planner.grab_release({"movie_id": 42, "guid": "guid", "indexer_id": 4, "title": "Movie.2020.2160p.REMUX"})

        self.assertEqual(calls, [("api/v3/release", {"guid": "guid", "indexerId": 4})])
        self.assertEqual(result["title"], "Movie.2020.2160p.REMUX")
        self.assertEqual(result["rejections"], ["warning"])

    def test_quality_fallback_uses_put_json_before_search(self):
        client = FakeArrClient()
        planner = AcquisitionPlanner(client, FakeArrClient(), None)

        result = planner.retry_with_quality_fallback({
            "media_type": "movie",
            "service_id": 42,
            "quality_profile_id": 1,
            "fallback_order": ["Fallback"],
        })

        self.assertEqual(result["status"], "retry_triggered")
        self.assertEqual(client.put_calls[0][0], "api/v3/movie/42")
        self.assertEqual(client.put_calls[0][1]["qualityProfileId"], 2)
        self.assertEqual(client.post_calls[0][1], {"name": "MoviesSearch", "movieIds": [42]})

    def test_global_queue_pressure_includes_sonarr_and_deduplicates_downloads(self):
        radarr = FakeArrClient({"records": [{"id": 1, "downloadId": "shared", "title": "Movie", "status": "downloading"}]})
        sonarr = FakeArrClient({"records": [{"id": 2, "downloadId": "shared", "title": "Episode", "status": "downloading"}, {"id": 3, "downloadId": "series-only", "title": "Episode 2", "status": "queued"}, {"id": 4, "downloadId": "import-pending", "title": "Episode 3", "status": "completed", "trackedDownloadState": "importPending"}]})
        with patch.dict("os.environ", {"SABNZBD_URL": "", "SABNZBD_API_KEY": ""}):
            pressure = AcquisitionPlanner(radarr, sonarr, None).global_queue_pressure(3)

        self.assertTrue(pressure["pressured"])
        self.assertEqual(pressure["active"], 3)
        self.assertEqual(pressure["sources"], {"radarr": 1, "sonarr": 3, "sabnzbd": 0})
        self.assertFalse(pressure["sabnzbd"]["configured"])


class DiscordServiceTests(unittest.TestCase):
    def test_release_name_parser_extracts_movie_and_year(self):
        release = "The.Big.Lebowski.1998.BluRay.2160p.DV.HDR.REMUX"

        self.assertEqual(DiscordService._release_title_year(release), ("The Big Lebowski", 1998))
        self.assertTrue(DiscordService._looks_like_release(release))

    def test_authorization_requires_matching_guild_channel_and_user(self):
        environment = {"CINESWARM_DISCORD_GUILD_IDS": "1", "CINESWARM_DISCORD_CHANNEL_IDS": "2", "CINESWARM_DISCORD_USER_IDS": "3"}
        with patch.dict("os.environ", environment, clear=False):
            service = DiscordService(SimpleNamespace())

        self.assertTrue(service.configured())
        self.assertTrue(service.authorized(3, 1, 2))
        self.assertFalse(service.authorized(4, 1, 2))
        self.assertFalse(service.authorized(3, 1, 9))

    def test_one_time_pairing_captures_discord_ids(self):
        policies = {}
        store = SimpleNamespace(get_policy=lambda key: policies.get(key), set_policy=lambda key, value: policies.__setitem__(key, value), audit=lambda *args: None)
        environment = {"CINESWARM_DISCORD_GUILD_IDS": "", "CINESWARM_DISCORD_CHANNEL_IDS": "", "CINESWARM_DISCORD_USER_IDS": "", "CINESWARM_DISCORD_SETUP_CODE": "pair-code"}
        with patch.dict("os.environ", environment, clear=False):
            service = DiscordService(SimpleNamespace(store=store))
            response = service.pair("pair-code", 3, 1, 2)

        self.assertIn("paired successfully", response)
        self.assertTrue(service.authorized(3, 1, 2))
        self.assertEqual(policies["CINESWARM_DISCORD_GUILD_IDS"], "1")
        self.assertEqual(policies["CINESWARM_DISCORD_CHANNEL_IDS"], "2")
        self.assertEqual(policies["CINESWARM_DISCORD_USER_IDS"], "3")

        moved = service.move_channel("pair-code", 3, 1, 9)

        self.assertIn("moved successfully", moved)
        self.assertFalse(service.authorized(3, 1, 2))
        self.assertTrue(service.authorized(3, 1, 9))
        self.assertEqual(policies["CINESWARM_DISCORD_CHANNEL_IDS"], "9")

    def test_queue_command_lists_actual_downloads_and_progress(self):
        queue = {"totalRecords": 1, "records": [{"title": "Movie.2020.1080p", "status": "downloading", "trackedDownloadState": "downloading", "size": 100, "sizeleft": 25, "timeleft": "00:10:00", "errorMessage": ""}]}
        client = SimpleNamespace(get=lambda path, params=None: queue)
        plane = SimpleNamespace(planner=SimpleNamespace(radarr=client, sonarr=client))

        response = DiscordService(plane).handle("queue", 3)

        self.assertIn("Movie.2020.1080p", response)
        self.assertIn("75.0%", response)
        self.assertIn("Time left: 00:10:00", response)
        self.assertIn("Radarr", response)
        self.assertIn("Sonarr", response)

    def test_discovery_acquire_creates_pending_add_proposal(self):
        calls = []
        store = SimpleNamespace(get_policy=lambda key: None, record_decision=lambda *args: "decision-7")
        discovery = SimpleNamespace(candidate=lambda candidate_id: {"title": "Candidate", "year": 2020, "score": 95})
        plane = SimpleNamespace(store=store, discovery=discovery, discovery_action=lambda candidate_id, action, actor: calls.append((candidate_id, action, actor)) or {"task_id": "task-7"})

        response = DiscordService(plane).handle("acquire 7", 3)

        self.assertIn("task-7", response)
        self.assertIn("Confirm the add with", response)
        self.assertEqual(calls, [(7, "approve", "discord:3")])

    def test_add_command_creates_pending_task_without_executing(self):
        tasks = []

        class Radarr:
            def get(self, path, params=None):
                if path == "api/v3/movie/lookup":
                    return [{"title": "Heat", "year": 1995, "tmdbId": 949, "genres": ["Crime"]}]
                if path == "api/v3/rootfolder":
                    return [{"path": "/movies"}]
                if path == "api/v3/qualityprofile":
                    return [{"id": 1, "name": "HD-1080p"}]
                raise AssertionError(path)

        store = SimpleNamespace(get_policy=lambda key: "HD-1080p", create_task=lambda *args: tasks.append(args) or "task-1", record_decision=lambda *args: "decision-1")
        tools = SimpleNamespace(search_catalog=lambda *args: [])
        planner = SimpleNamespace(radarr=Radarr(), _candidate=AcquisitionPlanner._candidate)
        plane = SimpleNamespace(store=store, planner=planner, agents=SimpleNamespace(tools=tools))
        service = DiscordService(plane)

        response = service.handle("add Heat (1995)", 3)

        self.assertIn("Nothing has changed yet", response)
        self.assertIn("task-1", response)
        self.assertEqual(tasks[0][0], "radarr_add_request")
        self.assertEqual(tasks[0][1], "discord:3")

    def test_full_autopilot_executes_add_and_search_without_confirmation(self):
        tasks = []
        decisions = []

        class Radarr:
            def get(self, path, params=None):
                if path == "api/v3/movie/lookup":
                    return [{"title": "Heat", "year": 1995, "tmdbId": 949, "genres": ["Crime"]}]
                if path == "api/v3/rootfolder":
                    return [{"path": "/movies"}]
                if path == "api/v3/qualityprofile":
                    return [{"id": 1, "name": "HD-1080p"}]
                raise AssertionError(path)

        store = SimpleNamespace(
            get_policy=lambda key: "true" if key == "CINESWARM_FULL_AUTOPILOT" else "HD-1080p" if key == "CINESWARM_AUTO_REQUIRED_QUALITY_PROFILE" else None,
            create_task=lambda *args: tasks.append(args) or "add-task",
            record_decision=lambda *args: "decision-1",
            update_decision=lambda *args: decisions.append(args),
        )
        tools = SimpleNamespace(search_catalog=lambda *args: [])
        planner = SimpleNamespace(radarr=Radarr(), _candidate=AcquisitionPlanner._candidate)
        plane = SimpleNamespace(
            store=store,
            planner=planner,
            agents=SimpleNamespace(tools=tools),
            approve_task=lambda task_id, actor: {"result": {"id": 42}, "follow_up": {"task_id": "search-task"}} if task_id == "add-task" else {"result": {"id": 99}, "follow_up": None},
        )

        response = DiscordService(plane).handle("add Heat (1995)", 3)

        self.assertIn("Autopilot added and searched", response)
        self.assertEqual(decisions[0][1], "executed")

    def test_confirmation_rejects_another_identity_task(self):
        store = SimpleNamespace(task=lambda task_id: {"requested_by": "discord:4"})
        service = DiscordService(SimpleNamespace(store=store))

        self.assertIn("not created by your Discord identity", service.handle("confirm task-1", 3))

    def test_discord_views_and_buttons(self):
        from cineswarm_discord import TaskApprovalView, DiscoveryView
        service = DiscordService(SimpleNamespace())
        service.user_ids = {3}

        task_view = TaskApprovalView(service, "task-100", 3)
        disc_view = DiscoveryView(service, [{"id": 50, "title": "Test Title", "year": 2024, "score": 95, "media_type": "movie"}], 3)

        self.assertEqual(len(task_view.children), 2)
        self.assertEqual(len(disc_view.children), 1)



class DiscoveryTests(unittest.TestCase):
    def test_candidate_score_schema_migrates_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            control = f"{directory}/control.db"
            with sqlite3.connect(control) as connection:
                connection.execute("CREATE TABLE discovery_candidates (id INTEGER PRIMARY KEY, candidate_key TEXT UNIQUE, media_type TEXT, title TEXT, year INTEGER, external_ids_json TEXT, genres_json TEXT, score REAL, rationale TEXT, status TEXT, source TEXT, raw_json TEXT, created_at TEXT, updated_at TEXT)")
                connection.execute("INSERT INTO discovery_candidates VALUES (1, 'key', 'movie', 'Old', 2000, '{}', '[]', 77, '', 'new', 'legacy', '{}', '', '')")
            with patch("cineswarm_discovery.CONTROL_DB", control):
                DiscoveryEngine(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
                DiscoveryEngine(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
            with sqlite3.connect(control) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(discovery_candidates)")}
                migrated = connection.execute("SELECT score, overall_score FROM discovery_candidates").fetchone()

        self.assertTrue({"watch_affinity_score", "collection_significance_score", "rarity_preservation_score", "storage_cost_score", "acquisition_confidence_score", "overall_score", "score_reasons_json"} <= columns)
        self.assertEqual(migrated, (77, 77))

    def test_lookup_rejects_wrong_title_or_year(self):
        matches = [{"title": "The Green Room", "year": 1978}, {"title": "Green Room", "year": 2015}]

        self.assertEqual(DiscoveryEngine._select_match(matches, "Green Room", 2015), matches[1])
        self.assertIsNone(DiscoveryEngine._select_match(matches, "Green Room", 1978))
        self.assertIsNone(DiscoveryEngine._select_match(matches, "Green Room", 2020))

    def test_queue_filters_candidates_already_in_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = f"{directory}/catalog.db"
            control = f"{directory}/control.db"
            with sqlite3.connect(catalog) as connection:
                connection.execute("CREATE TABLE catalog_items (source_id TEXT, title TEXT, present INTEGER)")
                connection.execute("INSERT INTO catalog_items VALUES ('tmdb:123', 'Owned', 1)")
            with sqlite3.connect(control) as connection:
                connection.execute("CREATE TABLE discovery_candidates (id INTEGER, media_type TEXT, title TEXT, year INTEGER, external_ids_json TEXT, score REAL, rationale TEXT, status TEXT, created_at TEXT, updated_at TEXT)")
                connection.execute("INSERT INTO discovery_candidates VALUES (1, 'movie', 'Owned', 2020, '{\"tmdbId\": 123}', 99, 'owned', 'new', '', '')")
                connection.execute("INSERT INTO discovery_candidates VALUES (2, 'movie', 'Missing', 2021, '{\"tmdbId\": 456}', 90, 'missing', 'new', '', '')")
                connection.execute("INSERT INTO discovery_candidates VALUES (3, 'movie', 'Plex Owned', 2022, '{\"tmdbId\": 789}', 95, 'plex owned', 'new', '', '')")
            engine = DiscoveryEngine.__new__(DiscoveryEngine)
            engine.store = SimpleNamespace(snapshot_payload=lambda service: {"items": [{"Type": "Movie", "Name": "Plex Owned", "ProviderIds": {}}]})
            with patch("cineswarm_discovery.CATALOG_DB", catalog), patch("cineswarm_discovery.CONTROL_DB", control):
                queue = engine.queue()

        self.assertEqual([item["title"] for item in queue], ["Missing"])
        self.assertEqual(DiscoveryEngine._stable_key("movie", {"tmdbId": 123}), "tmdb:123")

    def test_score_components_are_bounded_and_explainable(self):
        engine = DiscoveryEngine.__new__(DiscoveryEngine)
        candidate = {"title": "Rare Cut", "year": 2020, "tmdbId": 7, "genres": ["Drama"], "runtime": 5000, "reason": "rare restored limited edition", "collection": "Archive"}
        profile = {"top_genres": [("Drama", 1000)], "top_decades": [("2020s", 1000)], "decision_feedback": []}

        scores = engine._score_components(candidate, profile)

        component_keys = [key for key in scores if key.endswith("_score")]
        self.assertTrue(component_keys)
        self.assertTrue(all(0 <= scores[key] <= 100 for key in component_keys))
        self.assertEqual(engine._score(candidate, profile), scores["overall_score"])
        self.assertEqual({reason["component"] for reason in scores["score_reasons"]}, {"watch_affinity", "collection_significance", "rarity_preservation", "storage_cost", "acquisition_confidence", "overall"})

    def test_decision_feedback_deterministically_changes_affinity(self):
        engine = DiscoveryEngine.__new__(DiscoveryEngine)
        candidate = {"title": "Moon Garden", "year": 2020, "tmdbId": 8, "genres": ["Drama"]}
        base = {"top_genres": [("Drama", 2)], "top_decades": [("2020s", 1)]}
        good = {**base, "decision_feedback": [{"subject": "Moon Garden", "note": "", "sentiment": "good"}]}
        bad = {**base, "decision_feedback": [{"subject": "Moon Garden", "note": "", "sentiment": "bad"}]}

        good_score = engine._score_components(candidate, good)
        bad_score = engine._score_components(candidate, bad)

        self.assertGreater(good_score["watch_affinity_score"], bad_score["watch_affinity_score"])
        self.assertGreater(good_score["overall_score"], bad_score["overall_score"])
        self.assertEqual(good_score, engine._score_components(candidate, good))

    def test_learned_taste_and_genre_feedback_transfer_affect_affinity(self):
        engine = DiscoveryEngine.__new__(DiscoveryEngine)
        candidate = {"title": "Neon Drift", "year": 2018, "tmdbId": 9, "genres": ["Science Fiction"], "runtime": 110}
        base = {"top_genres": [("Science Fiction", 2)], "top_decades": [("2010s", 1)], "decision_feedback": [], "learned_taste_profile": {}}
        boosted = {
            **base,
            "learned_taste_profile": {"science fiction": 3.0},
            "decision_feedback": [{"subject": "Other Film", "note": "more science fiction please", "sentiment": "good", "reasons": {"genres": ["Science Fiction"]}}],
        }
        base_score = engine._score_components(candidate, base)
        boosted_score = engine._score_components(candidate, boosted)
        self.assertGreater(boosted_score["watch_affinity_score"], base_score["watch_affinity_score"])
        self.assertGreater(boosted_score["overall_score"], base_score["overall_score"])


class ControlPlaneTests(unittest.TestCase):
    def test_emergency_stop_blocks_automatic_write_execution(self):
        calls = []
        plane = ControlPlane.__new__(ControlPlane)
        plane.store = SimpleNamespace(is_emergency_stop=lambda: True)
        plane.actions = {"plex_library_refresh": lambda payload: calls.append(payload)}

        with patch.dict("os.environ", {"CINESWARM_AUTO_EMERGENCY_STOP": "false"}):
            result = plane.execute_automatic("plex_library_refresh", "worker")

        self.assertEqual(result, {"status": "blocked_emergency_stop"})
        self.assertEqual(calls, [])

    def test_stale_search_commands_do_not_fail_queue_monitoring(self):
        saved = []
        store = SimpleNamespace(
            completed_search_tasks=lambda: [{"task_id": "old", "payload": {"media_type": "movie", "service_id": 42}, "result": {"id": 9}}],
            save_acquisition_observation=lambda task_id, state, details: saved.append((task_id, state, details)),
            task_exists=lambda task_type, parent_task_id: False,
            audit=lambda *args: None,
        )
        planner = SimpleNamespace(monitor_search=lambda payload, result: (_ for _ in ()).throw(RuntimeError("command expired")))
        plane = ControlPlane.__new__(ControlPlane)
        plane.store = store
        plane.planner = planner

        result = plane.monitor_acquisitions("test")

        self.assertEqual(result["summary"], {"monitor_unavailable": 1})
        self.assertEqual(saved[0][1], "monitor_unavailable")


class WorkerTests(unittest.TestCase):
    def test_multiple_editions_are_preserved_as_distinct_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ControlStore(f"{directory}/control.db")
            first = store.add_edition("Theatrical Cut", catalog_item_id=12, source_release_name="Film.1080p", authenticity_confidence=88, backup_status="backed_up")
            second = store.add_edition("Director's Cut", catalog_item_id=12, source_release_name="Film.Directors.Cut.2160p", authenticity_confidence=97, rarity_flag=True)
            editions = store.editions(catalog_item_id=12)

        self.assertNotEqual(first, second)
        self.assertEqual({item["edition_label"] for item in editions}, {"Theatrical Cut", "Director's Cut"})
        self.assertTrue(next(item for item in editions if item["id"] == second)["rarity_flag"])

    def test_operational_summary_consolidates_recent_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/control.db"
            store = ControlStore(path)
            store.audit("test", "refresh", "plex", "read-only", "success")
            task_id = store.create_task("radarr_search_retry_request", "test", {})
            store.update_task(task_id, "failed", {"error": "no release"})
            store.save_acquisition_observation(task_id, "imported", {})
            summary = store.operational_summary()

        self.assertEqual(summary["audit"]["successes"], 1)
        self.assertEqual(summary["tasks"]["failures"], 1)
        self.assertEqual(summary["tasks"]["retries"], 1)
        self.assertEqual(summary["acquisitions"]["imports"], 1)
        self.assertTrue(summary["actionable_issues"])
        self.assertIn("storage", summary)
        self.assertIn("queue", summary)

    def test_decision_ledger_records_outcome_and_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ControlStore(f"{directory}/control.db")
            decision_id = store.record_decision("worker", "discovery", "Movie", "pending", {"score": 95}, {})
            store.update_decision(decision_id, "executed", {"service_id": 42})
            saved = store.add_decision_feedback(decision_id, "discord:3", "good", "Great pick")
            item = store.decisions(1)[0]

        self.assertTrue(saved)
        self.assertEqual(item["decision"], "executed")
        self.assertEqual(item["outcome"], {"service_id": 42})
        self.assertEqual(item["feedback"]["sentiment"], "good")

    def test_budget_increment_records_movie_and_gigabytes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ControlStore(f"{directory}/control.db")
            store.increment_budget("movie", 20)
            store.increment_budget("movie", 20)

            usage = store.get_budget_usage()

        self.assertEqual(usage, {"movies_added": 2, "series_added": 0, "gb_added": 40})

    def test_completed_retry_clears_stale_error(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkerStore(f"{directory}/worker.db")
            store.ensure_schedules({"test": 60})
            store.enqueue_due_schedules()
            job = store.claim()
            store.fail(job["job_id"], "old error", 1)
            with store.connect() as connection:
                connection.execute("UPDATE worker_jobs SET available_at=0 WHERE job_id=?", (job["job_id"],))
            retried = store.claim()
            store.complete(retried["job_id"])
            with store.connect() as connection:
                row = connection.execute("SELECT status, last_error FROM worker_jobs WHERE job_id=?", (job["job_id"],)).fetchone()

        self.assertEqual(tuple(row), ("completed", None))

    def test_worker_store_records_heartbeat_and_notification_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkerStore(f"{directory}/worker.db")
            store.heartbeat("healthy", {"job_type": "test"})
            self.assertTrue(store.notification_allowed("test", 1800))
            store.mark_notification("test")
            with store.connect() as connection:
                heartbeat = connection.execute("SELECT status, details_json FROM worker_heartbeat WHERE singleton_id=1").fetchone()
            notification_blocked = not store.notification_allowed("test", 1800)

        self.assertEqual(heartbeat["status"], "healthy")
        self.assertEqual(json.loads(heartbeat["details_json"]), {"job_type": "test"})
        self.assertTrue(notification_blocked)

    def test_discord_notification_uses_embed_and_marks_cooldown(self):
        marked = []
        worker = Worker.__new__(Worker)
        worker.store = SimpleNamespace(notification_allowed=lambda key, cooldown: True, mark_notification=lambda key: marked.append(key))
        worker.control_store = SimpleNamespace(get_policy=lambda key: None)
        worker._policy_int = lambda key, default: default

        with patch.dict("os.environ", {"CINESWARM_DISCORD_BOT_TOKEN": "", "CINESWARM_DISCORD_WEBHOOK": "https://discord.com/api/webhooks/test"}, clear=False), patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = SimpleNamespace()
            sent = worker._send_notification("job_failed", {"job_type": "test"})

        body = json.loads(urlopen.call_args.args[0].data)
        self.assertTrue(sent)
        self.assertEqual(body["username"], "CineSwarm")
        self.assertEqual(body["embeds"][0]["title"], "CineSwarm: Job Failed")
        self.assertEqual(marked, ["job_failed"])

    def test_notification_prefers_paired_bot_channel(self):
        worker = Worker.__new__(Worker)
        worker.store = SimpleNamespace(notification_allowed=lambda key, cooldown: True, mark_notification=lambda key: None)
        worker.control_store = SimpleNamespace(get_policy=lambda key: "123" if key == "CINESWARM_DISCORD_CHANNEL_IDS" else None)
        worker._policy_int = lambda key, default: default

        with patch.dict("os.environ", {"CINESWARM_DISCORD_BOT_TOKEN": "token", "CINESWARM_DISCORD_WEBHOOK": "https://discord.com/api/webhooks/old"}, clear=False), patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = SimpleNamespace()
            sent = worker._send_notification("test_alert", {})

        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertTrue(sent)
        self.assertEqual(request.full_url, "https://discord.com/api/v10/channels/123/messages")
        self.assertEqual(request.headers["Authorization"], "Bot token")
        self.assertNotIn("username", body)

    def test_autonomous_job_stops_before_budget_or_discovery(self):
        worker = Worker.__new__(Worker)
        worker._check_storage = lambda: {"alerts": []}
        worker._record_autopilot_decision = lambda decision, details: {"status": decision, **details}
        worker.control_store = SimpleNamespace(is_emergency_stop=lambda: True, get_policy=lambda key: None)
        worker.plane = SimpleNamespace()

        with patch.dict("os.environ", {"CINESWARM_AUTO_EMERGENCY_STOP": "false"}):
            result = worker.execute({"job_type": "autonomous_acquisition"})

        self.assertEqual(result, {"status": "blocked_emergency_stop"})

    def test_autonomous_job_enforces_weekly_budget(self):
        worker = Worker.__new__(Worker)
        worker._check_storage = lambda: {"alerts": []}
        worker._record_autopilot_decision = lambda decision, details: {"status": decision, **details}
        worker.control_store = SimpleNamespace(
            is_emergency_stop=lambda: False,
            get_policy=lambda key: "true" if key == "CINESWARM_AUTO_ADD_SEARCH" else None,
            check_budget=lambda: (False, {"reason": "weekly_movie_budget_exceeded"}),
        )
        worker.plane = SimpleNamespace(planner=SimpleNamespace(queue=lambda media_type: {"total_records": 0}))

        with patch.dict("os.environ", {"CINESWARM_AUTO_EMERGENCY_STOP": "false"}):
            result = worker.execute({"job_type": "autonomous_acquisition"})

        self.assertEqual(result["status"], "blocked_weekly_budget")
        self.assertEqual(result["details"]["reason"], "weekly_movie_budget_exceeded")

    def test_autonomous_job_pauses_at_download_limit(self):
        worker = Worker.__new__(Worker)
        worker._check_storage = lambda: {"alerts": []}
        policies = {"CINESWARM_AUTO_ADD_SEARCH": "true", "CINESWARM_AUTO_MAX_CONCURRENT_DOWNLOADS": "2"}
        worker._record_autopilot_decision = lambda decision, details: {"status": decision, **details}
        worker.control_store = SimpleNamespace(is_emergency_stop=lambda: False, get_policy=lambda key: policies.get(key))
        worker.plane = SimpleNamespace(planner=SimpleNamespace(queue=lambda media_type: {"total_records": 2}))

        result = worker.execute({"job_type": "autonomous_acquisition"})

        self.assertEqual(result["status"], "blocked_queue_limit")
        self.assertEqual(result["active"], 4)
        self.assertEqual(result["pressure"]["sources"]["sonarr"], 2)

    def test_autonomous_job_adds_and_searches_eligible_movie(self):
        worker = Worker.__new__(Worker)
        worker._check_storage = lambda: {"alerts": []}
        worker._record_autopilot_decision = lambda decision, details: {"status": decision, **details}
        policies = {
            "CINESWARM_AUTO_ADD_SEARCH": "true",
            "CINESWARM_AUTO_MAX_CONCURRENT_DOWNLOADS": "2",
            "CINESWARM_AUTO_MIN_SCORE": "90",
            "CINESWARM_AUTO_ALLOWED_GENRES": "Action,Comedy",
            "CINESWARM_AUTO_REQUIRED_QUALITY_PROFILE": "HD-1080p",
            "CINESWARM_AUTO_RECENT_YEARS": "2",
            "CINESWARM_AUTO_RECENT_WEEKLY_TARGET": "3",
        }
        calls = []
        store = SimpleNamespace(
            is_emergency_stop=lambda: False,
            get_policy=lambda key: policies.get(key),
            check_budget=lambda: (True, {"budget": {"movies_added": 0, "series_added": 0, "gb_added": 0}, "limits": {"gb": 200}}),
            record_autonomous_action=lambda *args: "action",
            create_task=lambda *args: calls.append(args) or "add-task",
            increment_budget=lambda *args: calls.append(("budget", *args)),
            complete_autonomous_action=lambda action_id: calls.append(("complete", action_id)),
            fail_autonomous_action=lambda action_id: calls.append(("fail", action_id)),
            get_week_start=lambda: "2026-08-17",
        )
        discovery = SimpleNamespace(
            candidate=lambda candidate_id: {"candidate": {"title": "New Movie", "year": 2026, "tmdbId": 123, "genres": ["Action"]}},
            set_status=lambda *args: calls.append(("status", *args)),
        )
        planner = SimpleNamespace(
            queue=lambda media_type: {"total_records": 0},
            plan=lambda media_type, title: {"root_folders": [{"path": "/movies"}], "quality_profiles": [{"id": 1, "name": "HD-1080p"}]},
            radarr=SimpleNamespace(get=lambda path: []),
        )
        plane = SimpleNamespace(
            planner=planner,
            discovery=discovery,
            discovery_queue=lambda: [{"id": 7, "media_type": "movie", "title": "New Movie", "year": 2026, "score": 95, "status": "new"}],
            approve_task=lambda task_id, actor: {"result": {"id": 42 if task_id == "add-task" else 99}, "follow_up": {"task_id": "search-task"}} if task_id == "add-task" else {"result": {"id": 99}},
        )
        worker.control_store = store
        worker.plane = plane
        worker._send_notification = lambda *args, **kwargs: True

        with patch("cineswarm_worker.sync_catalog"):
            result = worker.execute({"job_type": "autonomous_acquisition"})

        self.assertEqual(result["status"], "executed_add_search")
        self.assertEqual(result["service_id"], 42)
        self.assertEqual(result["command_id"], 99)

    def test_reconcile_job_is_reachable(self):
        worker = Worker.__new__(Worker)
        worker.plane = SimpleNamespace(
            reconcile=lambda actor: {
                "movies": {"file_not_indexed": 0},
                "series": {"not_indexed": 0},
            }
        )
        worker._auto_refresh_allowed = lambda: False
        worker._refresh_cooldown_elapsed = lambda: True

        result = worker.execute({"job_type": "reconcile_library"})

        self.assertEqual(result["automatic_action"]["status"], "not_run")

    def test_worker_audit_modes_reflect_side_effect_boundaries(self):
        self.assertEqual(Worker._audit_mode("failed_download_recovery"), "approval-gated")
        self.assertEqual(Worker._audit_mode("queue_monitor"), "approval-gated")
        self.assertEqual(Worker._audit_mode("service_refresh"), "local-write")
        self.assertEqual(Worker._audit_mode("autonomous_acquisition"), "automatic")
        self.assertEqual(Worker._audit_mode("reconcile_library", {"automatic_action": {"status": "completed"}}), "automatic")

    def test_failed_download_recovery_does_not_alert_for_zero_failures(self):
        worker = Worker.__new__(Worker)
        worker.plane = SimpleNamespace(planner=SimpleNamespace(get_failed_downloads=lambda media_type: [], queue=lambda media_type: {"total_records": 0, "records": []}))
        worker.control_store = FakeControlStore()
        worker.control_store.get_policy = lambda key: "true" if key == "CINESWARM_NOTIFY_ON_FAILED_DOWNLOAD" else None
        notifications = []
        worker._send_notification = lambda *args: notifications.append(args)

        result = worker.execute({"job_type": "failed_download_recovery"})

        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(notifications, [])

    def test_failed_download_recovery_creates_approval_tasks(self):
        worker = Worker.__new__(Worker)
        planner = SimpleNamespace(
            queue=lambda media_type: {"total_records": 0, "records": []},
            get_failed_downloads=lambda media_type: [{
                "media_type": media_type,
                "service_id": 42 if media_type == "movie" else 7,
                "queue_id": 10 if media_type == "movie" else 11,
                "title": "Failed item",
                "error_message": "import failed",
            }]
        )
        worker.plane = SimpleNamespace(planner=planner)
        worker.control_store = FakeControlStore()
        worker._send_notification = lambda *args, **kwargs: None

        first = worker.execute({"job_type": "failed_download_recovery"})
        second = worker.execute({"job_type": "failed_download_recovery"})

        self.assertEqual(first["approval_tasks_created"], 2)
        self.assertEqual(second["approval_tasks_created"], 0)
        self.assertEqual(
            {task[0] for task in worker.control_store.tasks},
            {"radarr_search_retry_request", "sonarr_search_retry_request"},
        )

    def test_failed_download_recovery_caps_retry_storm_deterministically(self):
        worker = Worker.__new__(Worker)
        policies = {
            "CINESWARM_FAILED_DOWNLOAD_MAX_PER_CYCLE": "3",
            "CINESWARM_FAILED_DOWNLOAD_MAX_PER_MEDIA_PER_CYCLE": "1",
            "CINESWARM_FAILED_DOWNLOAD_COOLDOWN": "60",
        }
        store = FakeControlStore()
        store.get_policy = lambda key: policies.get(key)
        failed = [{"media_type": "movie", "service_id": service_id, "queue_id": 100 + service_id, "title": f"Movie {service_id}"} for service_id in (5, 1, 4, 2, 3)]
        planner = SimpleNamespace(global_queue_pressure=lambda limit: {"status": "available", "pressured": False, "active": 0, "limit": limit}, get_failed_downloads=lambda media_type: failed if media_type == "movie" else [])
        worker.control_store = store
        worker.plane = SimpleNamespace(planner=planner)
        worker._send_notification = lambda *args, **kwargs: None

        result = worker.execute({"job_type": "failed_download_recovery"})

        self.assertEqual(result["approval_tasks_created"], 3)
        self.assertEqual([task[2]["service_id"] for task in store.tasks], [1, 2, 3])
        self.assertEqual(result["limits"], {"per_cycle": 3, "per_media": 1, "cooldown": 60})

    def test_emergency_stop_blocks_failed_download_recovery(self):
        worker = Worker.__new__(Worker)
        calls = []
        worker.control_store = SimpleNamespace(get_policy=lambda key: "true" if key == "CINESWARM_AUTO_EMERGENCY_STOP" else None, is_emergency_stop=lambda: True)
        worker.plane = SimpleNamespace(planner=SimpleNamespace(get_failed_downloads=lambda media_type: calls.append(media_type)))

        result = worker.execute({"job_type": "failed_download_recovery"})

        self.assertEqual(result["status"], "blocked_emergency_stop")
        self.assertEqual(result["approval_tasks_created"], 0)
        self.assertEqual(calls, [])

    def test_startup_readiness_waits_then_resumes_once(self):
        worker = Worker.__new__(Worker)
        worker._startup_ready = False
        worker._resume_recorded = False
        refreshes = [
            {"plex": {"status": "healthy"}, "radarr": {"status": "healthy"}, "sonarr": {"status": "error"}},
            {"plex": {"status": "healthy"}, "radarr": {"status": "healthy"}, "sonarr": {"status": "healthy"}},
        ]
        decisions = []
        notifications = []
        worker.plane = SimpleNamespace(refresh=lambda actor: refreshes.pop(0))
        worker._check_storage = lambda: {"status": "ready", "alerts": [], "root_folders": {"radarr": 1, "sonarr": 1}}
        worker.control_store = SimpleNamespace(record_decision=lambda *args: decisions.append(args) or "decision-ready")
        worker._send_notification = lambda *args, **kwargs: notifications.append((args, kwargs))

        waiting = worker._startup_readiness()
        ready = worker._startup_readiness()
        still_ready = worker._startup_readiness()

        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(ready["status"], "ready")
        self.assertTrue(still_ready["ready"])
        self.assertEqual(len(decisions), 1)
        self.assertEqual(len(notifications), 1)


class PreservationTests(unittest.TestCase):
    def test_collects_only_relevant_kernel_storage_events(self):
        output = "2026-09-01T01:00:00 kernel: ordinary message\n2026-09-01T01:01:00 kernel: Buffer I/O error on dev sda1\n"
        with patch("cineswarm_preservation.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=output)):
            events = collect_storage_events()

        self.assertEqual(len(events), 1)
        self.assertIn("Buffer I/O error", events[0]["message"])

    def test_maps_container_media_path_to_host_union(self):
        self.assertEqual(mapped_path("/media/Movies/Example", "/media=/mnt/media,/data=/mnt/media"), "/mnt/media/Movies/Example")

    def test_resolves_union_path_to_physical_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            union = os.path.join(directory, "media")
            branch = os.path.join(directory, "disk1")
            os.makedirs(os.path.join(union, "movies"))
            os.makedirs(os.path.join(branch, "movies"))
            logical = os.path.join(union, "movies", "film.mkv")
            physical = os.path.join(branch, "movies", "film.mkv")
            with open(logical, "wb") as output:
                output.write(b"union")
            with open(physical, "wb") as output:
                output.write(b"physical")

            self.assertEqual(resolve_physical_path(logical, [union], [branch]), physical)

    def test_detects_checksum_mismatch_and_metadata_change(self):
        with tempfile.TemporaryDirectory() as directory:
            media = os.path.join(directory, "film.mkv")
            control = os.path.join(directory, "control.db")
            catalog = os.path.join(directory, "missing-catalog.db")
            with open(media, "wb") as output:
                output.write(b"first")
            preservation_scan(control, catalog, roots=[media], union_roots=[directory], sample_limit=1)
            stat = os.stat(media)
            with open(media, "wb") as output:
                output.write(b"other")
            os.utime(media, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            preservation_scan(control, catalog, roots=[media], union_roots=[directory], sample_limit=1)
            with open(media, "ab") as output:
                output.write(b" changed")
            preservation_scan(control, catalog, roots=[media], union_roots=[directory], sample_limit=1)
            with sqlite3.connect(control) as connection:
                statuses = [row[0] for row in connection.execute("SELECT status FROM preservation_files ORDER BY id")]

        self.assertEqual(statuses, ["new", "checksum_mismatch", "changed"])

    def test_reports_missing_mount(self):
        path = os.path.join(tempfile.gettempdir(), f"cineswarm-missing-{os.getpid()}")
        health = collect_mount_health([path])

        self.assertFalse(health[0]["mounted"])
        self.assertEqual(health[0]["error"], "not mounted")

    def test_online_backup_is_consistent_and_integral(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source.db")
            backup = os.path.join(directory, "backup.db")
            with sqlite3.connect(source) as connection:
                connection.execute("CREATE TABLE samples(value TEXT)")
                connection.execute("INSERT INTO samples VALUES ('preserved')")
            result = online_backup(source, backup)
            with sqlite3.connect(backup) as connection:
                value = connection.execute("SELECT value FROM samples").fetchone()[0]
                check = connection.execute("PRAGMA quick_check").fetchone()[0]

        self.assertEqual(result["quick_check"], ["ok"])
        self.assertEqual((value, check), ("preserved", "ok"))

    def test_retention_only_removes_generated_backup_names(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = []
            for index in range(3):
                path = os.path.join(directory, f"cineswarm-control-20260101T00000{index}Z-1234567{index}.sqlite3")
                with open(path, "wb") as output:
                    output.write(b"backup")
                os.utime(path, (index + 1, index + 1))
                generated.append(path)
            unrelated = os.path.join(directory, "important.sqlite3")
            malformed = os.path.join(directory, "cineswarm-control-old.sqlite3")
            for path in (unrelated, malformed):
                with open(path, "wb") as output:
                    output.write(b"keep")

            removed = rotate_backups(directory, 1)

            self.assertEqual(set(removed), set(generated[:2]))
            self.assertTrue(os.path.exists(generated[2]))
            self.assertTrue(os.path.exists(unrelated))
            self.assertTrue(os.path.exists(malformed))

    def test_restore_refuses_existing_target_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = os.path.join(directory, "backup.db")
            target = os.path.join(directory, "live.db")
            for path, value in ((backup, "backup"), (target, "live")):
                with sqlite3.connect(path) as connection:
                    connection.execute("CREATE TABLE marker(value TEXT)")
                    connection.execute("INSERT INTO marker VALUES (?)", (value,))

            with self.assertRaises(FileExistsError):
                restore_database(backup, target)
            with sqlite3.connect(target) as connection:
                value = connection.execute("SELECT value FROM marker").fetchone()[0]

        self.assertEqual(value, "live")


class ConfigurationTests(unittest.TestCase):
    def test_exact_release_grab_requires_approval(self):
        self.assertEqual(Policy.classify("radarr_release_grab_request"), ("approval_required", "requires_explicit_approval"))

    def test_worker_environment_keys_are_allowlisted(self):
        expected = {
            "CINESWARM_API_TIMEOUT",
            "CINESWARM_WORKER_MAX_ATTEMPTS",
            "CINESWARM_WORKER_LEASE_SECONDS",
            "CINESWARM_WORKER_POLL_SECONDS",
            "CINESWARM_DISCOVERY_ENABLED",
            "CINESWARM_NOTIFICATION_WEBHOOK",
            "CINESWARM_DISCORD_WEBHOOK",
            "CINESWARM_NOTIFICATION_COOLDOWN",
            "CINESWARM_HEARTBEAT_STALE_SECONDS",
            "CINESWARM_AUTONOMOUS_INTERVAL",
            "CINESWARM_FAILED_DOWNLOAD_INTERVAL",
            "PLEX_URL",
            "PLEX_TOKEN",
            "CINESWARM_AUTO_PLEX_REFRESH",
            "CINESWARM_PLEX_REFRESH_COOLDOWN",
            "CINESWARM_AUTO_ADD_SEARCH",
            "CINESWARM_AUTO_RECENT_YEARS",
            "CINESWARM_AUTO_RECENT_WEEKLY_TARGET",
            "CINESWARM_AUTO_ESTIMATED_MOVIE_GB",
        }
        self.assertTrue(expected <= LOCAL_ENV_KEYS)


class MediaHealthTests(unittest.TestCase):
    def test_resolve_path_and_locate_video_file(self):
        from cineswarm_health import MediaHealthScanner
        with tempfile.TemporaryDirectory() as directory:
            test_file = os.path.join(directory, "Sample Movie (2020).mkv")
            with open(test_file, "wb") as f:
                f.write(b"mock_video_bytes")

            with patch.dict("os.environ", {"CINESWARM_PATH_MAP": f"/media={directory}"}):
                resolved = MediaHealthScanner.resolve_path("/media/Sample Movie (2020).mkv")
                self.assertEqual(resolved, test_file)
                located = MediaHealthScanner.locate_video_file(directory)
                self.assertEqual(located, test_file)


class AIEnrichmentTests(unittest.TestCase):
    def test_chapter_summarizer_export_vtt(self):
        from cineswarm_chapters import ChapterSummarizer
        chapters = [
            {"title": "Prologue", "start_seconds": 0, "summary": "Beginning of film."},
            {"title": "Climax", "start_seconds": 3600, "summary": "Final battle."}
        ]
        vtt = ChapterSummarizer.export_vtt(chapters)
        self.assertIn("WEBVTT", vtt)
        self.assertIn("00:00:00.000 --> 01:00:00.000", vtt)
        self.assertIn("Prologue: Beginning of film.", vtt)

    def test_trivia_commentary_export_srt(self):
        from cineswarm_agents import TriviaCommentaryAgent
        markers = [
            {
                "timestamp": "00:01:30",
                "start_seconds": 90,
                "end_seconds": 105,
                "speaker": "Director",
                "category": "Trivia",
                "commentary": "This scene was shot in one continuous take."
            }
        ]
        srt = TriviaCommentaryAgent.export_srt(markers)
        self.assertIn("00:01:30,000 --> 00:01:45,000", srt)
        self.assertIn("[TRIVIA] Director: This scene was shot in one continuous take.", srt)


if __name__ == "__main__":
    unittest.main()
