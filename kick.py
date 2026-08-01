"""
Motor Ultraligero de Chat para Kick (100% Python Nativo sin Playwright):
1. Obtiene chatroom_id mediante HTTP GET a la API pública de Kick (https://kick.com/api/v2/channels/{canal}).
2. Conecta por WebSocket nativo (aiohttp) a wss://ws-us2.pusher.com/app/eb1fd122312b2d88b72d.
3. Suscribe el canal 'chatrooms.<chatroom_id>.v2', responde pings y cuenta risas en tiempo real.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from pathlib import Path
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)

PUSHER_KEY = "32cbd69e4b950bf97679"


class KickDiscovery:
    def __init__(self, data_dir: Path):
        self.cache_file = data_dir / "kick_cache.json"

    def get_chatroom_id(self, channel: str) -> str | None:
        channel_lower = channel.lower()
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if channel_lower in data:
                    return data[channel_lower].get("chatroom_id")
            except Exception:
                pass

        try:
            url = f"https://kick.com/api/v2/channels/{channel_lower}"
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36"})
            with urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                chatroom_id = str(payload["chatroom"]["id"])
                self.save_cache(channel_lower, chatroom_id)
                return chatroom_id
        except Exception as exc:
            LOG.debug("Error obteniendo chatroom_id directo para Kick %s: %s", channel, exc)
            return None

    def save_cache(self, channel: str, chatroom_id: str):
        current = {}
        if self.cache_file.exists():
            try:
                current = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        current[channel.lower()] = {
            "chatroom_id": chatroom_id,
            "updated_at": time.time(),
        }
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(current, indent=2), encoding="utf-8")


class KickChatListener:
    def __init__(self, channel: str, data_dir: Path):
        self.channel = channel.lower()
        self.discovery = KickDiscovery(data_dir)
        self.messages = collections.deque(maxlen=1000)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def poll_and_reset(self) -> list[str]:
        mensajes = list(self.messages)
        self.messages.clear()
        return mensajes

    async def _listen_loop(self):
        import aiohttp
        while self._running:
            chatroom_id = await asyncio.to_thread(self.discovery.get_chatroom_id, self.channel)
            if not chatroom_id:
                await asyncio.sleep(20)
                continue

            ws_url = f"wss://ws-us2.pusher.com/app/{PUSHER_KEY}?protocol=7&client=js&version=7.4.0&flash=false"
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
                                elif "ChatMessage" in event or "message" in event.lower():
                                    texto = _texto_mensaje(payload.get("data", ""))
                                    if texto:
                                        self.messages.append(texto)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)


def _texto_mensaje(data) -> str:
    """Extrae el contenido real de los formatos de evento de Kick."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return data.strip()
    if not isinstance(data, dict):
        return ""
    for key in ("content", "text", "body"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("message", "data"):
        value = data.get(key)
        texto = _texto_mensaje(value)
        if texto:
            return texto
    return ""
