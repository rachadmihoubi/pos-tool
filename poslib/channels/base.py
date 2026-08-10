"""
base.py - what every delivery channel has to look like.

A channel is one way of getting the daily digest to the owner: saved to a
file, emailed, sent on Telegram, sent on WhatsApp.

THE RULE THAT MATTERS
---------------------
A channel that fails must not stop the others. If the internet is down, the
email channel fails and the file channel still writes the digest to disk.
Every channel therefore returns a result saying what happened rather than
raising an error, and `Channel.deliver()` is the only entry point, which
catches everything.

TO ADD A FOURTH CHANNEL
-----------------------
Make one new file next to this one with a class that inherits Channel and
fills in `name` and `send()`. Register it in __init__.py. Nothing else in
the tool needs changing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config import Config

if TYPE_CHECKING:
    from ..digest import Digest

log = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    """What happened when we tried to send the digest one way."""
    channel: str
    ok: bool
    detail: str = ""
    error: str = ""
    attempts: int = 1
    files: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (f"{self.channel}: sent {self.detail}" if self.ok
                else f"{self.channel}: FAILED - {self.error}")


class Channel:
    """One way of delivering the digest."""

    #: Matches the key under digest.channels in config.yaml.
    name: str = "base"

    #: A channel that cannot be switched off, whatever the settings say.
    always_on: bool = False

    def __init__(self, cfg: Config):
        self.cfg = cfg

    # -- what a channel must provide ---------------------------------------

    def send(self, digest: "Digest", language: str) -> DeliveryResult:
        """Actually deliver it. Subclasses fill this in."""
        raise NotImplementedError

    # -- what a channel gets for free --------------------------------------

    def setting(self, key: str, default: Any = None) -> Any:
        return self.cfg.get(f"digest.channels.{self.name}.{key}", default)

    @property
    def enabled(self) -> bool:
        if self.always_on:
            return True
        return bool(self.setting("enabled", False))

    def is_ready(self) -> tuple[bool, str]:
        """
        Can this channel actually run? Returns (yes/no, why not).

        Checked before sending so a missing password gives a clear message
        in the log instead of a confusing error from a mail library.
        """
        return True, ""

    def deliver(self, digest: "Digest", language: str) -> DeliveryResult:
        """
        Send, retrying if the channel asks for it, and never raising.

        This is what the digest runner calls. Whatever goes wrong inside a
        channel stops here.
        """
        if not self.enabled:
            return DeliveryResult(self.name, ok=False,
                                  error="switched off in config.yaml")

        ready, why = self.is_ready()
        if not ready:
            return DeliveryResult(self.name, ok=False, error=why)

        attempts = max(1, int(self.setting("retry_attempts", 1)))
        delay = float(self.setting("retry_delay_seconds", 15))
        last_error = ""

        for attempt in range(1, attempts + 1):
            try:
                result = self.send(digest, language)
                result.attempts = attempt
                if result.ok:
                    return result
                last_error = result.error or "unknown problem"
            except Exception as exc:                       # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                log.exception("Channel %r failed on attempt %d", self.name, attempt)

            if attempt < attempts:
                log.warning("Channel %r failed (%s). Trying again in %.0fs.",
                            self.name, last_error, delay)
                time.sleep(delay)

        return DeliveryResult(self.name, ok=False, error=last_error, attempts=attempts)
