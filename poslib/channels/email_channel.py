"""
email_channel.py - emails the digest, with the Excel report attached.

The password never appears in config.yaml. It is read from the .env file,
which is not shared and is excluded from version control.

For Gmail you cannot use your normal password. You need an "app password":
turn on two-step verification, then create an app password and paste that
into .env as SMTP_PASSWORD. The README has the steps.
"""

from __future__ import annotations

import logging
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Channel, DeliveryResult

if TYPE_CHECKING:
    from ..digest import Digest

log = logging.getLogger(__name__)


class EmailChannel(Channel):
    """Sends the digest by SMTP."""

    name = "email"

    # -- checks before we try ----------------------------------------------

    def is_ready(self) -> tuple[bool, str]:
        if not self.cfg.has_secret("SMTP_PASSWORD"):
            return False, ("no SMTP_PASSWORD in the .env file - see .env.example "
                           "for how to create one")
        if not self.setting("from_address"):
            return False, "digest.channels.email.from_address is empty in config.yaml"
        if not self.setting("to_addresses"):
            return False, "digest.channels.email.to_addresses is empty in config.yaml"
        if not self.setting("smtp_host"):
            return False, "digest.channels.email.smtp_host is empty in config.yaml"
        return True, ""

    # -- sending -----------------------------------------------------------

    def send(self, digest: "Digest", language: str) -> DeliveryResult:
        host = str(self.setting("smtp_host", "smtp.gmail.com"))
        port = int(self.setting("smtp_port", 587))
        use_tls = bool(self.setting("use_tls", True))
        sender = str(self.setting("from_address"))
        recipients = list(self.setting("to_addresses") or [])

        username = self.cfg.secret("SMTP_USERNAME") or sender
        password = self.cfg.secret("SMTP_PASSWORD")

        message = EmailMessage()
        message["Subject"] = digest.subject(language)
        message["From"] = formataddr((str(self.cfg.get("business.name", "")), sender))
        message["To"] = ", ".join(recipients)
        message["Date"] = formatdate(localtime=True)

        # A plain-text version for mail readers that will not show HTML, and
        # the full page for those that will.
        message.set_content(digest.text(language))
        message.add_alternative(digest.html(language), subtype="html")

        attached: list[str] = []
        if bool(self.setting("attach_excel", True)):
            path = digest.excel_path(language)
            if path and Path(path).is_file():
                self._attach(message, Path(path))
                attached.append(Path(path).name)

        if digest.pdf_path and Path(digest.pdf_path).is_file():
            self._attach(message, Path(digest.pdf_path))
            attached.append(Path(digest.pdf_path).name)

        context = ssl.create_default_context()
        timeout = 60

        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(message)

        detail = f"to {len(recipients)} recipient(s)"
        if attached:
            detail += f", attached {', '.join(attached)}"
        return DeliveryResult(self.name, ok=True, detail=detail)

    @staticmethod
    def _attach(message: EmailMessage, path: Path) -> None:
        guessed, _ = mimetypes.guess_type(path.name)
        main, _, sub = (guessed or "application/octet-stream").partition("/")
        message.add_attachment(path.read_bytes(), maintype=main,
                               subtype=sub or "octet-stream", filename=path.name)
