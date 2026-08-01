"""
Motor Híbrido de Chat para Kick:
1. Descubrimiento vía Playwright / caché JSON de chatroom_id y pusher_key.
2. Conexión WebSocket pura con asyncio / aiohttp a wss://ws-us2.pusher.com/app/<key>.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
import aiohttp

LOG = logging.getLogger(__name__)

HYPE_WORDS = {"jaja", "jajaja", "kekw", "wtf", "lmao", "lol", "pog", "clip", "xd", "omg", "base"}
DEFAULT_PUSHER_KEY = "eb1fd122312b2d88b72d"


class KickDiscovery:
    def __init__(self, data_dir: Path):
        self.cache_file = data_dir / "kick_cache.json"

    def get_cached(self, channel: str) -> dict | None:
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                return data.get(channel.lower())
            except Exception:
                pass
        return None

    def save_cache(self, channel: str, chatroom_id: int | str, pusher_key: str):
        current = {}
        if self.cache_file.exists():
            try:
                current = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        current[channel.lower()] = {
            "chatroom_id": str(chatroom_id),
            "pusher_key": pusher_key,
            "updated_at": time.time(),
        }
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(current, indent=2), encoding="utf-8")

    async def discover_playwright(self, channel: str) -> tuple[str, str] | None:
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                chatroom_id = None
                pusher_key = DEFAULT_PUSHER_KEY

                def handle_ws(ws):
                    nonlocal chatroom_id, pusher_key
                    if "pusher" in ws.url:
                        m_key = re.search(r"/app/([a-zA-Z0-9]+)", ws.url)
                        if m_key:
                            pusher_key = m_key.group(1)

                    def handle_frame(payload):
                        nonlocal chatroom_id
                        if isinstance(payload, str) and "chatrooms." in payload:
                            m_room = re.search(r"chatrooms\.(\d+)", payload)
                            if m_room:
                                chatroom_id = m_room.group(1)

                    ws.on("framereceived", handle_frame)

                page.on("websocket", handle_ws)
                await page.goto(f"https://kick.com/{channel}", wait_until="commit", timeout=15000)
                await asyncio.sleep(4)
                await browser.close()

                if chatroom_id:
                    self.save_cache(channel, chatroom_id, pusher_key)
                    return str(chatroom_id), pusher_key
        except Exception as exc:
            LOG.debug("Playwright discovery no disponible para %s: %s", channel, exc)

        return None


class KickChatListener:
    def __init__(self, channel: str, data_dir: Path):
        self.channel = channel.lower()
        self.discovery = KickDiscovery(data_dir)
        self.count = 0
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    def poll_and_reset(self) -> float:
        val = float(self.count)
        self.count = 0
        return val

    async def _listen_loop(self):
        while self._running:
            cached = self.discovery.get_cached(self.channel)
            chatroom_id, pusher_key = None, DEFAULT_PUSHER_KEY

            if cached:
                chatroom_id = cached.get("chatroom_id")
                pusher_key = cached.get("pusher_key", DEFAULT_PUSHER_KEY)

            if not chatroom_id:
                disc = await self.discovery.discover_playwright(self.channel)
                if disc:
                    chatroom_id, pusher_key = disc

            if not chatroom_id:
                await asyncio.sleep(30)
                continue

            ws_url = f"wss://ws-us2.pusher.com/app/{pusher_key}?protocol=7&client=js&version=7.4.0&flash=false"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, timeout=15) as ws:
                        sub_msg = {
                            "event": "pusher:subscribe",
                            "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"}
                        }
                        await ws.send_str(json.dumps(sub_msg))

                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                payload = json.loads(msg.data)
                                event = payload.get("event", "")
                                if event == "pusher:ping":
                                    await ws.send_str(json.dumps({"event": "pusher:pong", "data": {}}))
                                elif "ChatMessage" in event or "message" in event:
                                    data_str = payload.get("data", "")
                                    if any(w in data_str.lower() for w in HYPE_WORDS):
                                        self.count += 1
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)
