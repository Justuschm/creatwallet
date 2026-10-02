"""Minimal PNG helpers (no Pillow needed).

Used to read image dimensions for validation and to create placeholder
images for new pass projects.
"""

import math
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(data):
    """Return (width, height) of PNG data, or None if it isn't a PNG."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _chunk(kind, payload):
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def make_png(width, height, color=(0, 122, 255), accent=None):
    """Create an RGBA PNG filled with ``color``.

    If ``accent`` is given, a centered rounded block in that color is drawn,
    so placeholder images are recognisable on the pass.
    """
    bg = bytes((*color, 255))
    fg = bytes((*accent, 255)) if accent else bg
    left, right = round(width * 0.25), round(width * 0.75)
    top, bottom = round(height * 0.25), round(height * 0.75)
    radius = min(right - left, bottom - top) * 0.25
    rows = []
    for y in range(height):
        if accent and top <= y < bottom:
            # horizontal inset caused by the rounded corners
            dy = max(top + radius - (y + 0.5), (y + 0.5) - (bottom - radius), 0)
            inset = round(radius - math.sqrt(max(radius ** 2 - dy ** 2, 0))) if dy else 0
            a, b = left + inset, right - inset
            row = bg * a + fg * (b - a) + bg * (width - b)
        else:
            row = bg * width
        rows.append(b"\x00" + row)  # filter type: none
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _chunk(b"IEND", b""))
