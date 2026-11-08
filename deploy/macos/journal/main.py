import asyncio
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sys

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .store import Store


@register("astrbot_plugin_dafeiyu_journal", "dafeiyu-tools", "主人私聊持久化归档", "0.1.0")
class Journal(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.store = Store(Path(StarTools.get_data_dir("astrbot_plugin_dafeiyu_journal")) / "chat.db")

    def allowed(self, event):
        if event.get_platform_name() != "aiocqhttp" or event.get_group_id():
            return False
        config = self.context.get_config(umo=event.unified_msg_origin)
        return str(event.get_sender_id()) in [str(x) for x in config.get("admins_id", [])]

    async def record(self, event, stage, chain):
        if not self.allowed(event):
            return
        key = event.get_extra("dafeiyu_journal_key")
        if not key:
            message_id = str(getattr(event.message_obj, "message_id", "") or secrets.token_hex(16))
            key = event.unified_msg_origin + ":" + message_id
            event.set_extra("dafeiyu_journal_key", key)
        # AstrBot components are Pydantic models. Keep all component metadata;
        # media bytes/expiring remote attachments are not downloaded here.
        payload = [component.model_dump(mode="json") for component in chain]
        record = {"schema": 1, "time": datetime.now(timezone.utc).isoformat(),
                  "umo": event.unified_msg_origin,
                  "sender_id": str(event.get_sender_id() if stage == "incoming" else getattr(event, "get_self_id", lambda: "bot")()),
                  "stage": stage, "components": payload,
                  "delivery_verified": False}
        try:
            await asyncio.to_thread(self.store.append, key, stage, record)
        except Exception:
            # Do not put private message content or exception payloads in logs.
            logger.error("[dafeiyu_journal] archive write failed; inspect disk/database locally")
            raise

    @filter.event_message_type(filter.EventMessageType.ALL, priority=sys.maxsize - 1)
    async def incoming(self, event: AstrMessageEvent):
        await self.record(event, "incoming", event.get_messages())

    @filter.on_decorating_result(priority=-sys.maxsize)
    async def prepared(self, event: AstrMessageEvent):
        result = event.get_result()
        if result:
            await self.record(event, "outbound_prepared", result.chain)

    @filter.after_message_sent(priority=-sys.maxsize)
    async def after_send(self, event: AstrMessageEvent):
        result = event.get_result()
        if result:
            await self.record(event, "outbound_hook_ran", result.chain)
