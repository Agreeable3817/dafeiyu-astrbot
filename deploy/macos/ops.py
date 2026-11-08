#!/usr/bin/env python3
"""Isolated Mac deployment operations. Python 3.10+, standard library only.

Never sources .env, prunes Docker, deletes data, or changes another project.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent
PROJECT = "dafeiyu-macos-m2"
KIT_FILES = ("ops.py", "deploy.sh", "backup.sh", "docker-compose.yml", "Dockerfile",
             "sources.lock.json", ".env.example", ".gitignore", "README-macos.md",
             "journal/main.py", "journal/store.py", "journal/metadata.yaml",
             "requirements-extra.lock.txt")
SERVICES = ("astrbot", "napcat")
BASE_IMAGES = {
    "soulter/astrbot:v4.29.0": "sha256:66ae67e93be2996f9aaef29d90f659c0b7f1d245a45cda8bea40ebdd0f34d943",
    "mlikiowa/napcat-docker:v4.18.37": "sha256:5973e8f1b16c0c42cb72bb9db99d93d8143012dcdc1dd6f91257b48054eb302f",
}


def run(args, *, cwd=None, capture=False):
    # Keep Docker context/credentials selected by the user, but prevent Compose
    # interpolation from inheriting accidental shell overrides or other projects.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("COMPOSE_", "DAFEIYU_", "ASTRBOT_", "NAPCAT_")) and k != "BOT_QQ"}
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout or ""


def read_env(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in result:
            raise ValueError("Invalid or duplicated .env key (values suppressed)")
        value = value.strip()
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Unclosed .env quote")
            value = value[1:-1]
        result[key] = value
    return result


def validate_env(env):
    for key in ("DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY", "OWNER_QQ",
                "ONEBOT_TOKEN", "NAPCAT_WEBUI_TOKEN", "ASTRBOT_PASSWORD"):
        if not env.get(key) or env[key].lower().startswith(("replace", "your_")):
            raise ValueError(f"Fill {key} locally in .env")
    if any("$" in v or "\\" in v or "\n" in v or "\r" in v for v in env.values()):
        raise ValueError(".env values must be literal, without $, backslash or newlines")
    if not re.fullmatch(r"[1-9][0-9]{4,11}", env["OWNER_QQ"]):
        raise ValueError("Invalid OWNER_QQ")
    if env.get("BOT_QQ") and (not re.fullmatch(r"[1-9][0-9]{4,11}", env["BOT_QQ"]) or env["BOT_QQ"] == env["OWNER_QQ"]):
        raise ValueError("BOT_QQ must be a separate valid bot account")
    umo = env.get("OWNER_UMO", "")
    if umo and not re.fullmatch(r"[^:\s]+:FriendMessage:" + re.escape(env["OWNER_QQ"]), umo):
        raise ValueError("OWNER_UMO must be the owner's exact private-chat /sid")
    for key in ("ONEBOT_TOKEN", "NAPCAT_WEBUI_TOKEN"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", env[key]):
            raise ValueError(f"{key}: use 24+ URL-safe random characters")
    if env["ONEBOT_TOKEN"] == env["NAPCAT_WEBUI_TOKEN"]:
        raise ValueError("Use distinct tokens")
    password = env["ASTRBOT_PASSWORD"]
    if len(password) < 12 or not all(re.search(p, password) for p in (r"[A-Z]", r"[a-z]", r"[0-9]")):
        raise ValueError("ASTRBOT_PASSWORD requires 12+ chars, uppercase, lowercase and digit")
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", env.get("QUIET_HOURS", "23-8"))
    if not match or any(int(x) > 23 for x in match.groups()) or int(match[1]) == int(match[2]):
        raise ValueError("QUIET_HOURS must be two distinct hours in 0..23")
    low = int(env.get("PROACTIVE_MIN_MINUTES", 120))
    high = int(env.get("PROACTIVE_MAX_MINUTES", 240))
    if not 30 <= low <= high <= 2880:
        raise ValueError("Proactive interval requires 30 <= min <= max <= 2880")
    if not 1 <= int(env.get("PROACTIVE_MAX_UNANSWERED", 2)) <= 5:
        raise ValueError("PROACTIVE_MAX_UNANSWERED requires 1..5")
    if env.get("MEMORY_AUTO_CLEANUP", "false") not in ("true", "false"):
        raise ValueError("MEMORY_AUTO_CLEANUP must be true or false")
    if not 30 <= int(env.get("MEMORY_CLEANUP_DAYS", 90)) <= 3650:
        raise ValueError("MEMORY_CLEANUP_DAYS requires 30..3650")
    if not 0 <= float(env.get("MEMORY_CLEANUP_IMPORTANCE", .3)) <= 1:
        raise ValueError("MEMORY_CLEANUP_IMPORTANCE requires 0..1")
    ports = [int(env.get(k, d)) for k, d in (("ASTRBOT_PORT", 16185), ("NAPCAT_PORT", 16099))]
    if len(set(ports)) != 2 or any(not 1024 <= p <= 65535 for p in ports):
        raise ValueError("Use two distinct unprivileged ports")
    for key, default in (("ASTRBOT_MEMORY", "1536m"), ("NAPCAT_MEMORY", "1280m")):
        m = re.fullmatch(r"(\d+)m", env.get(key, default))
        if not m or not 1024 <= int(m[1]) <= 4096:
            raise ValueError(f"{key}: use 1024m..4096m; smaller caps risk repeated OOM")
    for key in ("ASTRBOT_CPUS", "NAPCAT_CPUS"):
        if not .5 <= float(env.get(key, 1)) <= 2:
            raise ValueError(f"{key} requires 0.5..2")
    if not re.fullmatch(r"dafeiyu-macos:[a-zA-Z0-9_.-]+", env.get("DAFEIYU_IMAGE", "dafeiyu-macos:20261108")):
        raise ValueError("Use a local dafeiyu-macos:<version> image tag")


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    try:
        temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def operation_lock(root):
    path = root / ".operation.lock"
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        raise RuntimeError("Another operation or stale .operation.lock exists; inspect before removing it")
    try:
        (path / "pid").write_text(str(os.getpid()))
        yield
    finally:
        shutil.rmtree(path)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksum(path):
    Path(str(path) + ".sha256").write_text(sha256(path) + "\n")
    Path(str(path) + ".sha256").chmod(0o600)


def verify_checksum(path):
    if Path(str(path) + ".sha256").read_text().strip() != sha256(path):
        raise ValueError("Archive/image checksum mismatch")


def check_databases(root):
    count = 0
    for path in Path(root).rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as f:
                is_sqlite = f.read(16) == b"SQLite format 3\x00"
            if is_sqlite:
                with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise ValueError("SQLite integrity_check failed")
                count += 1
    return count


def schema_defaults(schema):
    return {key: schema_defaults(value["items"]) if value.get("type") == "object"
            else value.get("default") for key, value in schema.items()}


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def configure(data, env, schemas):
    """Seed a new data tree. Never call on an existing installation."""
    owner, umo = env["OWNER_QQ"], env.get("OWNER_UMO", "")
    config = {
        "config_version": 4, "admins_id": [owner], "timezone": "Asia/Shanghai",
        "platform_settings": {"enable_id_white_list": True,
                              "id_whitelist": [umo] if umo else [],
                              "wl_ignore_admin_on_friend": True,
                              "wl_ignore_admin_on_group": False},
        "platform": [{"id": "qq", "type": "aiocqhttp", "enable": True,
                      "ws_reverse_host": "0.0.0.0", "ws_reverse_port": 6199,
                      "ws_reverse_token": env["ONEBOT_TOKEN"]}],
        "provider": [
            {"id": "deepseek-chat", "type": "openai_chat_completion", "provider": "deepseek",
             "provider_type": "chat_completion", "enable": True,
             "key": [env["DEEPSEEK_API_KEY"]], "api_base": "https://api.deepseek.com/v1",
             "model": "deepseek-chat", "timeout": 90, "proxy": ""},
            {"id": "sf-bge-m3", "type": "openai_embedding", "provider_type": "embedding",
             "enable": True, "embedding_api_base": "https://api.siliconflow.cn/v1",
             "embedding_api_key": env["SILICONFLOW_API_KEY"], "embedding_model": "BAAI/bge-m3",
             "embedding_dimensions": 1024, "embedding_dimensions_mode": "never", "timeout": 60}],
        "agent_runner": {"runner_type": "local", "config": {
            "model": {"provider_id": "deepseek-chat", "request_max_retries": 2},
            "compression": {"max_turns": 30, "trim_turns": 2,
                            "overflow_strategy": "truncate_by_turns"},
            "misc": {"max_steps": 8}}},
        "provider_settings": {"web_search": False, "streaming_response": False,
                              "default_image_caption_provider_id": ""},
        "provider_tts_settings": {"enable": False}, "provider_stt_settings": {"enable": False},
        "dashboard": {"enable": True, "host": "0.0.0.0", "port": 6185,
                      "username": "astrbot", "password": hashlib.md5(env["ASTRBOT_PASSWORD"].encode()).hexdigest(),
                      "pbkdf2_password": password_hash(env["ASTRBOT_PASSWORD"]),
                      "password_storage_upgraded": True, "password_change_required": False},
        "log_file_enable": False, "trace_log_enable": False, "log_level": "INFO",
        "disable_metrics": True, "temp_dir_max_size": 128,
    }
    write_json(data / "astrbot/cmd_config.json", config)
    living = schema_defaults(schemas["livingmemory"])
    living["access_control"].update(whitelist_enabled=True, allowed_ids=owner)
    living["provider_settings"].update(embedding_provider_id="sf-bge-m3", llm_provider_id="deepseek-chat")
    living["session_manager"].update(enable_full_group_capture=False, max_sessions=4,
                                       context_window_size=30, max_messages_per_session=200)
    living["filtering_settings"].update(memory_scope_mode="session", use_session_filtering=True)
    living["graph_memory"].update(enabled=False, atom_enabled=False)
    living["reflection_engine"].update(summary_trigger_rounds=10, max_segments_per_run=1)
    living["recall_engine"].update(top_k=3, max_k=5, search_cache_max_size=32)
    living["index_rebuild_settings"].update(batch_size=20, embedding_batch_size=2, tasks_limit=1)
    living["forgetting_agent"].update(auto_cleanup_enabled=env.get("MEMORY_AUTO_CLEANUP", "false") == "true",
                                     auto_archived_enabled=False,
                                     cleanup_days_threshold=int(env.get("MEMORY_CLEANUP_DAYS", 90)),
                                     cleanup_importance_threshold=float(env.get("MEMORY_CLEANUP_IMPORTANCE", .3)))
    living["memory_consolidation"]["enabled"] = False
    living["agent_tools"].update(enable_agentic_recall_beta=False, enable_forget_tool=False)
    proactive = schema_defaults(schemas["proactive_chat"])
    friend = proactive["friend_settings"]
    friend.update(enable=True, session_list=[umo] if umo else [])
    friend["auto_trigger_settings"].update(enable_auto_trigger=True, auto_trigger_after_minutes=30)
    friend["context_settings"]["source_mode"] = "conversation_history"
    friend["schedule_settings"].update(min_interval_minutes=int(env.get("PROACTIVE_MIN_MINUTES", 120)),
                                       max_interval_minutes=int(env.get("PROACTIVE_MAX_MINUTES", 240)),
                                       quiet_hours=env.get("QUIET_HOURS", "23-8"),
                                       max_unanswered_times=int(env.get("PROACTIVE_MAX_UNANSWERED", 2)))
    friend["tts_settings"]["enable_tts"] = False
    proactive["group_settings"]["enable"] = False
    proactive["web_admin"]["enabled"] = False
    proactive["notification_settings"]["enabled"] = False
    proactive["telemetry_config"]["enabled"] = False
    affective = schema_defaults(schemas["affective_core"])
    affective.update(owner_id=owner, owner_scope=umo, appraisal_provider_id="deepseek-chat")
    for name, settings in (("livingmemory", living), ("proactive_chat", proactive), ("affective_core", affective)):
        write_json(data / f"astrbot/config/astrbot_plugin_{name}_config.json", settings)
    write_json(data / "astrbot/plugins/astrbot_plugin_face_convert/config.json",
               {"max_media_mb": 10, "video_max_duration_s": 30, "video_max_frames": 2,
                "video_workers": 1, "max_concurrent_media": 1})
    write_json(data / "napcat/config/webui.json", {"host": "0.0.0.0", "port": 6099,
               "prefix": "", "token": env["NAPCAT_WEBUI_TOKEN"], "loginRate": 3})
    write_json(data / "napcat/config/onebot11.json", {"network": {
        "httpServers": [], "httpSseServers": [], "httpClients": [], "websocketServers": [],
        "websocketClients": [{"enable": True, "name": "astrbot", "url": "ws://astrbot:6199/ws",
                              "reportSelfMessage": False, "messagePostFormat": "array",
                              "token": env["ONEBOT_TOKEN"], "debug": False,
                              "heartInterval": 30000, "reconnectInterval": 10000}], "plugins": []},
        "musicSignUrl": "", "enableLocalFile2Url": False, "parseMultMsg": False})
    (data / "napcat/qq").mkdir(parents=True, exist_ok=True)
    (data / "napcat/plugins").mkdir(parents=True, exist_ok=True)


class Stack:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.runtime = self.root / ".runtime"
        self.data = self.runtime / "data"
        self.backups = self.runtime / "backups"

    def compose(self, *args, capture=False, use_lock=True):
        command = ["docker", "compose", "--project-name", PROJECT,
                   "--project-directory", self.root, "--env-file", self.root / ".env",
                   "-f", self.root / "docker-compose.yml"]
        override = self.runtime / "image-lock.json"
        if use_lock and override.exists():
            command += ["-f", override]
        return run(command + list(args), cwd=self.root, capture=capture)

    def require_install(self):
        if not (self.runtime / "installation.json").is_file():
            raise RuntimeError("Not an initialized deployment; run init first")

    def check_ownership(self):
        containers = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}", "-q"], capture=True).split()
        for container in containers:
            directory = run(["docker", "inspect", "--format",
                             '{{index .Config.Labels "com.docker.compose.project.working_dir"}}', container], capture=True).strip()
            if directory != str(self.root):
                raise RuntimeError("Project name belongs to another directory; refusing to touch its containers")

    def ensure_clean_stop(self):
        for service in SERVICES:
            container = self.compose("ps", "-a", "-q", service, capture=True).strip()
            if container:
                state = json.loads(run(["docker", "inspect", "--format", "{{json .State}}", container], capture=True))
                if state.get("Running") or state.get("OOMKilled") or state.get("ExitCode") in (137, 139):
                    raise RuntimeError("Container was killed/OOM or is still running; clean database snapshot not established")

    def environment(self):
        envfile = self.root / ".env"
        if envfile.is_symlink():
            raise ValueError(".env must be a local regular file")
        env = read_env(envfile)
        validate_env(env)
        envfile.chmod(0o600)
        return env

    def check(self):
        self.environment()
        self.compose("config", "--quiet")
        print("Environment and Compose syntax: OK (secret values suppressed)")

    def check_arm(self):
        for tag, digest in BASE_IMAGES.items():
            manifest = json.loads(run(["docker", "manifest", "inspect", tag], capture=True))
            matches = [m for m in manifest.get("manifests", [])
                       if m.get("platform", {}).get("os") == "linux"
                       and m["platform"].get("architecture") == "arm64"]
            if not any(m["digest"] == digest for m in matches):
                raise RuntimeError(f"ARM64 digest changed or unavailable: {tag}")
            print(f"{tag}: linux/arm64 {digest}")

    def prepare(self):
        """Only fetch/build inputs; never changes installed plugins or data."""
        self.runtime.mkdir(mode=0o700, exist_ok=True)
        lock = json.loads((self.root / "sources.lock.json").read_text())
        sources = self.runtime / "sources"
        sources.mkdir(exist_ok=True)
        for name, source in lock.items():
            destination = sources / name
            if not destination.exists():
                run(["git", "init", "-q", destination])
                run(["git", "-C", destination, "remote", "add", "origin", source["url"]])
            run(["git", "-C", destination, "fetch", "--depth", "1", "origin", source["commit"]])
            run(["git", "-C", destination, "checkout", "--detach", "--force", source["commit"]])
            if run(["git", "-C", destination, "rev-parse", "HEAD"], capture=True).strip() != source["commit"]:
                raise ValueError("Source commit mismatch")
            if source.get("schema_sha256") and sha256(destination / "_conf_schema.json") != source["schema_sha256"]:
                raise ValueError("Pinned configuration schema mismatch")
        build = self.runtime / "build"
        build.mkdir(exist_ok=True)
        requirements = (sources / "livingmemory/requirements.txt").read_text() + "\n"
        requirements += (sources / "proactive_chat/requirements.txt").read_text() + "\n"
        # These are the dafeiyu core dependencies; faiss 1.14.2 is explicitly
        # incompatible per LivingMemory. This library is an index, not a model.
        requirements += "\n" + (self.root / "requirements-extra.lock.txt").read_text()
        (build / "requirements.txt").write_text(requirements)
        print("Pinned source inputs prepared; runtime data unchanged")

    def install_plugins(self, destination):
        repository = self.root.parents[1]
        if not (repository / "plugin").is_dir():
            raise RuntimeError("init/upgrade requires this kit inside its full Git checkout")
        sources = self.runtime / "sources"
        plugins = destination / "astrbot/plugins"
        plugins.mkdir(parents=True, exist_ok=True)
        mapping = {"face_convert": repository / "plugin/astrbot_plugin_face_convert",
                   "schedule_awareness": repository / "plugin/astrbot_plugin_schedule_awareness",
                   "affective_core": repository / "plugin/astrbot_plugin_affective_core",
                   "livingmemory": sources / "livingmemory", "proactive_chat": sources / "proactive_chat",
                   "dafeiyu_journal": self.root / "journal"}
        for name, source in mapping.items():
            target = plugins / f"astrbot_plugin_{name}"
            if target.exists():
                # Upgrade only code. Preserve plugin-local config and runtime files.
                raise FileExistsError("Plugin target exists; install only into a new staging tree")
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", "config.json", "*.db", "*.db-*", ".env", ".venv", "node_modules"))
        return {name: json.loads((mapping[name] / "_conf_schema.json").read_text())
                for name in ("livingmemory", "proactive_chat", "affective_core")}

    def init(self):
        env = self.environment()
        if self.data.exists() or (self.runtime / "installation.json").exists():
            raise FileExistsError("Refusing to initialize over existing data")
        self.check()
        self.prepare()
        with tempfile.TemporaryDirectory(prefix="init-", dir=self.runtime) as tmp:
            staged = Path(tmp) / "data"
            schemas = self.install_plugins(staged)
            configure(staged, env, schemas)
            staged.rename(self.data)
        repo = self.root.parents[1]
        prompt = (repo / "persona/大肥鱼-人格.md").read_text(encoding="utf-8")
        # Use the existing project's extractor rather than guessing persona syntax.
        import importlib.util
        spec = importlib.util.spec_from_file_location("dafeiyu_preflight", repo / "deploy-kit/preflight.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The raw source remains available even if a future extractor changes.
        (self.runtime / "persona-source.md").write_text(prompt)
        (self.runtime / "persona.txt").write_text(module.extract_persona_prompt(prompt), encoding="utf-8")
        revision = run(["git", "-C", repo, "rev-parse", "HEAD"], capture=True).strip()
        write_json(self.runtime / "installation.json", {"schema": 1, "dafeiyu_commit": revision,
                   "sources": json.loads((self.root / "sources.lock.json").read_text())})
        print("Initialized isolated data. Import persona and set the exact /sid before enabling proactive sends.")

    def require_arm_daemon(self):
        arch = run(["docker", "info", "--format", "{{.Architecture}}"], capture=True).strip()
        if arch not in ("aarch64", "arm64"):
            raise RuntimeError("Production start requires a native ARM64 Docker daemon; no amd64 emulation fallback")

    def start(self):
        self.require_install()
        self.check()
        self.require_arm_daemon()
        self.require_arm_images()
        self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "300")

    def require_arm_images(self):
        config = json.loads(self.compose("config", "--format", "json", capture=True))
        for service in SERVICES:
            image = config["services"][service]["image"]
            arch = run(["docker", "image", "inspect", "--format", "{{.Architecture}}", image], capture=True).strip()
            if arch != "arm64":
                raise RuntimeError(f"{service} image is not native ARM64")

    def upgrade(self):
        """Upgrade code using already-built images. Keep pre-migration data intact."""
        self.require_install()
        self.environment()
        self.require_arm_daemon()
        records = self.image_records()
        if set(records) != set(SERVICES):
            raise RuntimeError("Upgrade requires both existing containers so their image IDs can be rolled back")
        env = self.environment()
        new_astrbot = run(["docker", "image", "inspect", "--format", "{{.Id}}", env.get("DAFEIYU_IMAGE", "dafeiyu-macos:20261108")], capture=True).strip()
        napcat_ref = "mlikiowa/napcat-docker@" + list(BASE_IMAGES.values())[1]
        new_napcat = run(["docker", "image", "inspect", "--format", "{{.Id}}", napcat_ref], capture=True).strip()
        archive = self.backup()
        stamp = secrets.token_hex(6)
        previous = self.runtime / ("pre-upgrade-" + stamp)
        failed = self.runtime / ("failed-upgrade-" + stamp)
        live = self.compose("ps", "--status", "running", "--services", capture=True).split()
        lockpath = self.runtime / "image-lock.json"
        promoted = False
        try:
            self.compose("stop", *SERVICES)
            self.ensure_clean_stop()
            with tempfile.TemporaryDirectory(prefix="upgrade-", dir=self.runtime) as tmp:
                staged = Path(tmp) / "data"
                shutil.copytree(self.data, staged, symlinks=True)
                new_code = Path(tmp) / "new-code"
                self.install_plugins(new_code)
                for source in (new_code / "astrbot/plugins").iterdir():
                    target = staged / "astrbot/plugins" / source.name
                    preserved_config = (target / "config.json").read_bytes() if (target / "config.json").exists() else None
                    # Only the staged copy is replaced; the entire old tree remains.
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(source, target)
                    if preserved_config is not None:
                        (target / "config.json").write_bytes(preserved_config)
                self.data.rename(previous)
                try:
                    staged.rename(self.data)
                except BaseException:
                    previous.rename(self.data)
                    raise
                promoted = True
            write_json(lockpath, {"services": {"astrbot": {"image": new_astrbot, "pull_policy": "never"},
                                               "napcat": {"image": new_napcat, "pull_policy": "never"}}})
            self.require_arm_images()
            # Recreate both containers, even if stopped, to pin the new image IDs;
            # start only those that were running before the upgrade.
            self.compose("create", "--no-build", *SERVICES)
            if live:
                self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "300", *live)
        except BaseException:
            self.compose("stop", *SERVICES)
            if promoted:
                self.data.rename(failed)
                previous.rename(self.data)
            write_json(lockpath, {"services": {s: {"image": i, "pull_policy": "never"} for s, i in records.items()}})
            self.compose("create", "--no-build", *SERVICES)
            if live:
                self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "300", *live)
            raise
        installation = json.loads((self.runtime / "installation.json").read_text())
        installation["sources"] = json.loads((self.root / "sources.lock.json").read_text())
        installation["dafeiyu_commit"] = run(["git", "-C", self.root.parents[1], "rev-parse", "HEAD"], capture=True).strip()
        write_json(self.runtime / "installation.json", installation)
        print(f"Upgrade complete. Before-migration data: {previous}; rollback backup: {archive}")

    def image_records(self):
        records = {}
        for service in SERVICES:
            container = self.compose("ps", "-a", "-q", service, capture=True).strip()
            if container:
                image = run(["docker", "inspect", "--format", "{{.Image}}", container], capture=True).strip()
                records[service] = image
        return records

    def save_images(self, records):
        vault = self.backups / "images"
        vault.mkdir(parents=True, exist_ok=True)
        for image in set(records.values()):
            path = vault / (image.removeprefix("sha256:") + ".tar")
            if path.exists():
                verify_checksum(path)
                continue
            temporary = path.with_suffix(".partial")
            run(["docker", "image", "save", "--output", temporary, image])
            temporary.chmod(0o600)
            temporary.replace(path)
            write_checksum(path)

    @contextlib.contextmanager
    def quiesced(self):
        was_running = self.compose("ps", "--status", "running", "--services", capture=True).split()
        if any(s not in SERVICES for s in was_running):
            raise RuntimeError("Unexpected service in this project's namespace")
        try:
            self.compose("stop", *SERVICES)
            if self.compose("ps", "--status", "running", "--services", capture=True).strip():
                raise RuntimeError("Services still running; refusing inconsistent snapshot")
            self.ensure_clean_stop()
            yield
        finally:
            if was_running:
                self.compose("start", "--wait", "--wait-timeout", "300", *was_running)

    def backup(self):
        self.require_install()
        self.backups.mkdir(parents=True, mode=0o700, exist_ok=True)
        records = self.image_records()
        if set(records) != set(SERVICES):
            raise RuntimeError("Backup needs both created containers to preserve exact rollback images; start once first")
        self.save_images(records)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
        archive = self.backups / (stamp + ".tar.gz")
        temporary = archive.with_suffix(".partial")
        try:
            with self.quiesced():
                # Validate a copy: SQLite may recover a WAL; never modify original DBs.
                with tempfile.TemporaryDirectory(prefix="snapshot-", dir=self.runtime) as tmp:
                    snapshot = Path(tmp)
                    ignored = []
                    def transient_qq_locks(directory, names):
                        skip = []
                        for name in names:
                            p = Path(directory) / name
                            if p.is_symlink() and name in ("SingletonLock", "SingletonCookie", "SingletonSocket") and self.data / "napcat/qq" in p.parents:
                                skip.append(name)
                                ignored.append(str(p.relative_to(self.data)))
                        return skip
                    shutil.copytree(self.data, snapshot / ".runtime/data", symlinks=True,
                                    ignore=transient_qq_locks)
                    for name in KIT_FILES + (".env",):
                        (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(self.root / name, snapshot / name)
                    shutil.copy2(self.runtime / "installation.json", snapshot / ".runtime/installation.json")
                    for optional in ("persona-source.md", "persona.txt", "image-lock.json"):
                        if (self.runtime / optional).exists():
                            shutil.copy2(self.runtime / optional, snapshot / ".runtime" / optional)
                    for p in snapshot.rglob("*"):
                        if p.is_symlink() or (not p.is_file() and not p.is_dir()):
                            raise ValueError("Unsupported data link/special file; snapshot not published")
                    databases = check_databases(snapshot.resolve())
                    files = {str(p.relative_to(snapshot)): sha256(p) for p in snapshot.rglob("*") if p.is_file()}
                    write_json(snapshot / "backup-manifest.json", {"schema": 1, "files": files,
                               "images": records, "sqlite_checked": databases, "transient_qq_links_omitted": ignored})
                    with tarfile.open(temporary, "w:gz", compresslevel=1) as tar:
                        for p in sorted(snapshot.rglob("*")):
                            tar.add(p, arcname=str(p.relative_to(snapshot)), recursive=False)
                    temporary.chmod(0o600)
            # Publish only after original running services have been restored.
            temporary.replace(archive)
            write_checksum(archive)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Backup complete: {archive} (keep its .sha256 and sibling images/ together)")
        return archive


def unpack_verified(archive, target):
    verify_checksum(archive)
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or ".." in name.parts or member.name in seen
                    or not (member.isdir() or member.isfile())):
                raise ValueError("Unsafe archive member")
            seen.add(member.name)
            destination = target.joinpath(*name.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tar.extractfile(member) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
                destination.chmod(0o600)
    manifest = json.loads((target / "backup-manifest.json").read_text())
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported backup schema")
    actual = {str(p.relative_to(target)): sha256(p) for p in target.rglob("*")
              if p.is_file() and p != target / "backup-manifest.json"}
    if actual != manifest["files"]:
        raise ValueError("Backup content checksum mismatch")
    check_databases(target.resolve())
    for service, image in manifest["images"].items():
        if service not in SERVICES or not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ValueError("Invalid image identity in backup")
        verify_checksum(Path(archive).parent / "images" / (image[7:] + ".tar"))
    return manifest


def restore(archive, destination):
    """Stage a verified restoration into a NEW directory; never overwrite live data."""
    archive, destination = Path(archive).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Restore destination must not exist")
    if not destination.parent.is_dir():
        raise ValueError("Restore parent must already exist")
    with tempfile.TemporaryDirectory(prefix="dafeiyu-restore-", dir=destination.parent) as tmp:
        staged = Path(tmp) / "restored"
        staged.mkdir(mode=0o700)
        manifest = unpack_verified(archive, staged)
        if manifest["images"]:
            write_json(staged / ".runtime/image-lock.json", {"services": {
                service: {"image": image, "pull_policy": "never"}
                for service, image in manifest["images"].items()}})
        staged.rename(destination)
    print(f"Restored and SQLite-checked in new directory: {destination}; services remain stopped")


def main(argv=None):
    os.umask(0o077)
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "prepare", "check", "check-arm64", "build", "start", "stop", "retire", "upgrade", "status", "backup", "verify", "restore", "load-images"))
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    stack = Stack()
    with operation_lock(ROOT):
        if args.command in ("start", "stop", "retire", "upgrade", "backup", "status", "build"):
            stack.check_ownership()
        if args.command == "check":
            stack.check()
        elif args.command == "check-arm64":
            stack.check_arm()
        elif args.command == "prepare":
            stack.prepare()
        elif args.command == "init":
            stack.init()
        elif args.command == "build":
            stack.require_install()
            stack.check()
            stack.require_arm_daemon()
            stack.check_arm()
            stack.compose("build", "astrbot", use_lock=False)
            stack.compose("pull", "napcat", use_lock=False)
        elif args.command == "start":
            stack.start()
        elif args.command == "stop":
            stack.require_install()
            stack.compose("stop", *SERVICES)
        elif args.command == "retire":
            stack.require_install()
            stack.compose("down")  # no --volumes, no --remove-orphans, no global prune
        elif args.command == "upgrade":
            stack.upgrade()
        elif args.command == "status":
            stack.require_install()
            stack.compose("ps", "-a")
            stack.compose("stats", "--no-stream")
        elif args.command == "backup":
            stack.backup()
        elif args.command in ("verify", "restore", "load-images"):
            needed = 2 if args.command == "restore" else 1
            if len(args.paths) != needed:
                parser.error(f"{args.command} requires {needed} path argument(s)")
            archive = Path(args.paths[0]).resolve()
            if args.command == "restore":
                restore(archive, args.paths[1])
            else:
                with tempfile.TemporaryDirectory(prefix="dafeiyu-verify-") as tmp:
                    manifest = unpack_verified(archive, Path(tmp))
                if args.command == "load-images":
                    for image in set(manifest["images"].values()):
                        run(["docker", "image", "load", "--input", archive.parent / "images" / (image[7:] + ".tar")])
                print("Backup hashes, image archives and SQLite integrity: OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        # subprocess output may contain secrets; don't echo command/env/config.
        reason = str(exc) if isinstance(exc, (ValueError, RuntimeError, FileExistsError)) else "See prior error; private values are not echoed."
        print(f"Operation failed: {type(exc).__name__}: {reason}", file=sys.stderr)
        sys.exit(1)
