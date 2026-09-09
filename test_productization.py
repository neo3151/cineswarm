import base64
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from cineswarm_config import main as config_main, validate_config
from cineswarm_control import API_VERSION, ControlStore, DASHBOARD_HTML, Handler, READ_ONLY_V1_ALIASES
from cineswarm_dashboard import DASHBOARD_HTML as MODULAR_DASHBOARD_HTML


class ProductizationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temporary.name, "control.db")
        self.catalog = os.path.join(self.temporary.name, "catalog.db")
        with sqlite3.connect(self.catalog) as connection:
            connection.execute("CREATE TABLE catalog_items(media_type TEXT, present INTEGER)")
        self.store = ControlStore(self.database)
        Handler.plane = SimpleNamespace(
            store=self.store,
            discovery=None,
            discovery_queue=lambda: [{"id": 1, "title": "Example"}],
            operational_queue=lambda limit=200: {**self.store.queue_detail(limit), "downloads": {"movies": {"records": [], "total_records": 0}, "series": {"records": [], "total_records": 0}}, "errors": {}},
            evolution_engine=SimpleNamespace(process_plex_playback_event=lambda payload: {"action": "monitored", "title": (payload.get("Metadata") or {}).get("title")}),
            user_profiles=SimpleNamespace(apply_plex_account=lambda name: {"active_profile": "kids"} if name else None),
            log_decision=lambda *args, **kwargs: "dec-test",
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.catalog_patch = patch("cineswarm_control.CATALOG_DB", self.catalog)
        self.catalog_patch.start()
        self.auth_patch = patch.dict(os.environ, {"CINESWARM_DASHBOARD_USERNAME": "", "CINESWARM_DASHBOARD_PASSWORD": ""})
        self.auth_patch.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.catalog_patch.stop()
        self.auth_patch.stop()
        self.temporary.cleanup()

    def request(self, path, credentials=None, method="GET", data=None, content_type=None):
        headers = {}
        if credentials:
            encoded = base64.b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        if content_type:
            headers["Content-Type"] = content_type
        body = data if data is None or isinstance(data, bytes) else data.encode()
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read()) if path != "/" else response.read().decode(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), error.headers

    def test_authentication_disabled_preserves_access(self):
        with patch.dict(os.environ, {"CINESWARM_DASHBOARD_USERNAME": "", "CINESWARM_DASHBOARD_PASSWORD": ""}):
            status, _, _ = self.request("/api/status")
        self.assertEqual(status, 200)

    def test_authentication_protects_dashboard_and_api(self):
        configured = {"CINESWARM_DASHBOARD_USERNAME": "operator", "CINESWARM_DASHBOARD_PASSWORD": "correct horse"}
        with patch.dict(os.environ, configured):
            denied, payload, headers = self.request("/api/status")
            dashboard, _, _ = self.request("/", ("operator", "correct horse"))
            accepted, _, _ = self.request("/api/status", ("operator", "correct horse"))
            wrong, _, _ = self.request("/api/status", ("operator", "wrong"))
        self.assertEqual((denied, dashboard, accepted, wrong), (401, 200, 200, 401))
        self.assertEqual(payload["error"], "authentication_required")
        self.assertIn("Basic", headers["WWW-Authenticate"])

    def test_health_and_versioned_health_are_unauthenticated(self):
        configured = {"CINESWARM_DASHBOARD_USERNAME": "operator", "CINESWARM_DASHBOARD_PASSWORD": "password"}
        with patch.dict(os.environ, configured):
            unversioned, _, _ = self.request("/api/health")
            versioned, _, _ = self.request("/api/v1/health")
        self.assertEqual(unversioned, 503)
        self.assertEqual(versioned, unversioned)

    def test_plex_webhook_accepts_unauthenticated_multipart(self):
        configured = {"CINESWARM_DASHBOARD_USERNAME": "operator", "CINESWARM_DASHBOARD_PASSWORD": "password"}
        boundary = "XXXX"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="payload"\r\n\r\n'
            '{"event":"media.scrobble","Metadata":{"title":"Heat"},"Account":{"title":"Kids Tablet"}}\r\n'
            f"--{boundary}--\r\n"
        ).encode()
        with patch.dict(os.environ, configured):
            denied, _, _ = self.request("/api/refresh", method="POST", data=b"{}")
            status, payload, _ = self.request(
                "/api/webhooks/plex",
                method="POST",
                data=body,
                content_type=f"multipart/form-data; boundary={boundary}",
            )
        self.assertEqual(denied, 401)
        self.assertEqual(status, 200)
        self.assertEqual(payload["event"], "media.scrobble")
        self.assertEqual(payload["media"], "Heat")

    def test_read_only_v1_aliases_reuse_existing_responses(self):
        for route in ("status", "operational-summary?hours=1", "editions?limit=2", "discovery"):
            plain_status, plain, _ = self.request(f"/api/{route}")
            alias_status, alias, headers = self.request(f"/api/v1/{route}")
            self.assertEqual((alias_status, alias), (plain_status, plain))
            self.assertEqual(headers["X-CineSwarm-API-Version"], API_VERSION)
        metadata_status, metadata, _ = self.request("/api/v1")
        self.assertEqual(metadata_status, 200)
        self.assertEqual(metadata["api_version"], API_VERSION)

    def test_queue_and_preservation_routes_have_v1_aliases_and_bounded_responses(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO tasks(task_id, task_type, requested_by, status, requires_approval, payload_json, result_json, created_at, updated_at) VALUES ('task-1', 'radarr_search_retry_request', 'tester', 'pending_approval', 1, '{}', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
            connection.execute("CREATE TABLE preservation_scans(scan_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, catalog_db TEXT, policy_json TEXT, summary_json TEXT, error TEXT)")
            connection.execute("CREATE TABLE preservation_mount_health(id INTEGER PRIMARY KEY, scan_id TEXT, mount_path TEXT, mounted INTEGER, filesystem TEXT, source TEXT, total_bytes INTEGER, used_bytes INTEGER, free_bytes INTEGER, error TEXT)")
            connection.execute("CREATE TABLE preservation_files(id INTEGER PRIMARY KEY, scan_id TEXT, catalog_item_id INTEGER, logical_path TEXT, physical_path TEXT, size INTEGER, mtime_ns INTEGER, device INTEGER, checksum_sha256 TEXT, checksum_bytes INTEGER, status TEXT, error TEXT)")
            connection.execute("CREATE TABLE preservation_storage_events(id INTEGER PRIMARY KEY, scan_id TEXT, event_at TEXT, message TEXT)")
            connection.execute("INSERT INTO preservation_scans VALUES ('scan-1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00', 'completed', 'catalog.db', '{\"secret\":\"not-returned\"}', '{\"files\":1,\"statuses\":{\"checksum_mismatch\":1},\"storage_events\":1}', NULL)")
            connection.execute("INSERT INTO preservation_mount_health VALUES (1, 'scan-1', '/vault', 1, 'ext4', '/dev/test', 1000, 250, 750, NULL)")
            connection.execute("INSERT INTO preservation_files VALUES (1, 'scan-1', 7, '/vault/movie.mkv', '/private/movie.mkv', 500, 1, 2, 'abc', 500, 'checksum_mismatch', NULL)")
            connection.execute("INSERT INTO preservation_storage_events VALUES (1, 'scan-1', '2026-01-01T00:00:30+00:00', 'I/O warning')")

        for route in ("operations/queue?limit=1", "preservation?limit=1"):
            plain_status, plain, _ = self.request(f"/api/{route}")
            alias_status, alias, _ = self.request(f"/api/v1/{route}")
            self.assertEqual((plain_status, plain), (alias_status, alias))
        self.assertEqual(len(plain["files"]), 1)
        self.assertNotIn("policy", json.dumps(plain))
        queue_status, queue, _ = self.request("/api/operations/queue?limit=1")
        self.assertEqual(queue_status, 200)
        self.assertEqual(queue["tasks"][0]["status"], "pending_approval")
        self.assertTrue(queue["tasks"][0]["requires_approval"])

    def test_new_read_only_routes_reject_invalid_limits(self):
        for route in ("operations/queue", "preservation"):
            status, payload, _ = self.request(f"/api/{route}?limit=invalid")
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"], "limit must be an integer")

    def test_diagnostics_are_bounded_and_secret_redacted(self):
        secret = "never-return-this-secret"
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE worker_heartbeat(singleton_id INTEGER PRIMARY KEY, worker_id TEXT, status TEXT, details_json TEXT, updated_at REAL)")
            connection.execute("INSERT INTO worker_heartbeat VALUES (1, 'worker-test', 'running', ?, 0)", (json.dumps({"token": secret, "private_path": "/private/full/path"}),))
        configured = {
            "PLEX_TOKEN": secret,
            "RADARR_API_KEY": secret,
            "CINESWARM_DASHBOARD_PASSWORD": secret,
            "CINESWARM_DASHBOARD_USERNAME": "operator",
        }
        with patch.dict(os.environ, configured):
            status, payload, _ = self.request("/api/diagnostics", ("operator", secret))
        serialized = json.dumps(payload)
        self.assertEqual(status, 200)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("/private/full/path", serialized)
        self.assertNotIn("details", payload["worker"])
        self.assertIs(payload["configuration"]["plex"]["credential_configured"], True)
        self.assertEqual(payload["storage"]["control_database"]["integrity"], "ok")


class DashboardUITests(unittest.TestCase):
    def test_modular_dashboard_is_assigned_and_has_required_navigation(self):
        self.assertIs(DASHBOARD_HTML, MODULAR_DASHBOARD_HTML)
        for section in ("Overview", "Operations", "Discovery", "Curation", "Preservation", "Analytics", "Terminal"):
            self.assertIn(f'>{section}<', DASHBOARD_HTML)
            self.assertIn(f'id="view-{section.lower()}"', DASHBOARD_HTML)
        self.assertIn('aria-label="Primary navigation"', DASHBOARD_HTML)
        self.assertIn('@media(max-width:760px)', DASHBOARD_HTML)
        self.assertIn('@media(prefers-reduced-motion:reduce)', DASHBOARD_HTML)

    def test_dashboard_contains_critical_sections_and_transparent_discovery_scores(self):
        for text in ("System overview", "Active queue and downloads", "Pending and recent tasks", "Emergency stop", "Latest scan", "Editions and provenance", "Terminal"):
            self.assertIn(text, DASHBOARD_HTML)
        for field in ("watch_affinity_score", "collection_significance_score", "rarity_preservation_score", "storage_cost_score", "acquisition_confidence_score", "overall_score"):
            self.assertIn(field, DASHBOARD_HTML)
        self.assertNotIn("confidence: 95", DASHBOARD_HTML.lower())

    def test_dashboard_has_central_error_and_escaping_helpers_for_dynamic_html(self):
        self.assertIn("async function apiFetch", DASHBOARD_HTML)
        self.assertIn("function escapeHtml", DASHBOARD_HTML)
        for escaped in ("&amp;", "&lt;", "&gt;", "&#39;", "&quot;"):
            self.assertIn(escaped, DASHBOARD_HTML)
        self.assertIn("Network error while requesting", DASHBOARD_HTML)
        self.assertIn("Invalid response from", DASHBOARD_HTML)
        self.assertIn("function relativeTime", DASHBOARD_HTML)
        self.assertIn("function fmtBytes", DASHBOARD_HTML)

    def test_dashboard_references_required_read_only_routes(self):
        for route in ("/api/status", "/api/operational-summary", "/api/operations/queue", "/api/preservation", "/api/discovery", "/api/decisions"):
            self.assertIn(route, DASHBOARD_HTML)
        self.assertEqual(READ_ONLY_V1_ALIASES["/api/v1/operations/queue"], "/api/operations/queue")
        self.assertEqual(READ_ONLY_V1_ALIASES["/api/v1/preservation"], "/api/preservation")


class MigrationTests(unittest.TestCase):
    def test_schema_migrations_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "control.db")
            ControlStore(path)
            ControlStore(path)
            with sqlite3.connect(path) as connection:
                migrations = connection.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
                columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
                user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(migrations, [(1, "baseline_schema"), (2, "tasks_result_json")])
        self.assertIn("result_json", columns)
        self.assertEqual(user_version, 2)


class ConfigValidationTests(unittest.TestCase):
    def test_valid_configuration_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "PLEX_URL": "http://plex.local:32400",
                "PLEX_TOKEN": "secret",
                "RADARR_URL": "https://radarr.local:7878",
                "RADARR_API_KEY": "secret",
                "SONARR_URL": "http://sonarr.local:8989",
                "SONARR_API_KEY": "secret",
                "CINESWARM_CONTROL_PORT": "8787",
                "CINESWARM_PATH_MAP": f"/media={directory}",
                "CINESWARM_BACKUP_DIR": directory,
                "CINESWARM_CONTROL_DB": os.path.join(directory, "control.db"),
            }
            self.assertEqual(validate_config(config), [])

    def test_invalid_configuration_reports_names_without_values(self):
        secret = "must-not-be-reported"
        errors = validate_config({"PLEX_URL": "ftp://user:pass@example.test:99999", "PLEX_TOKEN": secret, "CINESWARM_CONTROL_PORT": "invalid", "CINESWARM_PATH_MAP": "relative=also-relative"})
        report = "\n".join(errors)
        self.assertIn("RADARR_URL", report)
        self.assertIn("PLEX_URL", report)
        self.assertIn("CINESWARM_CONTROL_PORT", report)
        self.assertIn("CINESWARM_PATH_MAP", report)
        self.assertNotIn(secret, report)

    def test_cli_exits_nonzero_without_exposing_values(self):
        secret = "cli-secret-value"
        output = StringIO()
        with patch.dict(os.environ, {"PLEX_TOKEN": secret}, clear=True), patch("sys.argv", ["cineswarm_config.py", "--env-file", "/nonexistent"]), redirect_stdout(output):
            result = config_main()
        self.assertEqual(result, 1)
        self.assertNotIn(secret, output.getvalue())


if __name__ == "__main__":
    unittest.main()
