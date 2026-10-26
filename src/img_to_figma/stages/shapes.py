"""Find solid-colour rounded rectangles behind text: buttons, pills, badges, bars.

Flood-fills outwards from just around each text block; if the filled region is a
tidy rectangle a bit bigger than the text, it becomes its own Figma shape.
"""
import cv2
import numpy as np

from .. import config
from ..schemas import Box, Shape, TextBlock
from .text_style import hex_color


def _corner_radius(region: np.ndarray, box: Box) -> int:
    """Walk diagonally in from each corner until the fill starts: d = r(1 - 1/√2)."""
    sub = region[box.y:box.y2, box.x:box.x2]
    h, w = sub.shape
    steps = []
    for dy, dx, y0, x0 in ((1, 1, 0, 0), (1, -1, 0, w - 1), (-1, 1, h - 1, 0), (-1, -1, h - 1, w - 1)):
        d = 0
        while d < min(h, w) // 2 and not sub[y0 + dy * d, x0 + dx * d]:
            d += 1
        steps.append(d)
    r = float(np.median(steps)) / (1 - 2 ** -0.5)
    return int(min(r, h / 2))


def find(img: np.ndarray, blocks: list[TextBlock]) -> list[Shape]:
    H, W = img.shape[:2]
    tol = (config.COLOR_TOLERANCE,) * 3
    shapes: list[Shape] = []
    for block in blocks:
        if block.keep_in_background or any(block.id in s.block_ids for s in shapes):
            continue
        b, m = block.box, max(4, int(block.font_size * 0.3))
        seeds = [(b.x - m, int(b.cy)), (b.x2 + m, int(b.cy)), (int(b.cx), b.y - m), (int(b.cx), b.y2 + m)]
        seeds = [(x, y) for x, y in seeds if 0 <= x < W and 0 <= y < H]
        if len(seeds) < 4:
            continue
        colors = np.array([img[y, x] for x, y in seeds], np.float32)
        if np.abs(colors - np.median(colors, axis=0)).max() > config.COLOR_TOLERANCE * 2:
            continue  # not one solid colour all around the text

        mask = np.zeros((H + 2, W + 2), np.uint8)
        flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
        cv2.floodFill(img.copy(), mask, seeds[0], (0, 0, 0), tol, tol, flags)
        region = mask[1:-1, 1:-1] > 0
        contours, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        c = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(c)
        box = Box(x=x, y=y, w=w, h=h)
        filled = np.zeros((H, W), np.uint8)
        cv2.drawContours(filled, [c], -1, 255, -1)

        if (x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1   # touches the edge: it is the background
                or w * h > 0.35 * W * H
                or not box.contains(b, slack=2)
                or w > b.w + 8 * block.font_size or h > b.h * 3.2      # button-like padding, not a panel/card
                or cv2.contourArea(c) / (w * h) < 0.8):
            continue
        inside = [o for o in blocks if not o.keep_in_background and box.contains(o.box, slack=2)]
        if len(inside) != 1:
            continue  # a card / container with several texts stays in the picture as it is
        fill_rgb = np.median(img[region], axis=0)
        if not _clean(img, filled > 0, box, fill_rgb, block):
            continue  # icons or other artwork inside: keep the shape in the picture, only the text moves
        shapes.append(Shape(box=box, fill=hex_color(fill_rgb), corner_radius=_corner_radius(filled > 0, box),
                            block_ids=[block.id]))
    return shapes


def _clean(img: np.ndarray, shape: np.ndarray, box: Box, fill: np.ndarray, block: TextBlock) -> bool:
    """True when the shape holds only its fill and the text (no icon, avatar, chevron…)."""
    inner = cv2.erode(shape.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0  # skip the anti-aliased rim
    pad = int(block.font_size * 0.2)
    for line in block.lines:
        inner[max(0, line.box.y - pad):line.box.y2 + pad, max(0, line.box.x - pad):line.box.x2 + pad] = False
    if not inner.any():
        return True
    other = np.linalg.norm(img[inner].astype(np.float32) - fill, axis=1) > config.COLOR_TOLERANCE * 3
    return float(other.mean()) < 0.01


def rounded_mask(shape: Shape, H: int, W: int, grow: int = 0) -> np.ndarray:
    b, r = shape.box, max(0, shape.corner_radius)
    x0, y0, x1, y1 = b.x - grow, b.y - grow, b.x2 - 1 + grow, b.y2 - 1 + grow
    r = min(r + grow, (x1 - x0) // 2, (y1 - y0) // 2)
    mask = np.zeros((H, W), np.uint8)
    cv2.rectangle(mask, (x0 + r, y0), (x1 - r, y1), 255, -1)
    cv2.rectangle(mask, (x0, y0 + r), (x1, y1 - r), 255, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    return mask


def plan_erase(img: np.ndarray, shapes: list[Shape]) -> tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray]:
    """How to remove each button from the picture.
    On a flat surround (most UIs) fill it with that colour: inpainting a big solid block smears.
    Returns ([(mask, colour)] flat fills, mask of the shapes that still need inpainting)."""
    H, W = img.shape[:2]
    fills, leftover = [], np.zeros((H, W), np.uint8)
    for s in shapes:
        mask = rounded_mask(s, H, W, grow=2)
        ring = (rounded_mask(s, H, W, grow=8) > 0) & (rounded_mask(s, H, W, grow=4) == 0)
        px = img[ring].astype(np.float32)
        if len(px) and np.abs(px - np.median(px, axis=0)).mean() < 6:
            fills.append((mask, np.median(px, axis=0).astype(np.uint8)))
        else:
            leftover |= mask
    return fills, leftover
