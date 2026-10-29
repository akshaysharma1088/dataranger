"""Draws docs/assets/banner.svg: the DataRanger pixel wordmark above a patrol trail."""
import math
from pathlib import Path

GLYPHS = {
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "G": ["01111", "10000", "10000", "10111", "10001", "10001", "01111"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
}
BG, TRAIL = "#10221c", "#2c4a3e"
WORDS = [("DATA", "#5fb98a", "#1d4733"), ("RANGER", "#e8b53a", "#5e4510")]
CELL, X0, Y0, SHADOW = 12, 40, 34, 4

out, x = [], X0
for word, fill, shade in WORDS:
    for ch in word:
        for r, row in enumerate(GLYPHS[ch]):
            for c, on in enumerate(row):
                if on == "1":
                    px, py = x + c * CELL, Y0 + r * CELL
                    out.append(f'<rect x="{px + SHADOW}" y="{py + SHADOW}" width="{CELL}" height="{CELL}" fill="{shade}"/>')
                    out.append(f'<rect x="{px}" y="{py}" width="{CELL}" height="{CELL}" fill="{fill}"/>')
        x += 6 * CELL
    x += 3 * CELL
width = x - 3 * CELL - CELL + X0
height = 196

# The trail: one marker per daily partition, the same marks the patrol console uses.
marks = {3: "e", 9: "b", 14: "e", 16: "f", 17: "e", 19: "b", 21: "e", 22: "e", 24: "f", 25: "b", 27: "f", 28: "b",
         31: "f", 34: "f", 38: "e", 39: "e", 40: "f", 41: "f", 43: "b", 44: "b", 47: "f", 48: "f", 49: "f", 50: "f",
         51: "f", 52: "f", 53: "f", 54: "f", 55: "f", 56: "f", 57: "f"}
n = 58
for i in range(n):
    f = i / (n - 1)
    cx = X0 + f * (width - 2 * X0)
    cy = 162 + 9 * math.sin(f * math.pi * 3)
    k = marks.get(i)
    if k == "e":
        out.append(f'<rect x="{cx - 5:.1f}" y="{cy - 5:.1f}" width="10" height="10" fill="#e8b53a" transform="rotate(45 {cx:.1f} {cy:.1f})"/>')
    elif k == "f":
        out.append(f'<rect x="{cx - 4:.1f}" y="{cy - 4:.1f}" width="8" height="8" fill="#5fb98a"/>')
    elif k == "b":
        out.append(f'<rect x="{cx - 3.5:.1f}" y="{cy - 3.5:.1f}" width="7" height="7" fill="none" stroke="#5fb98a" stroke-width="2"/>')
    else:
        out.append(f'<rect x="{cx - 2:.1f}" y="{cy - 2:.1f}" width="4" height="4" fill="{TRAIL}"/>')

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
       f'role="img" aria-label="DataRanger"><rect width="{width}" height="{height}" rx="10" fill="{BG}"/>' + "".join(out) + "</svg>")
Path(__file__).with_name("banner.svg").write_text(svg)
print(width, height, len(out))
