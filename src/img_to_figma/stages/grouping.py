"""Merge OCR lines into text blocks (a heading, a paragraph, a list item…) and work out alignment."""
import statistics

from .. import config
from ..schemas import StyledLine, TextBlock
from .text_style import color_distance


def _same_block(prev: StyledLine, line: StyledLine) -> bool:
    fs = min(prev.font_size, line.font_size)
    if fs <= 0 or not 0.78 <= line.font_size / prev.font_size <= 1.28:
        return False
    if color_distance(prev.color, line.color) > 70 and color_distance(prev.bg_color, line.bg_color) > 40:
        return False
    gap = line.box.y - prev.box.y2
    if gap < -0.3 * fs or gap > 0.9 * fs:
        return False
    tol = 0.6 * fs
    aligned = (abs(line.box.x - prev.box.x) < tol or abs(line.box.cx - prev.box.cx) < tol
               or abs(line.box.x2 - prev.box.x2) < tol)
    overlap = min(prev.box.x2, line.box.x2) - max(prev.box.x, line.box.x)
    return aligned and overlap > 0


def _align(block_lines: list[StyledLine], image_w: int) -> str:
    if len(block_lines) == 1:
        b = block_lines[0].box
        return "CENTER" if abs(b.cx - image_w / 2) < image_w * 0.03 and b.w < image_w * 0.9 else "LEFT"
    spread = {
        "LEFT": statistics.pstdev(l.box.x for l in block_lines),
        "CENTER": statistics.pstdev(l.box.cx for l in block_lines),
        "RIGHT": statistics.pstdev(l.box.x2 for l in block_lines),
    }
    return min(spread, key=lambda k: (round(spread[k] / 3), ["LEFT", "CENTER", "RIGHT"].index(k)))


def group(lines: list[StyledLine], image_w: int, image_h: int, is_ui: bool = False) -> list[TextBlock]:
    min_h = config.MIN_TEXT_HEIGHT * min(image_w, image_h)
    blocks: list[list[StyledLine]] = []
    for line in sorted(lines, key=lambda l: (l.box.y, l.box.x)):
        for b in reversed(blocks):  # newest first; columns side by side each keep their own block
            if _same_block(b[-1], line):
                b.append(line)
                break
        else:
            blocks.append([line])

    out = []
    for i, ls in enumerate(blocks):
        box = ls[0].box
        for l in ls[1:]:
            box = box.union(l.box)
        pitch = statistics.mean(b.box.cy - a.box.cy for a, b in zip(ls, ls[1:])) if len(ls) > 1 else 0
        block = TextBlock(id=i, lines=ls, box=box, align=_align(ls, image_w), line_height=round(pitch, 1))
        # Stays in the picture: tiny print, logos / multicolour artwork, icon glyphs read as text,
        # and uncertain readings in ads (wordmarks on packaging). On a UI, uncertain text is usually
        # a non-dictionary word (a username, a code) and stays editable unless it sits on a photo.
        text = block.text.strip()
        block.keep_in_background = (
            block.font_size * 0.93 < min_h
            or any(l.multicolor for l in ls)
            or not any(c.isalnum() for c in text)
            or (len(text) == 1 and not text.isdigit())
            or (max(l.confidence for l in ls) < 0.6 and (not is_ui or max(l.bg_noise for l in ls) >= 12))
        )
        out.append(block)
    return out
