"""
get_fonts.py - fetches the Cairo font so Arabic looks right.

Run once by setup.bat. Completely optional: if there is no internet, or the
download fails, the dashboard falls back to the Arabic font that comes with
Windows. Arabic still renders correctly either way - only the shape of the
letters differs.

Nothing else in the tool ever reaches the internet.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"

# Cairo, from Google Fonts. Licensed under the SIL Open Font License, which
# allows bundling it with a tool like this.
FONTS = {
    "Cairo-Regular.woff2":
        "https://fonts.gstatic.com/s/cairo/v28/"
        "SLXgc1nY6HkvangtZmpQdkhzfH5lkSs2SgRjCAGMQ1z0hOA-W1Q.woff2",
    "Cairo-SemiBold.woff2":
        "https://fonts.gstatic.com/s/cairo/v28/"
        "SLXgc1nY6HkvangtZmpQdkhzfH5lkSs2SgRjCAGMQ1z0hOA-elQ.woff2",
    "Cairo-Bold.woff2":
        "https://fonts.gstatic.com/s/cairo/v28/"
        "SLXgc1nY6HkvangtZmpQdkhzfH5lkSs2SgRjCAGMQ1z0hOA-QVQ.woff2",
}

TIMEOUT = 20


def main() -> int:
    FONT_DIR.mkdir(parents=True, exist_ok=True)

    got, missed = 0, 0
    for name, url in FONTS.items():
        target = FONT_DIR / name
        if target.is_file() and target.stat().st_size > 1000:
            got += 1
            continue
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (pos-tool font fetch)"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = response.read()
            if len(data) < 1000:
                raise ValueError("the downloaded file looks empty")
            target.write_bytes(data)
            got += 1
            print(f"        got {name}")
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            missed += 1
            print(f"        could not get {name}: {exc}")

    if missed:
        print(f"        {got} of {len(FONTS)} fonts. Windows' own Arabic font "
              "will be used for the rest.")
        # Not an error worth stopping setup for.
        return 0

    print(f"        Arabic font ready ({got} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
