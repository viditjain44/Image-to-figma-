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
    rgba = np.dstack([img, alpha])[box.y:box.y2, box.x:box.x2]
    return rgba, box


def subject_mask(rgba: np.ndarray, box: Box, H: int, W: int) -> np.ndarray:
    """What to remove from the background: the subject plus a margin (~3% of the image) so hair
    fringes, glow and contact shadows go too; otherwise they smear into the fill."""
    mask = np.zeros((H, W), np.uint8)
    mask[box.y:box.y2, box.x:box.x2] = np.where(rgba[..., 3] > 8, 255, 0)
    grow = max(9, int(0.03 * min(H, W))) | 1
    return cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow)))
