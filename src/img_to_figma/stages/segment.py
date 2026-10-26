"""Cut the main subject (product, person) out onto its own transparent layer with rembg."""
import logging
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image

from ..schemas import Box

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _session():
    from rembg import new_session

    return new_session("isnet-general-use")


@lru_cache(maxsize=1)
def _session_u2net():
    from rembg import new_session

    return new_session("u2net")


def _refine(img: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Second, close-up pass around the subject. The models shrink the whole picture to ~1024 px, so
    thin or white parts (pump heads, spray triggers, caps on a pale scene) get lost; on a crop they
    are seen at full size. Two models are combined because each misses different caps."""
    from rembg import remove

    H, W = alpha.shape
    ys, xs = np.nonzero(alpha > 127)
    if not len(xs):
        return alpha
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    m = int(0.15 * max(x1 - x0, y1 - y0))
    x0, y0, x1, y1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
    crop = Image.fromarray(img[y0:y1, x0:x1])
    try:
        a1 = np.array(remove(crop, session=_session(), only_mask=True, post_process_mask=True))
        a2 = np.array(remove(crop, session=_session_u2net(), only_mask=True, post_process_mask=True))
    except Exception as e:
        log.warning("Close-up cut-out failed, keeping the first pass: %s", e)
        return alpha
    close = np.maximum(a1, a2)
    # keep only what belongs to the subject found in the first pass (not new things in the margin)
    first = alpha[y0:y1, x0:x1] > 127
    n, labels = cv2.connectedComponents((close > 127).astype(np.uint8))
    keep = np.zeros(close.shape, bool)
    for i in range(1, n):
        comp = labels == i
        if (comp & first).sum() >= 0.2 * comp.sum():
            keep |= comp
    keep = cv2.dilate(keep.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    refined = np.zeros_like(alpha)
    refined[y0:y1, x0:x1] = np.where(keep, close, 0)
    before, after = (alpha > 127).sum(), (refined > 127).sum()
    if after < 0.6 * before or after > 1.6 * before:
        return alpha  # the close-up disagrees wildly: trust the first pass
    return refined


def cutout(img: np.ndarray) -> tuple[np.ndarray, Box] | None:
    """Returns (RGBA crop, its box) or None when there is no clear subject."""
    from rembg import remove

    H, W = img.shape[:2]
    alpha = np.array(remove(Image.fromarray(img), session=_session(), only_mask=True, post_process_mask=True))
    solid = (alpha > 127).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 0.01 * W * H]
    if not keep:
        return None
    alpha = np.where(np.isin(labels, keep), alpha, 0).astype(np.uint8)
    coverage = (alpha > 127).mean()
    if coverage < 0.02 or coverage > 0.75:  # nothing there, or the "subject" is the whole picture
        log.info("No clear subject (coverage %.2f)", coverage)
        return None
    ys, xs = np.where(alpha > 8)
    box = Box(x=int(xs.min()), y=int(ys.min()), w=int(xs.max() - xs.min() + 1), h=int(ys.max() - ys.min() + 1))
    # Filling most of the frame or running off 3+ edges, the "subject" is the backdrop photo (a head
    # of hair, a room, a landscape). Cutting it out would leave a huge invented area behind it.
    edges = (box.x <= 2) + (box.y <= 2) + (box.x2 >= W - 2) + (box.y2 >= H - 2)
    if coverage > 0.45 or edges >= 3:
        log.info("Subject is the backdrop (coverage %.2f, %d edges): not cut out", coverage, edges)
        return None
    alpha = _refine(img, alpha)
    ys, xs = np.where(alpha > 8)
    box = Box(x=int(xs.min()), y=int(ys.min()), w=int(xs.max() - xs.min() + 1), h=int(ys.max() - ys.min() + 1))
    rgba = np.dstack([img, alpha])[box.y:box.y2, box.x:box.x2]
    return rgba, box


def subject_mask(rgba: np.ndarray, box: Box, H: int, W: int, img: np.ndarray | None = None) -> np.ndarray:
    """What to remove from the background: the subject plus a margin (~3% of the image) so hair
    fringes, glow and contact shadows go too; otherwise they smear into the fill. With `img`, thin
    bright lines attached to it (the outline of a zoom circle, a ring around a badge) go too: left
    behind they read as a scratch across the background."""
    mask = np.zeros((H, W), np.uint8)
    mask[box.y:box.y2, box.x:box.x2] = np.where(rgba[..., 3] > 8, 255, 0)
    grow = max(9, int(0.03 * min(H, W))) | 1
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow)))
    if img is not None:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        lines = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, np.ones((9, 9), np.uint8)) > 60  # thin & bright
        n, labels, stats, _ = cv2.connectedComponentsWithStats(lines.astype(np.uint8), connectivity=8)
        touch = cv2.dilate(mask, np.ones((5, 5), np.uint8)) > 0
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            comp = labels == i
            # a line, not a blob: long and thin, and part of it lies against the removed area
            if max(w, h) >= 0.05 * min(H, W) and area < 0.25 * w * h and (comp & touch).any():
                mask[comp] = 255
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    return mask
