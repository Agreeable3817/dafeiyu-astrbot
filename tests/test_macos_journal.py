"""Exercise archive hooks with framework doubles and a real persistent SQLite DB."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

JOURNAL = Path(__file__).resolve().parents[1] / "deploy/macos/journal"
spec = importlib.util.spec_from_file_location("macos_journal_store", JOURNAL / "store.py")
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def load_journal(data):
    package = types.ModuleType("macos_archive_fixture")
    package.__path__ = [str(JOURNAL)]
    api, events, stars = (types.ModuleType(n) for n in ("astrbot.api", "astrbot.api.event", "astrbot.api.star"))
    api.logger = Mock()
    class Filters:
        EventMessageType = types.SimpleNamespace(ALL="all")
        def __getattr__(self, name):
            return lambda *args, **kwargs: lambda function: function
    events.filter = Filters()
    events.AstrMessageEvent = object
    class Star:
        def __init__(self, context):
            self.context = context
    stars.Star = Star
    stars.Context = object
    stars.StarTools = types.SimpleNamespace(get_data_dir=lambda name: data)
    stars.register = lambda *args: lambda cls: cls
    modules = {"macos_archive_fixture": package, "macos_archive_fixture.store": store,
               "astrbot.api": api, "astrbot.api.event": events, "astrbot.api.star": stars}
    spec = importlib.util.spec_from_file_location("macos_archive_fixture.main", JOURNAL / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module.Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.owner = str(10**8)
        self.plugin = load_journal(self.data)(types.SimpleNamespace(
            get_config=lambda **kw: {"admins_id": [self.owner]}))
        self.extra = {}
        component = types.SimpleNamespace(model_dump=lambda **kw: {"type": "Plain", "text": "original private message"})
        self.event = types.SimpleNamespace(
            get_platform_name=lambda: "aiocqhttp", get_group_id=lambda: "",
            get_sender_id=lambda: self.owner, unified_msg_origin="qq:FriendMessage:" + self.owner,
            get_extra=lambda k: self.extra.get(k), set_extra=lambda k, v: self.extra.update({k: v}),
            message_obj=types.SimpleNamespace(message_id="message-id"),
            get_messages=lambda: [component], get_result=lambda: types.SimpleNamespace(chain=[component]))

    def test_real_hook_sequence_persists_and_deduplicates_on_reopen(self):
        async def dispatch():
            await self.plugin.incoming(self.event)
            await self.plugin.incoming(self.event)
            await self.plugin.prepared(self.event)
            await self.plugin.after_send(self.event)
        asyncio.run(dispatch())
        reopened = store.Store(self.data / "chat.db")
        with sqlite3.connect(reopened.path) as db:
            records = [json.loads(row[0]) for row in db.execute("select record from messages order by id")]
        self.assertEqual([r["stage"] for r in records], ["incoming", "outbound_prepared", "outbound_hook_ran"])
        self.assertTrue(all(not r["delivery_verified"] for r in records))
        self.assertEqual(records[0]["components"][0]["text"], "original private message")

    def test_other_people_groups_and_other_platforms_are_not_archived(self):
        for key, value in [("get_sender_id", "visitor"), ("get_group_id", "group"), ("get_platform_name", "other")]:
            original = getattr(self.event, key)
            setattr(self.event, key, lambda: value)
            asyncio.run(self.plugin.incoming(self.event))
            setattr(self.event, key, original)
        with sqlite3.connect(self.data / "chat.db") as db:
            self.assertEqual(db.execute("select count(*) from messages").fetchone()[0], 0)

    def test_concurrent_duplicate_writes_are_atomic(self):
        s = store.Store(self.data / "parallel.db")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: s.append(str(n % 20), "incoming", {"n": n % 20}), range(100)))
        with sqlite3.connect(s.path) as db:
            self.assertEqual(db.execute("select count(*) from messages").fetchone()[0], 20)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
