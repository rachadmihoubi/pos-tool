"""
telegram_channel.py - sends the digest as a Telegram message.

This is the quick, free alternative to WhatsApp. Setting it up takes about
five minutes and costs nothing:

  1. In Telegram, search for @BotFather and send it   /newbot
     Give the bot a name. It replies with a token that looks like
     123456789:AAE...   Put that in .env as TELEGRAM_BOT_TOKEN.
  2. Send any message to your new bot, then visit
     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
     and look for  "chat":{"id":123456789}
     Put that number in .env as TELEGRAM_CHAT_ID.

There is no approval process, no business verification and no per-message
charge. Arabic, French and English all display correctly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from .base import Channel, DeliveryResult

if TYPE_CHECKING:
    from ..digest import Digest

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram rejects anything longer than this in one message.
MAX_MESSAGE = 4096


class TelegramChannel(Channel):
    """Sends the digest to a Telegram chat."""

    name = "telegram"

    def is_ready(self) -> tuple[bool, str]:
        if not self.cfg.has_secret("TELEGRAM_BOT_TOKEN"):
            return False, "no TELEGRAM_BOT_TOKEN in the .env file"
        if not self.cfg.has_secret("TELEGRAM_CHAT_ID"):
            return False, "no TELEGRAM_CHAT_ID in the .env file"
        return True, ""

    def send(self, digest: "Digest", language: str) -> DeliveryResult:
        token = self.cfg.secret("TELEGRAM_BOT_TOKEN")
        chat_id = self.cfg.secret("TELEGRAM_CHAT_ID")

        text = digest.text(language)
        if len(text) > MAX_MESSAGE:
            text = text[:MAX_MESSAGE - 40].rstrip() + "\n…"

        response = requests.post(
            API.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": text,
                  "disable_web_page_preview": True},
            timeout=45)

        if not response.ok:
            return DeliveryResult(self.name, ok=False,
                                  error=self._explain(response))

        sent = ["message"]

        # The PDF, if the file channel managed to produce one.
        if bool(self.setting("send_pdf", True)) and digest.pdf_path and \
                Path(digest.pdf_path).is_file():
            path = Path(digest.pdf_path)
            with open(path, "rb") as fh:
                doc = requests.post(
                    API.format(token=token, method="sendDocument"),
                    data={"chat_id": chat_id},
                    files={"document": (path.name, fh, "application/pdf")},
                    timeout=120)
            if doc.ok:
                sent.append("pdf")
            else:
                # The message went; not getting the PDF through is not a
                # failure worth retrying the whole send for.
                log.warning("Telegram accepted the message but not the PDF: %s",
                            self._explain(doc))

        return DeliveryResult(self.name, ok=True, detail=" + ".join(sent))

    @staticmethod
    def _explain(response: requests.Response) -> str:
        """Turn Telegram's reply into something worth reading in the log."""
        try:
            body = response.json()
            description = body.get("description", "")
        except ValueError:
            description = response.text[:200]

        if response.status_code == 401:
            return f"the bot token was rejected ({description})"
        if response.status_code == 400 and "chat not found" in description.lower():
            return ("chat not found - send your bot a message first, then read "
                    "the chat id from getUpdates")
        return f"HTTP {response.status_code}: {description}"
