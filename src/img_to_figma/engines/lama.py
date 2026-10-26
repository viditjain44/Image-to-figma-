"""LaMa inpainting (big-lama TorchScript). Fills masked areas so the background looks untouched.

Falls back to OpenCV's Telea inpainting if the model can't be downloaded or run.
"""
import logging
import urllib.request
from functools import lru_cache

import cv2
import numpy as np

from .. import config

log = logging.getLogger(__name__)
MAX_SIDE = 1024  # LaMa works best near its training size; bigger crops are scaled down then pasted back


def download() -> None:
    if config.LAMA_PATH.exists():
        return
    config.LAMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.LAMA_PATH.with_suffix(".part")
    log.info("Downloading LaMa (~200 MB) to %s", config.LAMA_PATH)
    urllib.request.urlretrieve(config.LAMA_URL, tmp)
    tmp.rename(config.LAMA_PATH)


@lru_cache(maxsize=1)
def _device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


@lru_cache(maxsize=1)
def _model():
    import torch

    download()
    model = torch.jit.load(str(config.LAMA_PATH), map_location="cpu")
    model.eval()
    return model.to(_device())


def _run_lama(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    import torch

    h, w = img.shape[:2]
    ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
    img_p = np.pad(img, ((0, ph), (0, pw), (0, 0)), mode="reflect")
    mask_p = np.pad(mask, ((0, ph), (0, pw)), mode="reflect")
    dev = _device()
    t_img = (torch.from_numpy(img_p).permute(2, 0, 1)[None].float() / 255).to(dev)
    t_mask = (torch.from_numpy(mask_p)[None, None].float() > 127).float().to(dev)
    with torch.no_grad():  # not inference_mode: the TorchScript model fails with it on MPS
        out = _model()(t_img, t_mask)[0].permute(1, 2, 0).clamp(0, 1).cpu().numpy()
    return (out[:h, :w] * 255).round().astype(np.uint8)


def _crops(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boxes around each masked area with enough context for LaMa; overlapping boxes merged."""
    H, W = mask.shape
    n, _, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8))
    boxes = []
    for i in range(1, n):
        x, y, w, h = stats[i, :4]
        pad = max(48, int(max(w, h) * 0.6))
        boxes.append([max(0, x - pad), max(0, y - pad), min(W, x + w + pad), min(H, y + h + pad)])
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    boxes.pop(j)
                    merged = True
                    break
            if merged:
                break
    return [tuple(b) for b in boxes]


TEXTURE_LIMIT = 5.0  # mean |Laplacian| around a region; below = smooth (flat colour / gradient)


def _classify(img: np.ndarray, mask: np.ndarray) -> tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray]:
    """Split the masked regions by what surrounds them:
    flat colour (UIs, flat ads) -> filled with that exact colour, nothing to get wrong;
    textured (photo, hair, fabric) -> LaMa; smooth gradients -> Telea (LaMa shifts colour there)."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
    near = cv2.dilate(mask, np.ones((15, 15), np.uint8))  # join the letters of one line
    n, labels = cv2.connectedComponents((near > 0).astype(np.uint8))
    flat, textured = [], np.zeros_like(mask)
    for i in range(1, n):
        comp = (labels == i).astype(np.uint8)
        region = (comp > 0) & (mask > 0)
        ring = (cv2.dilate(comp, np.ones((11, 11), np.uint8)) > 0) & (comp == 0) & (mask == 0)
        if not ring.any():
            continue
        px = img[ring].astype(np.float32)
        colour = np.median(px, axis=0)
        if np.abs(px - colour).mean() < 3:
            flat.append((region, colour.astype(np.uint8)))
        elif float(np.median(lap[ring])) > TEXTURE_LIMIT or _thick(region, img.shape):
            # textured surroundings, or a big hole: Telea is only good for thin strokes (big holes
            # come out as hard-edged polygons), so those go to LaMa
            textured[region] = 255
    return flat, textured


def _thick(region: np.ndarray, shape) -> bool:
    """True for a hole wider than a text stroke (more than ~1.5% of the image's short side)."""
    ys, xs = np.nonzero(region)
    if len(ys) < 50:
        return False
    y0, x0 = ys.min(), xs.min()
    sub = np.zeros((ys.max() - y0 + 3, xs.max() - x0 + 3), np.uint8)
    sub[ys - y0 + 1, xs - x0 + 1] = 1
    return float(cv2.distanceTransform(sub, cv2.DIST_L2, 3).max()) > max(6.0, 0.015 * min(shape[:2]))


def inpaint(img: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, str]:
    """img: RGB uint8, mask: uint8 (255 = fill). Returns (result, engines used)."""
    if not mask.any():
        return img.copy(), "none"
    out = cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)
    flat, textured = _classify(img, mask)
    for region, colour in flat:
        out[region] = colour
    engine = "opencv"
    if textured.any():
        lama_out, lama_engine = _lama(img, textured)
        out[textured > 0] = lama_out[textured > 0]
        engine += "+" + lama_engine
    return out, engine


def _lama(img: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, str]:
    """Run LaMa on crops around the masked areas (faster, and closer to its training size)."""
    out = img.copy()
    try:
        for x0, y0, x1, y1 in _crops(mask):
            crop, crop_mask = img[y0:y1, x0:x1], mask[y0:y1, x0:x1]
            h, w = crop.shape[:2]
            scale = min(1.0, MAX_SIDE / max(h, w))
            if scale < 1:
                small = cv2.resize(crop, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
                small_mask = cv2.resize(crop_mask, small.shape[1::-1], interpolation=cv2.INTER_NEAREST)
                filled = cv2.resize(_run_lama(small, small_mask), (w, h), interpolation=cv2.INTER_CUBIC)
            else:
                filled = _run_lama(crop, crop_mask)
            region = out[y0:y1, x0:x1]
            region[crop_mask > 127] = filled[crop_mask > 127]  # only touch masked pixels
        return out, "lama"
    except Exception as e:
        log.warning("LaMa failed, using OpenCV inpainting: %s", e)
        return cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA), "opencv"
