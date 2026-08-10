"""
channels - the ways the daily digest can be delivered.

To add a fourth way of sending the digest:
  1. copy one of the files here and change the class
  2. add its name to CHANNEL_CLASSES below
  3. add a section for it under digest.channels in config.yaml
Nothing else in the tool needs to change.
"""

from __future__ import annotations

from ..config import Config
from .base import Channel, DeliveryResult
from .email_channel import EmailChannel
from .file_channel import FileChannel
from .telegram_channel import TelegramChannel
from .whatsapp_channel import WhatsAppChannel

# The order matters: the file channel runs first so that the HTML and PDF
# exist before the email and Telegram channels try to attach them.
CHANNEL_CLASSES: list[type[Channel]] = [
    FileChannel,
    EmailChannel,
    TelegramChannel,
    WhatsAppChannel,
]


def build_channels(cfg: Config) -> list[Channel]:
    """Every channel, whether switched on or not."""
    return [cls(cfg) for cls in CHANNEL_CLASSES]


def active_channels(cfg: Config) -> list[Channel]:
    """Only the channels that are switched on."""
    return [c for c in build_channels(cfg) if c.enabled]


__all__ = ["Channel", "DeliveryResult", "FileChannel", "EmailChannel",
           "TelegramChannel", "WhatsAppChannel", "CHANNEL_CLASSES",
           "build_channels", "active_channels"]
