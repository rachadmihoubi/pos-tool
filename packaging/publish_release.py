# packaging/publish_release.py
"""
publish_release.py - cuts a new release: bumps VERSION, builds the
installer, computes its checksum, and publishes both to GitHub Releases.

Run by hand on this dev PC when rachad wants to ship an update. Not part
of the shipped app - store PCs never see this file. Requires pyinstaller
(.venv), Inno Setup's `iscc` (found on PATH if present, else this dev PC's
known install location), and the `gh` CLI already logged in. Commits and
pushes the VERSION/setup.iss bump before publishing - `gh release create`
tags whatever the remote's default branch HEAD is, so without the push the
tag would land on the pre-bump commit.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
SETUP_ISS = PROJECT_ROOT / "packaging" / "setup.iss"
INSTALLER_PATH = PROJECT_ROOT / "dist-installer" / "Setup.exe"
CHECKSUM_PATH = PROJECT_ROOT / "dist-installer" / "Setup.exe.sha256"
REPO = "rachadmihoubi/pos-tool"
# This script now legitimately runs on more than one machine - the dev PC
# and, per CLAUDE.md's "till PC doubles as a dev machine" note, store #1's
# own till PC too - so a single hardcoded username fallback breaks on
# every machine but the one it was written on. Try every current user's
# own install location plus the common machine-wide one before giving up.
_ISCC_CANDIDATES = [
    Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def _find_iscc() -> str:
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for candidate in _ISCC_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    raise SystemExit(
        "Could not find ISCC.exe (Inno Setup) on PATH or in any known "
        f"install location: {[str(c) for c in _ISCC_CANDIDATES]}"
    )


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
    _run([_find_iscc(), "packaging/setup.iss"])


def _write_checksum() -> str:
    digest = hashlib.sha256(INSTALLER_PATH.read_bytes()).hexdigest()
    CHECKSUM_PATH.write_text(f"{digest}  Setup.exe\n", encoding="utf-8")
    return digest


def _commit_version_bump(version_text: str) -> None:
    _run(["git", "add", str(VERSION_FILE), str(SETUP_ISS)])
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=PROJECT_ROOT)
    if staged.returncode != 0:
        _run(["git", "commit", "-m", f"chore(release): bump version to {version_text}"])
    else:
        print("Version bump already committed - nothing to commit.")
    _run(["git", "push"])


def _release_asset_names(tag: str) -> set[str]:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", REPO,
         "--json", "assets", "--jq", ".assets[].name"],
        cwd=PROJECT_ROOT, check=True, capture_output=True, text=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def _publish(version_text: str) -> None:
    tag = f"v{version_text}"
    expected = {INSTALLER_PATH.name, CHECKSUM_PATH.name}
    _run(["gh", "release", "create", tag,
          str(INSTALLER_PATH), str(CHECKSUM_PATH),
          "--repo", REPO, "--generate-notes"])

    # gh release create uploads assets sequentially after creating the
    # release object - a transient failure partway through (this store's
    # connection has a documented history of exactly this) leaves a real,
    # published release with a missing asset, exit code notwithstanding.
    # This bit both the app teams's v1.0.10 AND v1.0.11 releases: Setup.exe
    # landed, Setup.exe.sha256 silently did not, and poslib/updater.py
    # correctly refused to apply the unverifiable update - but nobody
    # noticed until an actual store install tried to update days later.
    # Verify before declaring success instead of trusting the exit code.
    missing = expected - _release_asset_names(tag)
    if missing:
        print(f"Retrying missing asset(s) after publish: {sorted(missing)}")
        paths = {INSTALLER_PATH.name: INSTALLER_PATH, CHECKSUM_PATH.name: CHECKSUM_PATH}
        for name in missing:
            _run(["gh", "release", "upload", tag, str(paths[name]), "--repo", REPO])
        still_missing = expected - _release_asset_names(tag)
        if still_missing:
            raise SystemExit(
                f"Release {tag} is still missing asset(s) after retry: "
                f"{sorted(still_missing)} - fix manually before trusting this release."
            )


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

    _commit_version_bump(version_text)
    _publish(version_text)
    print(f"Published v{version_text} to {REPO}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
