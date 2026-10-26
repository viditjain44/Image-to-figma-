"""Find the photos inside a UI screenshot (product shots, thumbnails, hero images).

Text printed on a product or inside a photo belongs to that picture, not to the
interface, so it must stay baked in. Interfaces are drawn in exact flat colour while
resampled photos almost never repeat a pixel exactly, which separates the two:

1. surfaces = big regions of one exact colour (page, cards), plus near-page-colour
   pixels (soft shadows, hover tints);
2. everything else splits into containers; thin borders and glyph strokes are opened away;
3. a big container where many pixels match none of their neighbours is a photo tile,
   and its outline is filled so white labels and packaging inside it count too.
"""
import cv2
import numpy as np

from .. import config
from ..schemas import TextBlock


def tile_mask(img: np.ndarray) -> np.ndarray:
    """uint8 mask (1 = inside a photo tile)."""
    H, W = img.shape[:2]
    a = img.astype(np.int16)
    eq_r = np.zeros((H, W), bool)
    eq_d = np.zeros((H, W), bool)
    eq_r[:, :-1] = np.abs(a[:, 1:] - a[:, :-1]).sum(axis=2) == 0
    eq_d[:-1] = np.abs(a[1:] - a[:-1]).sum(axis=2) == 0
    eq_l = np.zeros((H, W), bool)
    eq_u = np.zeros((H, W), bool)
    eq_l[:, 1:] = eq_r[:, :-1]
    eq_u[1:] = eq_d[:-1]
    nonflat = ~(eq_r | eq_d | eq_l | eq_u)  # equals none of its 4 neighbours: photo content

    flat = (eq_r & eq_d).astype(np.uint8)  # boundaries between colours break the regions apart
    n, labels, stats, _ = cv2.connectedComponentsWithStats(flat, connectivity=4)
    big = np.zeros(n, bool)
    big[1:] = stats[1:, cv2.CC_STAT_AREA] >= config.UI_SURFACE_MIN * H * W
    surface = big[labels]
    if not surface.any():
        return np.zeros((H, W), np.uint8)
    colours, counts = np.unique(img[surface].reshape(-1, 3), axis=0, return_counts=True)
    page = colours[counts.argmax()].astype(np.int16)
    # soft shadows / tints over the page are smooth (pixels repeat); dark photo areas are noisy
    surface |= (np.abs(a - page).max(axis=2) <= 10) & ~nonflat

    rest = cv2.morphologyEx((~surface).astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    m, labels, stats, _ = cv2.connectedComponentsWithStats(rest, connectivity=4)
    side = min(H, W)
    out = np.zeros((H, W), np.uint8)
    for i in range(1, m):
        x, y, w, h, _ = stats[i]
        if w * h < (0.1 * side) ** 2 or min(w, h) < 0.05 * side:
            continue
        comp = labels == i
        if float(nonflat[comp].mean()) < config.UI_PHOTO_SHARE:
            continue  # a flat container (chip, panel), not a picture
        contours, _ = cv2.findContours(comp.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, contours, -1, 1, -1)  # fill holes: white labels inside the photo
    return out


def keep_photo_text(img: np.ndarray, blocks: list[TextBlock]) -> int:
    """Marks blocks sitting inside a photo as part of the picture. Returns how many."""
    mask = tile_mask(img)
    if not mask.any():
        return 0
    H, W = mask.shape
    count = 0
    for b in blocks:
        if b.keep_in_background:
            continue
        inside = sum(bool(mask[min(H - 1, int(l.box.cy)), min(W - 1, int(l.box.cx))]) for l in b.lines)
        if inside * 2 >= len(b.lines):
            b.keep_in_background = True
            count += 1
    return count
