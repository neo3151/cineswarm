"""Focused product-monitoring slice tests."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cineswarm_control import API_VERSION, ControlPlane, ControlStore, Handler
from cineswarm_discord import DiscordService
from cineswarm_worker import Worker


class MonitoringSnapshotHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temporary.name, "control.db")
        self.catalog = os.path.join(self.temporary.name, "catalog.db")
        with sqlite3.connect(self.catalog) as connection:
            connection.execute("CREATE TABLE catalog_items(media_type TEXT, present INTEGER)")
        self.store = ControlStore(self.database)
        self.plane = ControlPlane(self.store, {})
        self.plane.planner = SimpleNamespace(
            queue=lambda media_type, timeout=None: {"total_records": 2 if media_type == "movie" else 1, "records": []},
            global_queue_pressure=lambda limit, timeout=None: {
                "status": "available",
                "pressured": False,
                "active": 3,
                "limit": limit,
                "sources": {"radarr": 2, "sonarr": 1, "sabnzbd": 0},
                "records": [],
                "sabnzbd": {"configured": False, "error": None},
            },
        )
        Handler.plane = self.plane
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.catalog_patch = patch("cineswarm_control.CATALOG_DB", self.catalog)
        self.catalog_patch.start()
        self.store.save_snapshot("plex", {"counts": {"all": 1}})
        self.store.save_snapshot("radarr", {"counts": {"all": 1}})
        self.store.save_snapshot("sonarr", {"counts": {"all": 1}})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.catalog_patch.stop()
        self.temporary.cleanup()

    def request(self, path, credentials=None):
        headers = {}
        if credentials:
            encoded = base64.b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        request = urllib.request.Request(self.base_url + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read()), response.headers
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), error.headers

    def test_monitoring_snapshot_shape(self):
        status, payload, _ = self.request("/api/monitoring/snapshot")
        self.assertEqual(status, 200)
        for key in (
            "overall_status",
            "services",
            "worker",
            "downloads",
            "pending_approvals",
            "recent_failures",
            "emergency_stop",
            "generated_at",
        ):
            self.assertIn(key, payload)
        self.assertIn(payload["overall_status"], {"healthy", "degraded", "unhealthy"})
        self.assertEqual(payload["downloads"]["movies"], 2)
        self.assertEqual(payload["downloads"]["series"], 1)
        self.assertEqual(payload["downloads"]["total"], 3)
        self.assertIsInstance(payload["recent_failures"], list)
        self.assertLessEqual(len(payload["recent_failures"]), 10)
        self.assertNotIn("token", json.dumps(payload).lower())
        self.assertNotIn("api_key", json.dumps(payload).lower())

    def test_monitoring_snapshot_v1_alias_and_unauthenticated(self):
        configured = {"CINESWARM_DASHBOARD_USERNAME": "operator", "CINESWARM_DASHBOARD_PASSWORD": "secret"}
        with patch.dict(os.environ, configured):
            plain_status, plain, _ = self.request("/api/monitoring/snapshot")
            alias_status, alias, headers = self.request("/api/v1/monitoring/snapshot")
            denied, _, _ = self.request("/api/status")
        self.assertEqual(plain_status, 200)
        self.assertEqual((alias_status, alias), (plain_status, plain))
        self.assertEqual(headers["X-CineSwarm-API-Version"], API_VERSION)
        self.assertEqual(denied, 401)

    def test_incoming_issues_route_is_public(self):
        self.plane.incoming_issues_report = lambda persist=False: {
            "severity": "watch",
            "issues": [{"code": "av1_unaccelerated", "severity": "watch", "summary": "177 AV1 files"}],
            "headline": "177 AV1 files",
            "metrics": {},
            "generated_at": "2026-09-11T00:00:00+00:00",
        }
        configured = {"CINESWARM_DASHBOARD_USERNAME": "operator", "CINESWARM_DASHBOARD_PASSWORD": "secret"}
        with patch.dict(os.environ, configured):
            status, payload, _ = self.request("/api/monitoring/incoming")
            alias_status, alias, _ = self.request("/api/v1/monitoring/incoming")
        self.assertEqual(status, 200)
        self.assertEqual(alias_status, 200)
        self.assertEqual(payload["severity"], "watch")
        self.assertEqual(payload["issues"][0]["code"], "av1_unaccelerated")
        self.assertEqual(payload, alias)


class IncomingIssuesClassifierTests(unittest.TestCase):
    def test_healthy_snapshot_still_flags_stuck_plex_and_idle_acquire(self):
        from cineswarm_ops_pulse import classify_issues, overall_severity
        issues = classify_issues({
            "worker_healthy": True,
            "unhealthy_services": [],
            "emergency_stop": False,
            "without_file": 487,
            "delta_without_file": 4,
            "file_not_indexed": 185,
            "delta_file_not_indexed": 0,
            "never_imported": 522,
            "delta_never_imported": 12,
            "acquire_skip_streak": 4,
            "downloads_active": 0,
            "last_acquire_decision": "skipped_no_eligible_candidate",
            "skip_counts": {"low_score": 12, "short_runtime": 9},
            "plex_refresh_pending": 0,
            "watch_titles": 3,
            "av1_count": 177,
        })
        codes = {issue["code"] for issue in issues}
        self.assertIn("library_debt_growing", codes)
        self.assertIn("plex_index_stuck", codes)
        self.assertIn("never_imported_growing", codes)
        self.assertIn("acquire_idle", codes)
        self.assertIn("watch_ledger_thin", codes)
        self.assertIn("av1_unaccelerated", codes)
        self.assertEqual(overall_severity(issues), "attention")

    def test_ok_when_services_and_queues_are_quiet(self):
        from cineswarm_ops_pulse import classify_issues, overall_severity
        issues = classify_issues({
            "worker_healthy": True,
            "unhealthy_services": [],
            "emergency_stop": False,
            "without_file": 10,
            "file_not_indexed": 2,
            "never_imported": 3,
            "acquire_skip_streak": 0,
            "downloads_active": 1,
            "last_acquire_decision": "executed_add_search",
            "watch_titles": 200,
            "av1_count": 0,
        })
        self.assertEqual(issues, [])
        self.assertEqual(overall_severity(issues), "ok")


class DiscordMonitorCommandTests(unittest.TestCase):
    def test_monitor_command_formats_snapshot(self):
        snapshot = {
            "overall_status": "degraded",
            "services": [{"service": "plex", "status": "healthy"}, {"service": "sonarr", "status": "error"}],
            "unhealthy_services": ["sonarr"],
            "worker": {"healthy": True, "status": "idle", "age_seconds": 4.2},
            "downloads": {"movies": 3, "series": 1, "total": 4},
            "pending_approvals": 2,
            "recent_failures": [{"type": "task_attention", "action": "radarr_search_request", "status": "failed"}],
            "emergency_stop": False,
            "generated_at": "2026-09-07T17:00:00+00:00",
        }
        plane = SimpleNamespace(monitoring_snapshot=lambda: snapshot, store=SimpleNamespace())
        service = DiscordService(plane)
        text = service.handle("monitor", 42)
        self.assertIn("Live Monitor", text)
        self.assertIn("DEGRADED", text)
        self.assertIn("3 movies", text)
        self.assertIn("Pending approve: 2", text)
        self.assertIn("sonarr", text.lower())
        self.assertIn("task_attention", text)

    def test_incoming_command_lists_ranked_issues(self):
        report = {
            "severity": "attention",
            "headline": "Acquire skipped 4 cycles in a row with an empty download queue.",
            "issues": [{"code": "acquire_idle", "severity": "attention", "summary": "Acquire skipped 4 cycles in a row with an empty download queue."}],
        }
        plane = SimpleNamespace(incoming_issues_report=lambda persist=False: report, store=SimpleNamespace())
        service = DiscordService(plane)
        text = service.handle("incoming", 42)
        self.assertIn("ATTENTION", text)
        self.assertIn("acquire_idle", text)


class MonitorWebhookTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.control_db = os.path.join(self.temporary.name, "control.db")
        self.worker_db = os.path.join(self.temporary.name, "worker.db")
        self.control_store = ControlStore(self.control_db)
        self.control_store.save_snapshot("plex", {"counts": {"all": 1}})
        self.control_store.save_snapshot("radarr", {"counts": {"all": 1}})
        self.control_store.save_snapshot("sonarr", {"counts": {"all": 1}})
        # Fake a healthy worker heartbeat so overall can be healthy
        with self.control_store.lock, self.control_store._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_heartbeat (
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                    worker_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO worker_heartbeat(singleton_id, worker_id, status, details_json, updated_at) VALUES (1, 'w1', 'idle', '{}', ?)",
                (__import__("time").time(),),
            )
        self.worker = Worker.__new__(Worker)
        self.worker.control_store = self.control_store
        self.worker.store = SimpleNamespace()
        self.worker.plane = SimpleNamespace(refresh=lambda actor: {})
        self.worker._last_monitor_overall_status = None

    def tearDown(self):
        self.temporary.cleanup()

    def test_monitor_webhook_unset_is_noop(self):
        with patch.dict(os.environ, {"CINESWARM_MONITOR_WEBHOOK": ""}, clear=False):
            self.assertFalse(self.worker._post_monitor_webhook_on_transition({"plex": {"status": "healthy"}}))

    def test_monitor_webhook_posts_only_on_transition(self):
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=10):
            calls.append(json.loads(request.data.decode()))
            return FakeResponse()

        env = {"CINESWARM_MONITOR_WEBHOOK": "https://example.test/hooks/monitor"}
        with patch.dict(os.environ, env, clear=False), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            # Baseline healthy — no post
            healthy = {"plex": {"status": "healthy"}, "radarr": {"status": "healthy"}, "sonarr": {"status": "healthy"}}
            self.assertFalse(self.worker._post_monitor_webhook_on_transition(healthy))
            self.assertEqual(calls, [])
            # Transition to degraded — post once
            degraded = {"plex": {"status": "healthy"}, "radarr": {"status": "healthy"}, "sonarr": {"status": "error", "error": "down"}}
            self.assertTrue(self.worker._post_monitor_webhook_on_transition(degraded))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["status"], "degraded")
            self.assertEqual(calls[0]["previous_status"], "healthy")
            self.assertIn("sonarr", calls[0]["unhealthy_services"])
            # Same status again — no spam
            self.assertFalse(self.worker._post_monitor_webhook_on_transition(degraded))
            self.assertEqual(len(calls), 1)
            # Recover to healthy — post recovery
            self.assertTrue(self.worker._post_monitor_webhook_on_transition(healthy))
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1]["status"], "healthy")
            self.assertEqual(calls[1]["previous_status"], "degraded")


if __name__ == "__main__":
    unittest.main()
