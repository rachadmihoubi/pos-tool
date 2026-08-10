"""
file_channel.py - saves the digest to a folder on this computer.

This is the channel that always works. No internet, no password, no third
party. Whatever else fails, the digest is on disk, dated, in the language
you chose.

It writes an HTML page always, and a PDF when it can. The PDF is produced
by asking Microsoft Edge, which is on every Windows 11 machine, to print
the page silently. That avoids installing anything, and it renders Arabic
correctly, which most Python PDF libraries do not.
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Channel, DeliveryResult

if TYPE_CHECKING:
    from ..digest import Digest

log = logging.getLogger(__name__)

# Where Edge and Chrome usually live on Windows.
BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str | None:
    """Find a browser that can print a page to PDF from the command line."""
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for path in BROWSER_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def html_to_pdf(html_path: Path, pdf_path: Path, timeout: int = 90) -> bool:
    """
    Turn the digest page into a PDF using the browser already on this machine.

    Returns True if it worked. A failure here is never fatal - the HTML has
    already been written and is perfectly readable on its own.
    """
    browser = find_browser()
    if not browser:
        log.info("No Edge or Chrome found, so no PDF was made. The HTML digest "
                 "was still saved.")
        return False

    # Chrome and Edge refuse to write into their own profile folder, so give
    # them a scratch one.
    profile = Path(tempfile.gettempdir()) / "pos-tool-print-profile"
    profile.mkdir(parents=True, exist_ok=True)

    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={pdf_path}",
        html_path.as_uri(),
    ]

    try:
        proc = subprocess.run(command, capture_output=True, timeout=timeout,
                              check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Could not produce the PDF: %s", exc)
        return False

    if pdf_path.is_file() and pdf_path.stat().st_size > 0:
        return True

    log.warning("The browser did not produce a PDF (exit code %s). %s",
                proc.returncode, (proc.stderr or b"").decode("utf-8", "replace")[:400])
    return False


class FileChannel(Channel):
    """Writes the digest into the digests folder."""

    name = "file"
    always_on = True          # this is the fallback; it cannot be disabled

    def send(self, digest: "Digest", language: str) -> DeliveryResult:
        folder = self.cfg.digest_dir
        folder.mkdir(parents=True, exist_ok=True)

        stamp = digest.for_date.strftime("%Y-%m-%d")
        base = folder / f"digest-{stamp}-{language}"

        html_path = base.with_suffix(".html")
        html_path.write_text(digest.html(language), encoding="utf-8")

        written = [str(html_path)]

        if bool(self.setting("write_pdf", True)):
            pdf_path = base.with_suffix(".pdf")
            if html_to_pdf(html_path, pdf_path):
                written.append(str(pdf_path))
                digest.pdf_path = pdf_path

        digest.html_path = html_path
        self._tidy_up(folder)

        return DeliveryResult(self.name, ok=True,
                              detail=f"{len(written)} file(s) in {folder}",
                              files=written)

    def _tidy_up(self, folder: Path) -> None:
        """Delete digests older than the number of days set in config.yaml."""
        keep_days = int(self.cfg.get("digest.keep_days", 400))
        if keep_days <= 0:
            return
        cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
        for path in folder.glob("digest-*"):
            try:
                if datetime.datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink()
            except OSError:
                # Being unable to tidy up is not worth failing over.
                pass
