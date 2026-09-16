#!/usr/bin/env python3
"""Rasterise the Olondunge identity from its committed SVG sources.

Why headless Chrome and not CairoSVG: the glowing marks and the scenes are built from SVG filter
primitives (feGaussianBlur, feMorphology, feFlood, feComposite, feMerge, feTurbulence and
feDisplacementMap), and CairoSVG does not implement them (Kozea/CairoSVG issue 386, closed as
not planned). Rendering there would silently drop the glow, which is the whole point of the
artwork. Chrome renders the filters the way a browser
does, which is also how anyone will see the SVG.

The script is standard library only and makes no network call. Every output is read back from
its own PNG header and compared against the size and the background this file declares, so a
render that silently came out at the wrong size, or opaque where it should be transparent, fails
the build instead of shipping.

    python3 assets/build.py            # render and verify everything
    python3 assets/build.py --check    # verify what is already on disk, render nothing
    python3 assets/build.py --only social-preview.png
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
SRC = ASSETS / "src"
MB = 1024 * 1024

# Chrome and Chromium only. Another Chromium build may be named explicitly through
# OLONDUNGE_CHROME, which keeps the default set honest about what it shells out to.
CHROME_ENV = "OLONDUNGE_CHROME"
CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


@dataclass(frozen=True)
class Target:
    """One raster output and the contract it must satisfy."""

    out: str
    source: Path
    width: int
    height: int
    max_bytes: int = MB
    transparent: bool = True


def targets() -> list[Target]:
    return [
        Target("icon-32.png", ASSETS / "favicon.svg", 32, 32),
        Target("icon-128.png", ASSETS / "logo-mark-flat.svg", 128, 128),
        Target("icon-512.png", ASSETS / "logo-mark.svg", 512, 512),
        Target("logo-wordmark-dark.png", ASSETS / "logo-wordmark-dark.svg", 1060, 300),
        Target("logo-wordmark-light.png", ASSETS / "logo-wordmark-light.svg", 1060, 300),
        Target("banner.png", SRC / "banner.svg", 1600, 400, transparent=False),
        # GitHub: "at least 640 by 320 pixels (1280 by 640 pixels for best display)",
        # "PNG, JPG, or GIF", "under 1 MB in size".
        Target("social-preview.png", SRC / "social-preview.svg", 1280, 640, MB, False),
        Target("hero-allocation.png", SRC / "hero-allocation.svg", 1600, 900, transparent=False),
        Target(
            "hero-verification.png", SRC / "hero-verification.svg", 1600, 900, transparent=False
        ),
    ]


def find_chrome() -> str:
    named = os.environ.get(CHROME_ENV, "").strip()
    if named:
        found = shutil.which(named) or (named if Path(named).is_file() else None)
        if found is None:
            raise SystemExit(f"{CHROME_ENV} is set to {named}, which is not an executable file")
        return found
    for name in CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(
        "no Chrome or Chromium binary found on PATH; install one of "
        + ", ".join(CHROME_CANDIDATES)
        + f", or set {CHROME_ENV} to the binary to use"
    )


def png_dimensions(path: Path) -> tuple[int, int]:
    """Width and height from the PNG IHDR chunk, without any imaging dependency."""
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} is not a PNG")
    if raw[12:16] != b"IHDR":
        raise ValueError(f"{path.name} has no IHDR first chunk")
    width, height = struct.unpack(">II", raw[16:24])
    return int(width), int(height)


# PNG colour types: 2 is RGB, 6 is RGB with alpha. Chrome writes 6 for a transparent page
# background and 2 for an opaque one.
RGB, RGBA = 2, 6


def png_colour_type(path: Path) -> int:
    raw = path.read_bytes()
    return raw[25]


def corner_alpha(path: Path) -> int:
    """Alpha of the top left pixel of an 8 bit RGBA PNG.

    Every PNG row filter predicts the first pixel of the first row from zero, so its bytes sit
    unfiltered right after the row's filter byte and no filter decoding is needed.
    """
    raw = path.read_bytes()
    pos, data = 8, b""
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos : pos + 4])
        if raw[pos + 4 : pos + 8] == b"IDAT":
            data += raw[pos + 8 : pos + 8 + length]
        pos += 12 + length
    first = zlib.decompressobj().decompress(data, 5)
    return first[4]


def page(svg: Path, width: int, height: int) -> str:
    """A page sized to the target exactly, so the screenshot needs no cropping."""
    markup = svg.read_text(encoding="utf-8")
    return (
        "<!doctype html><meta charset='utf-8'><style>"
        "html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
        f"svg{{display:block;width:{width}px;height:{height}px}}"
        f"</style>{markup}"
    )


def render(chrome: str, target: Target) -> None:
    if not target.source.is_file():
        raise SystemExit(f"missing source {target.source}")
    with tempfile.TemporaryDirectory(prefix="olondunge-render-") as tmp:
        html = Path(tmp) / "page.html"
        html.write_text(page(target.source, target.width, target.height), encoding="utf-8")
        out = ASSETS / target.out
        argv = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--virtual-time-budget=4000",
            f"--user-data-dir={tmp}/profile",
            f"--default-background-color={'00000000' if target.transparent else '05050CFF'}",
            f"--window-size={target.width},{target.height}",
            f"--screenshot={out}",
            html.as_uri(),
        ]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        if not out.is_file():
            sys.stderr.write(done.stderr[-2000:] + "\n")
            raise SystemExit(f"chrome produced no file for {target.out}")


def check(target: Target) -> list[str]:
    out = ASSETS / target.out
    if not out.is_file():
        return [f"{target.out}: missing"]
    faults: list[str] = []
    width, height = png_dimensions(out)
    if (width, height) != (target.width, target.height):
        faults.append(
            f"{target.out}: rendered {width}x{height}, declared {target.width}x{target.height}"
        )
    colour_type = png_colour_type(out)
    if target.transparent and colour_type != RGBA:
        faults.append(f"{target.out}: colour type {colour_type}, declared transparent")
    elif target.transparent and corner_alpha(out) != 0:
        faults.append(f"{target.out}: top left pixel is not transparent")
    elif not target.transparent and colour_type != RGB:
        faults.append(f"{target.out}: colour type {colour_type}, declared opaque")
    size = out.stat().st_size
    if size > target.max_bytes:
        faults.append(f"{target.out}: {size} bytes, over the {target.max_bytes} byte limit")
    return faults


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify on disk, render nothing")
    parser.add_argument("--only", default=None, help="one output name, for example banner.png")
    args = parser.parse_args()

    wanted = [t for t in targets() if args.only in (None, t.out)]
    if not wanted:
        raise SystemExit(f"no target named {args.only}")

    chrome = "" if args.check else find_chrome()
    faults: list[str] = []
    for target in wanted:
        if not args.check:
            render(chrome, target)
        found = check(target)
        faults += found
        state = "FAIL" if found else "ok"
        size = (ASSETS / target.out).stat().st_size if (ASSETS / target.out).is_file() else 0
        print(f"{state:>4}  {target.out:<26} {target.width}x{target.height}  {size:>8} bytes")

    if faults:
        print("\nfailures:")
        for fault in faults:
            print(f"  {fault}")
        return 1
    print(f"\n{len(wanted)} target(s) match their declared size.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
