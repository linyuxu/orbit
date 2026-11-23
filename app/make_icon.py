"""Orbit's app icon: write icons/icon.png, icons/Orbit.icns and the title-bar logo web/vendor/logo.png.

With app/icons/source.png (the artwork, any size, on a transparent background) the artwork is cleaned (only its
large shapes are kept: the speckle a background remover leaves around them goes), trimmed, and centred on a
transparent 1024 square with Apple's icon margin: the icon is the artwork itself, no plate. `--plate` puts it on a
full-bleed white square instead (macOS 26+ rounds that; it may put bare artwork on a grey plate of its own).
The title-bar logo keeps the artwork alone. Without source.png a placeholder mark is drawn.

Run: uv run --with numpy python app/make_icon.py [--plate]   (uses macOS sips + iconutil)
"""

import shutil
import struct
import sys
from collections import deque
import subprocess
import tempfile
import zlib
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
N, SS = 1024, 4  # size, supersampling
ART = 620  # longer side of the artwork on the plate
ART_BARE = 824  # with no plate: Apple's grid leaves a 100px margin around an 824px icon body


def png(path: Path, rgba: np.ndarray) -> None:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def read_png(path: Path) -> np.ndarray:
    """Decode an 8-bit, non-interlaced RGB / RGBA PNG (what sips writes) to an RGBA array."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and ctype in (2, 6) and not interlace, "need an 8-bit RGB/RGBA PNG"
            ch = 4 if ctype == 6 else 3
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * ch
    out = np.zeros((h, stride), np.uint8)
    prev = np.zeros(stride, np.int32)
    for y in range(h):
        f, line = raw[y * (stride + 1)], np.frombuffer(raw, np.uint8, stride, y * (stride + 1) + 1).astype(np.int32)
        cur = np.zeros(stride, np.int32)
        if f == 0:
            cur = line
        elif f == 2:
            cur = (line + prev) & 255
        else:
            for x in range(stride):
                a = cur[x - ch] if x >= ch else 0
                b, c = prev[x], prev[x - ch] if x >= ch else 0
                if f == 1:
                    pr = a
                elif f == 3:
                    pr = (a + b) // 2
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                cur[x] = (line[x] + pr) & 255
        out[y], prev = cur, cur
    img = out.reshape(h, w, ch)
    if ch == 3:
        img = np.dstack([img, np.full((h, w), 255, np.uint8)])
    return img


def _big_shapes(solid: np.ndarray, keep: float = 0.02) -> np.ndarray:
    """The connected shapes of a boolean mask with at least `keep` of its area (8-connected, breadth first)."""
    h, w = solid.shape
    label = np.zeros((h, w), np.int32)
    sizes = [0]
    for y0, x0 in zip(*np.nonzero(solid)):
        if label[y0, x0]:
            continue
        n = len(sizes)
        label[y0, x0], q, size = n, deque([(y0, x0)]), 0
        while q:
            y, x = q.popleft()
            size += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and solid[yy, xx] and not label[yy, xx]:
                        label[yy, xx] = n
                        q.append((yy, xx))
        sizes.append(size)
    total = max(1, sum(sizes))
    big = [i for i, n in enumerate(sizes) if i and n >= keep * total]
    return np.isin(label, big)


def clean_art(src: Path) -> np.ndarray:
    """The artwork alone, trimmed. A background remover leaves speckle around it (bright red or yellow, some of it
    nearly opaque): only the large shapes are kept (found on a half-size mask of the solid pixels, grown by a few
    pixels so their soft edges stay), everything else becomes transparent."""
    art = read_png(src)
    h, w = art.shape[:2]
    keep = _big_shapes(art[::2, ::2, 3] >= 200)
    keep = np.repeat(np.repeat(keep, 2, 0), 2, 1)[:h, :w]
    for _ in range(3):  # grow by 3px: the anti-aliased edge
        g = keep.copy()
        g[1:] |= keep[:-1]
        g[:-1] |= keep[1:]
        g[:, 1:] |= keep[:, :-1]
        g[:, :-1] |= keep[:, 1:]
        keep = g
    art[~keep] = 0
    art[art[..., 3] <= 8] = 0
    ys, xs = np.where(art[..., 3] > 0)
    return np.ascontiguousarray(art[ys.min():ys.max() + 1, xs.min():xs.max() + 1])


def bare(src: Path) -> np.ndarray:
    """The artwork alone, centred on a transparent square with the icon grid's margin."""
    with tempfile.TemporaryDirectory() as t:
        crop_png, fit_png = Path(t) / "crop.png", Path(t) / "fit.png"
        png(crop_png, clean_art(src))
        subprocess.run(["sips", "-s", "format", "png", "-Z", str(ART_BARE), str(crop_png), "--out", str(fit_png)],
                       check=True, capture_output=True)
        fit = read_png(fit_png)
    out = np.zeros((N, N, 4), np.uint8)
    h, w = fit.shape[:2]
    y0, x0 = (N - h) // 2, (N - w) // 2
    out[y0:y0 + h, x0:x0 + w] = fit
    return out


def from_source(src: Path) -> np.ndarray:
    """The artwork centred on a full-bleed white square (macOS rounds the corners)."""
    with tempfile.TemporaryDirectory() as t:
        crop_png, fit_png = Path(t) / "crop.png", Path(t) / "fit.png"
        png(crop_png, clean_art(src))
        subprocess.run(["sips", "-s", "format", "png", "-Z", str(ART), str(crop_png), "--out", str(fit_png)],
                       check=True, capture_output=True)
        fit = read_png(fit_png).astype(np.float32)
    out = np.full((N, N, 4), 255, np.float32)
    h, w = fit.shape[:2]
    y0, x0 = (N - h) // 2, (N - w) // 2
    a = fit[..., 3:4] / 255
    out[y0:y0 + h, x0:x0 + w, :3] = out[y0:y0 + h, x0:x0 + w, :3] * (1 - a) + fit[..., :3] * a
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def draw() -> np.ndarray:
    m = N * SS
    y, x = np.mgrid[0:m, 0:m].astype(np.float32) / SS
    c = N / 2
    # macOS icon grid: a rounded square inset ~10%.
    inset, rad = N * 0.1, N * 0.18
    qx = np.maximum(np.abs(x - c) - (c - inset - rad), 0)
    qy = np.maximum(np.abs(y - c) - (c - inset - rad), 0)
    body = (np.hypot(qx, qy) <= rad).astype(np.float32)
    r = np.hypot(x - c, y - c)
    ring = (np.abs(r - N * 0.25) <= N * 0.028).astype(np.float32)
    core = (r <= N * 0.075).astype(np.float32)
    ang = np.deg2rad(-40)
    mx, my = c + N * 0.25 * np.cos(ang), c + N * 0.25 * np.sin(ang)
    moon = (np.hypot(x - mx, y - my) <= N * 0.062).astype(np.float32)
    halo = (np.hypot(x - mx, y - my) <= N * 0.095).astype(np.float32)
    bg = np.array([29, 29, 31], np.float32)           # --ink
    fg = np.array([249, 249, 248], np.float32)        # --bg
    accent = np.array([139, 151, 255], np.float32)    # dark --accent
    ink = ring * (1 - halo) + core
    img = bg[None, None] * (1 - ink[..., None]) + fg[None, None] * ink[..., None]
    img = img * (1 - moon[..., None]) + accent[None, None] * moon[..., None]
    alpha = body
    out = np.dstack([img, alpha * 255]).reshape(N, SS, N, SS, 4).mean(axis=(1, 3))
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def main() -> None:
    icons = HERE / "icons"
    icons.mkdir(exist_ok=True)
    source = icons / "source.png"
    plate = "--plate" in sys.argv[1:]
    big = (from_source(source) if plate else bare(source)) if source.exists() else draw()
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "icon_1024.png"
        png(src, big)
        subprocess.run(["sips", "-z", "512", "512", str(src), "--out", str(icons / "icon.png")], check=True,
                       capture_output=True)
        iconset = Path(t) / "Orbit.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = s * scale
                name = f"icon_{s}x{s}{'@2x' if scale == 2 else ''}.png"
                if px == 1024:
                    shutil.copy(src, iconset / name)
                else:
                    subprocess.run(["sips", "-z", str(px), str(px), str(src), "--out", str(iconset / name)],
                                   check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icons / "Orbit.icns")], check=True)
        if source.exists():  # the title-bar logo: the artwork alone, small
            logo = HERE.parent / "web" / "vendor" / "logo.png"
            png(Path(t) / "art.png", clean_art(source))
            subprocess.run(["sips", "-s", "format", "png", "-Z", "128", str(Path(t) / "art.png"), "--out", str(logo)],
                           check=True, capture_output=True)
            print("wrote", logo)
    print("wrote", icons / "icon.png", icons / "Orbit.icns", "(from source.png)" if source.exists() else "(placeholder)")


if __name__ == "__main__":
    main()
