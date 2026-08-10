"""
whatsapp_channel.py - sends the digest through the official WhatsApp
Business Cloud API from Meta.

READ THIS BEFORE TURNING IT ON
==============================
This is the only sanctioned way to send WhatsApp messages automatically,
and it is not free or instant. What it needs:

  - a Meta Business account, with your business verified
  - a phone number dedicated to the API. Once a number is connected it can
    no longer be used in the normal WhatsApp or WhatsApp Business app
  - a message TEMPLATE approved by Meta. The daily digest is a message you
    start, not a reply, so outside the 24-hour window that follows a message
    from the customer, only approved templates may be sent. Approval takes
    anywhere from a few minutes to a few days, and templates get rejected
    for wording Meta does not like
  - payment. Meta bills per conversation, and the free allowance is small

Because a template has fixed wording with a limited number of placeholders,
the WhatsApp digest is a SHORT SUMMARY - the headline numbers and the single
most urgent finding. The full digest goes to the file and email channels.

WHAT THIS FILE DELIBERATELY DOES NOT DO
---------------------------------------
There are Python libraries that automate WhatsApp Web by driving a browser.
They are against WhatsApp's terms of service and using one is a good way to
get the shop's number banned permanently. None is used here, and none
should be added.

If you want a free messaging channel today, use the Telegram one instead.
It does the same job, sets up in five minutes and costs nothing. Both can
be switched on at once.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

from .base import Channel, DeliveryResult

if TYPE_CHECKING:
    from ..digest import Digest

log = logging.getLogger(__name__)

API = "https://graph.facebook.com/{version}/{phone_number_id}/messages"


class WhatsAppChannel(Channel):
    """Sends a short digest through Meta's official Cloud API."""

    name = "whatsapp"

    def is_ready(self) -> tuple[bool, str]:
        if not self.cfg.has_secret("WHATSAPP_ACCESS_TOKEN"):
            return False, "no WHATSAPP_ACCESS_TOKEN in the .env file"
        if not self.setting("phone_number_id"):
            return False, ("digest.channels.whatsapp.phone_number_id is empty "
                           "in config.yaml")
        if not self.setting("to_numbers"):
            return False, ("digest.channels.whatsapp.to_numbers is empty "
                           "in config.yaml")
        if not self.setting("template_name"):
            return False, ("digest.channels.whatsapp.template_name is empty - "
                           "Meta must approve a template before anything can "
                           "be sent")
        return True, ""

    def send(self, digest: "Digest", language: str) -> DeliveryResult:
        token = self.cfg.secret("WHATSAPP_ACCESS_TOKEN")
        version = str(self.setting("api_version", "v21.0"))
        phone_number_id = str(self.setting("phone_number_id"))
        recipients = [str(n) for n in (self.setting("to_numbers") or [])]
        template = str(self.setting("template_name", "daily_digest"))
        template_lang = str(self.setting("template_language", language))

        url = API.format(version=version, phone_number_id=phone_number_id)
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}

        # The values that fill the {{1}}, {{2}} ... slots in the approved
        # template, in order. Keep this in step with whatever wording Meta
        # approved for you.
        variables = digest.template_variables(language)

        sent, failures = 0, []
        for number in recipients:
            payload: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "to": number,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": template_lang},
                    "components": [{
                        "type": "body",
                        "parameters": [{"type": "text", "text": v} for v in variables],
                    }],
                },
            }

            response = requests.post(url, headers=headers, json=payload, timeout=45)
            if response.ok:
                sent += 1
            else:
                failures.append(f"{number}: {self._explain(response)}")

        if sent and not failures:
            return DeliveryResult(self.name, ok=True, detail=f"to {sent} number(s)")
        if sent:
            return DeliveryResult(self.name, ok=True,
                                  detail=f"to {sent} number(s); failed: {'; '.join(failures)}")
        return DeliveryResult(self.name, ok=False, error="; ".join(failures))

    @staticmethod
    def _explain(response: requests.Response) -> str:
        """Translate Meta's error codes into something actionable."""
        try:
            error = response.json().get("error", {})
            message = error.get("message", "")
            code = error.get("code", "")
            subcode = error.get("error_subcode", "")
        except ValueError:
            return f"HTTP {response.status_code}: {response.text[:200]}"

        if code == 190:
            return "the access token has expired or been revoked - generate a new one"
        if code == 132001:
            return (f"the template '{message}' does not exist or is not approved "
                    "in this language")
        if code == 131030:
            return ("the recipient is not on your allowed list - while the number "
                    "is unverified, only test numbers you have added can receive "
                    "messages")
        if code == 131047:
            return ("outside the 24-hour window, so only an approved template "
                    "may be sent")
        if code == 100:
            return f"the request was rejected: {message}"
        return f"HTTP {response.status_code}, code {code}/{subcode}: {message}"
