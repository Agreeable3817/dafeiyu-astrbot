"""Mac deployment behavior tests: no QQ login, API calls or Docker daemon required."""
import importlib.util
import io
import json
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "deploy/macos"
spec = importlib.util.spec_from_file_location("macos_ops", HERE / "ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class FakeStack(ops.Stack):
    def __init__(self, root):
        super().__init__(root)
        self.live = ["astrbot"]
        self.calls = []
        self.fail_stop = False
        self.fail_restart = False

    def compose(self, *args, capture=False, use_lock=True):
        self.calls.append(args)
        if args[:1] == ("ps",):
            return "\n".join(self.live)
        if args[:1] == ("stop",):
            self.live = []
            if self.fail_stop:
                raise RuntimeError("simulated stop failure")
        if args[:1] == ("start",):
            if self.fail_restart:
                raise RuntimeError("simulated restart failure")
            self.live = [s for s in args[1:] if s in ops.SERVICES]
        return ""

    def image_records(self):
        return {service: "sha256:" + "a" * 64 for service in ops.SERVICES}

    def save_images(self, records):
        path = self.backups / "images" / ("a" * 64 + ".tar")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stand-in for Docker-save archive; data recovery test only")
        ops.write_checksum(path)

    def ensure_clean_stop(self):
        if self.live:
            raise RuntimeError("still running")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "stack"
        self.root.mkdir()
        for name in ops.KIT_FILES:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_bytes((HERE / name).read_bytes())
        (self.root / ".env").write_text("PRIVATE_TEST_VALUE=never-print-this\n")
        self.stack = FakeStack(self.root)
        self.stack.data.mkdir(parents=True)
        ops.write_json(self.stack.runtime / "installation.json", {"schema": 1})
        self.db = self.stack.data / "astrbot/state.db"
        self.db.parent.mkdir()
        with sqlite3.connect(self.db) as db:
            db.execute("create table memory (text)")
            db.execute("insert into memory values ('persistent memory')")
        p = self.stack.data / "astrbot/plugin_data/proactive/session_data.json"
        ops.write_json(p, {"unanswered": 1, "next_trigger": "later"})
        ops.write_json(self.stack.data / "napcat/config/webui.json", {"token": "private-test"})
        affect = self.stack.data / "astrbot/plugin_data/astrbot_plugin_affective_core/affective.db"
        affect.parent.mkdir(parents=True)
        with sqlite3.connect(affect) as db:
            db.execute("create table state (valence real)")
            db.execute("insert into state values (0.25)")

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_restore_persists_database_and_contact_state(self):
        archive = self.stack.backup()
        self.assertEqual(self.stack.live, ["astrbot"])
        self.assertIn(("stop", "astrbot", "napcat"), self.stack.calls)
        destination = self.root.parent / "restored"
        ops.restore(archive, destination)
        with sqlite3.connect(destination / ".runtime/data/astrbot/state.db") as db:
            self.assertEqual(db.execute("select text from memory").fetchone()[0], "persistent memory")
        self.assertEqual(json.loads((destination / ".runtime/data/astrbot/plugin_data/proactive/session_data.json").read_text())["unanswered"], 1)
        self.assertEqual((destination / ".env").read_bytes(), (self.root / ".env").read_bytes())
        self.assertEqual((destination / ".env").stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(destination / ".runtime/data/astrbot/plugin_data/astrbot_plugin_affective_core/affective.db") as db:
            self.assertEqual(db.execute("select valence from state").fetchone()[0], .25)

    def test_stopped_services_stay_stopped(self):
        self.stack.live = []
        self.stack.backup()
        self.assertFalse(any(c[0] == "start" for c in self.stack.calls))

    def test_stop_failure_restarts_previous_service_without_publishing_archive(self):
        self.stack.fail_stop = True
        with self.assertRaisesRegex(RuntimeError, "stop failure"):
            self.stack.backup()
        self.assertEqual(self.stack.live, ["astrbot"])
        self.assertEqual(list(self.stack.backups.glob("*.tar.gz")), [])

    def test_corrupt_database_fails_backup_and_recovers_service(self):
        self.db.write_bytes(b"SQLite format 3\x00" + b"invalid" * 100)
        with self.assertRaises(Exception):
            self.stack.backup()
        self.assertEqual(self.stack.live, ["astrbot"])
        self.assertFalse(list(self.stack.backups.glob("*.tar.gz")))

    def test_restart_failure_returns_failure(self):
        self.stack.fail_restart = True
        with self.assertRaisesRegex(RuntimeError, "restart failure"):
            self.stack.backup()

    def test_restore_refuses_existing_destination(self):
        archive = self.stack.backup()
        with self.assertRaises(FileExistsError):
            ops.restore(archive, self.root)
        self.assertTrue(self.db.exists())

    def test_archive_tampering_rejected(self):
        archive = self.stack.backup()
        archive.write_bytes(archive.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            ops.restore(archive, self.root.parent / "restore")
        self.assertFalse((self.root.parent / "restore").exists())

    def test_path_traversal_and_symlinks_rejected(self):
        for name, kind in [("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                archive = self.root.parent / "malicious.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.size = 1 if kind == tarfile.REGTYPE else 0
                    member.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                    tar.addfile(member, io.BytesIO(b"x") if member.size else None)
                ops.write_checksum(archive)
                with self.assertRaises(ValueError):
                    ops.restore(archive, self.root.parent / "escape-test")

    def test_lock_blocks_concurrent_mutation(self):
        with ops.operation_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "operation"):
                with ops.operation_lock(self.root):
                    self.fail("second mutation acquired the lock")

    def test_env_literal_no_shell_execution(self):
        p = self.root / "literal.env"
        p.write_text('KEY="$(touch /tmp/should-not-exist)"\nOTHER=abc#literal\n')
        self.assertEqual(ops.read_env(p)["KEY"], "$(touch /tmp/should-not-exist)")
        self.assertEqual(ops.read_env(p)["OTHER"], "abc#literal")

    def test_invalid_budget_and_night_hours_rejected(self):
        env = ops.read_env(HERE / ".env.example")
        env.update(DEEPSEEK_API_KEY="test-key", SILICONFLOW_API_KEY="test-key", OWNER_QQ=str(10**8), ONEBOT_TOKEN="a" * 32, NAPCAT_WEBUI_TOKEN="b" * 32, ASTRBOT_PASSWORD="SecureTest12345")
        ops.validate_env(env)
        for key, value in [("QUIET_HOURS", "25-8"), ("PROACTIVE_MIN_MINUTES", "0"), ("MEMORY_AUTO_CLEANUP", "maybe"), ("OWNER_UMO", "qq:GroupMessage:group"), ("ASTRBOT_MEMORY", "128m"), ("ASTRBOT_PORT", "16099")]:
            with self.subTest(key=key):
                bad = dict(env, **{key: value})
                with self.assertRaises(ValueError):
                    ops.validate_env(bad)

    def test_real_pinned_schema_configuration_and_stable_defaults(self):
        schemas = json.loads((HERE.parents[1] / "tests/fixtures/macos-plugin-schemas.json").read_text())
        env = ops.read_env(HERE / ".env.example")
        env.update(DEEPSEEK_API_KEY="test-key", SILICONFLOW_API_KEY="test-key", OWNER_QQ=str(10**8), ONEBOT_TOKEN="a" * 32, NAPCAT_WEBUI_TOKEN="b" * 32, ASTRBOT_PASSWORD="SecureTest12345")
        staged = self.root.parent / "new-data"
        ops.configure(staged, env, schemas)
        configs = {name: json.loads((staged / f"astrbot/config/astrbot_plugin_{name}_config.json").read_text()) for name in schemas}
        self.assertEqual(configs["livingmemory"]["provider_settings"]["embedding_provider_id"], "sf-bge-m3")
        self.assertEqual(configs["livingmemory"]["filtering_settings"]["memory_scope_mode"], "session")
        self.assertFalse(configs["livingmemory"]["graph_memory"]["enabled"])
        self.assertFalse(configs["livingmemory"]["forgetting_agent"]["auto_cleanup_enabled"])
        self.assertFalse(configs["livingmemory"]["agent_tools"]["enable_agentic_recall_beta"])
        self.assertEqual(configs["proactive_chat"]["friend_settings"]["session_list"], [])
        self.assertEqual(configs["proactive_chat"]["friend_settings"]["schedule_settings"]["quiet_hours"], "23-8")
        self.assertFalse(configs["proactive_chat"]["telemetry_config"]["enabled"])
        self.assertEqual(configs["affective_core"]["appraisal_provider_id"], "deepseek-chat")
        cmd = json.loads((staged / "astrbot/cmd_config.json").read_text())
        self.assertEqual([p["type"] for p in cmd["provider"]], ["openai_chat_completion", "openai_embedding"])
        self.assertEqual(cmd["platform"][0]["ws_reverse_token"], env["ONEBOT_TOKEN"])
        # Reject unknown generated keys against snapshots of actual pinned schemas.
        def verify_keys(config, schema):
            self.assertEqual(set(config), set(schema))
            for key, item in schema.items():
                if item["type"] == "object":
                    verify_keys(config[key], item["items"])
        for name, config in configs.items():
            verify_keys(config, schemas[name])

    def test_init_never_overwrites_existing_data(self):
        self.stack.environment = lambda: {}
        before = self.db.read_bytes()
        with self.assertRaises(FileExistsError):
            self.stack.init()
        self.assertEqual(self.db.read_bytes(), before)

    def test_wal_database_snapshot_keeps_committed_rows(self):
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("insert into memory values ('committed in WAL')")
        connection.commit()
        archive = self.stack.backup()
        destination = self.root.parent / "wal-restore"
        ops.restore(archive, destination)
        with sqlite3.connect(destination / ".runtime/data/astrbot/state.db") as db:
            self.assertEqual(db.execute("select count(*) from memory").fetchone()[0], 2)

    def test_missed_stop_blocks_inconsistent_backup(self):
        self.stack.ensure_clean_stop = lambda: (_ for _ in ()).throw(RuntimeError("forced kill"))
        with self.assertRaisesRegex(RuntimeError, "forced kill"):
            self.stack.backup()
        self.assertEqual(self.stack.live, ["astrbot"])
        self.assertFalse(list(self.stack.backups.glob("*.tar.gz")))

    def test_image_archive_tampering_blocks_restore(self):
        archive = self.stack.backup()
        image = next((self.stack.backups / "images").glob("*.tar"))
        image.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            ops.restore(archive, self.root.parent / "bad-images")

    def test_upgrade_failure_restores_pre_migration_database_and_image(self):
        self.stack.environment = lambda: {"DAFEIYU_IMAGE": "dafeiyu-macos:test"}
        self.stack.require_arm_daemon = lambda: None
        self.stack.require_arm_images = lambda: None
        def install(destination):
            plugin = destination / "astrbot/plugins/astrbot_plugin_face_convert"
            plugin.mkdir(parents=True)
            (plugin / "main.py").write_text("new code")
        self.stack.install_plugins = install
        original = self.stack.compose
        failed_once = False
        def compose(*args, **kwargs):
            nonlocal failed_once
            if args[0] == "up" and not failed_once:
                failed_once = True
                # A failed new runtime has already modified its database.
                with sqlite3.connect(self.db) as db:
                    db.execute("insert into memory values ('new schema runtime')")
                raise RuntimeError("new runtime unhealthy")
            if args[0] == "up":
                self.stack.live = [s for s in args[1:] if s in ops.SERVICES]
            return original(*args, **kwargs)
        self.stack.compose = compose
        with patch.object(ops, "run", return_value="sha256:" + "b" * 64):
            with self.assertRaisesRegex(RuntimeError, "new runtime unhealthy"):
                self.stack.upgrade()
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("select count(*) from memory").fetchone()[0], 1)
        self.assertEqual(self.stack.live, ["astrbot"])
        self.assertTrue(list(self.stack.runtime.glob("failed-upgrade-*")))
        lock = json.loads((self.stack.runtime / "image-lock.json").read_text())
        self.assertEqual(lock["services"]["astrbot"]["image"], "sha256:" + "a" * 64)

    def test_successful_upgrade_keeps_settings_and_stopped_service_stopped(self):
        self.stack.environment = lambda: {"DAFEIYU_IMAGE": "dafeiyu-macos:test"}
        self.stack.require_arm_daemon = lambda: None
        self.stack.require_arm_images = lambda: None
        settings = self.stack.data / "astrbot/plugins/astrbot_plugin_face_convert/config.json"
        ops.write_json(settings, {"max_media_mb": 12})
        def install(destination):
            plugin = destination / "astrbot/plugins/astrbot_plugin_face_convert"
            plugin.mkdir(parents=True)
            (plugin / "main.py").write_text("new code")
        self.stack.install_plugins = install
        original = self.stack.compose
        def compose(*args, **kwargs):
            if args[0] == "up":
                self.stack.live = [s for s in args[1:] if s in ops.SERVICES]
            return original(*args, **kwargs)
        self.stack.compose = compose
        with patch.object(ops, "run", return_value="sha256:" + "b" * 64):
            self.stack.upgrade()
        self.assertEqual(self.stack.live, ["astrbot"])
        self.assertEqual(json.loads(settings.read_text()), {"max_media_mb": 12})
        self.assertEqual((settings.parent / "main.py").read_text(), "new code")
        self.assertTrue(list(self.stack.runtime.glob("pre-upgrade-*")))


if __name__ == "__main__":
    unittest.main()
