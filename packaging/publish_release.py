# packaging/publish_release.py
"""
publish_release.py - cuts a new release: bumps VERSION, builds the
installer, computes its checksum, and publishes both to GitHub Releases.

Run by hand on this dev PC when rachad wants to ship an update. Not part
of the shipped app - store PCs never see this file. Requires pyinstaller
(.venv), Inno Setup's `iscc` on PATH, and the `gh` CLI already logged in.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
SETUP_ISS = PROJECT_ROOT / "packaging" / "setup.iss"
INSTALLER_PATH = PROJECT_ROOT / "dist-installer" / "Setup.exe"
CHECKSUM_PATH = PROJECT_ROOT / "dist-installer" / "Setup.exe.sha256"
REPO = "rachadmihoubi/pos-tool"


def _read_version() -> tuple[int, int, int]:
    text = VERSION_FILE.read_text(encoding="utf-8").strip()
    major, minor, patch = (int(p) for p in text.split("."))
    return (major, minor, patch)


def _bump_patch(version: tuple[int, int, int]) -> tuple[int, int, int]:
    major, minor, patch = version
    return (major, minor, patch + 1)


def _write_version(version: tuple[int, int, int]) -> str:
    text = "{}.{}.{}".format(*version)
    VERSION_FILE.write_text(text + "\n", encoding="utf-8")
    return text


def _update_setup_iss(version_text: str) -> None:
    content = SETUP_ISS.read_text(encoding="utf-8")
    updated = re.sub(r"AppVersion=\S+", f"AppVersion={version_text}", content)
    if updated == content:
        raise SystemExit("Could not find an AppVersion= line in setup.iss to update.")
    SETUP_ISS.write_text(updated, encoding="utf-8")


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def _build() -> None:
    _run([str(PROJECT_ROOT / ".venv" / "Scripts" / "pyinstaller.exe"),
          "packaging/pos-tool.spec", "--distpath", "dist", "--workpath", "build",
          "--noconfirm"])
    _run([r"C:\Users\RACHAD\AppData\Local\Programs\Inno Setup 6\ISCC.exe", "packaging/setup.iss"])


def _write_checksum() -> str:
    digest = hashlib.sha256(INSTALLER_PATH.read_bytes()).hexdigest()
    CHECKSUM_PATH.write_text(f"{digest}  Setup.exe\n", encoding="utf-8")
    return digest


def _publish(version_text: str) -> None:
    tag = f"v{version_text}"
    _run(["gh", "release", "create", tag,
          str(INSTALLER_PATH), str(CHECKSUM_PATH),
          "--repo", REPO, "--generate-notes"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version",
                        help="Explicit version, e.g. 1.2.0. Default: bump the patch number.")
    args = parser.parse_args()

    current = _read_version()
    if args.version:
        parts = args.version.split(".")
        if len(parts) != 3:
            parser.error("--version must be MAJOR.MINOR.PATCH, e.g. 1.2.0")
        new_version = (int(parts[0]), int(parts[1]), int(parts[2]))
    else:
        new_version = _bump_patch(current)

    version_text = _write_version(new_version)
    _update_setup_iss(version_text)
    print(f"Version: {'.'.join(map(str, current))} -> {version_text}")

    _build()
    digest = _write_checksum()
    print(f"Setup.exe sha256: {digest}")

    _publish(version_text)
    print(f"Published v{version_text} to {REPO}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
