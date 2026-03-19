"""DiscordBotService — Discord bot via discord.py (optional dependency).

Manages a Discord bot client in a daemon thread with asyncio event loop.
Communication between sync OpenPaw code and async discord.py via
asyncio.run_coroutine_threadsafe().

Config:
    bot_token: str          — Discord bot token
    guild_ids: str          — Comma-separated guild IDs to listen to (optional)
    allowed_channels: str   — Comma-separated channel IDs (optional)
"""

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core import ServiceFactory
from services.base_messaging_service import BaseMessagingService

logger = logging.getLogger(__name__)


class DiscordBotService(BaseMessagingService):
    """Discord bot service using discord.py library."""

    TYPE = "discordBot"
    DESCRIPTION = "Discord Bot connection (websocket gateway)"
    TAGS = ["discord", "bot", "messaging"]
    CHANNEL_NAME = "discord"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._bot_token = self.config.get("bot_token", "")
        self._guild_ids: set = set()
        guilds = self.config.get("guild_ids", "")
        if guilds:
            self._guild_ids = {g.strip() for g in guilds.split(",") if g.strip()}
        self._allowed_channels: set = set()
        channels = self.config.get("allowed_channels", "")
        if channels:
            self._allowed_channels = {c.strip() for c in channels.split(",") if c.strip()}
        self._client = None
        self._loop = None
        self._bot_thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()

    def _create_connection(self):
        if not self._bot_token:
            raise ValueError("bot_token is required for Discord")

        try:
            import discord
        except ImportError:
            raise ImportError(
                "discord.py is required for Discord bot. "
                "Install with: pip install discord.py"
            )

        self._start_bot()
        # Wait for bot to be ready
        if not self._ready_event.wait(timeout=30):
            raise RuntimeError("Discord bot failed to connect within 30s")

        logger.info("Discord bot connected")
        return {"status": "connected"}

    def _start_bot(self):
        """Start Discord bot in a daemon thread."""
        import asyncio

        def run_bot():
            import discord

            intents = discord.Intents.default()
            intents.message_content = True
            client = discord.Client(intents=intents)
            self._client = client

            @client.event
            async def on_ready():
                logger.info(f"Discord bot ready: {client.user}")
                self._ready_event.set()

            @client.event
            async def on_message(message):
                # Skip bot's own messages
                if message.author == client.user:
                    return

                # Guild filter
                if self._guild_ids and message.guild:
                    if str(message.guild.id) not in self._guild_ids:
                        return

                # Channel filter
                if self._allowed_channels:
                    if str(message.channel.id) not in self._allowed_channels:
                        return

                update = {
                    "channel_id": str(message.channel.id),
                    "user_id": str(message.author.id),
                    "username": str(message.author),
                    "guild_id": str(message.guild.id) if message.guild else "",
                    "message_id": str(message.id),
                    "content": message.content,
                    "message_type": "text",
                }
                self._dispatch(update)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop.run_until_complete(client.start(self._bot_token))

        self._bot_thread = threading.Thread(target=run_bot, daemon=True, name="discord-bot")
        self._bot_thread.start()

    def _poll_loop(self):
        """Not used — Discord uses websocket via discord.py client events."""
        # The bot thread handles receiving via on_message
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=1)

    def send_message(self, channel_id: str, text: str, **kwargs) -> dict:
        """Send a message to a Discord channel."""
        import asyncio

        if not self._client or not self._loop:
            raise RuntimeError("Discord bot not connected")

        async def _send():
            channel = self._client.get_channel(int(channel_id))
            if not channel:
                channel = await self._client.fetch_channel(int(channel_id))
            msg = await channel.send(text)
            return {"message_id": str(msg.id), "channel_id": channel_id}

        future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
        return future.result(timeout=15)

    def send_file(self, channel_id: str, file_bytes: bytes, filename: str,
                  caption: str = "") -> dict:
        """Send a file to a Discord channel."""
        import asyncio

        if not self._client or not self._loop:
            raise RuntimeError("Discord bot not connected")

        async def _send():
            import discord
            import io
            channel = self._client.get_channel(int(channel_id))
            if not channel:
                channel = await self._client.fetch_channel(int(channel_id))
            file = discord.File(io.BytesIO(file_bytes), filename=filename)
            msg = await channel.send(content=caption or None, file=file)
            return {"message_id": str(msg.id)}

        future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
        return future.result(timeout=30)

    def _close_connection(self):
        """Stop the Discord bot."""
        super()._close_connection()
        if self._client and self._loop:
            import asyncio
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._client.close(), self._loop,
                )
                future.result(timeout=5)
            except Exception:
                pass
        self._client = None
        self._loop = None
        self._ready_event.clear()

    def ensure_connected(self):
        if not self._initialized:
            self.connect()


ServiceFactory.register(DiscordBotService)
