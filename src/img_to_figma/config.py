"""All settings in one place. Override any of them with environment variables (I2F_<NAME>)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default):
    value = os.environ.get(f"I2F_{name}")
    if value is None:
        return default
    if isinstance(default, bool):
        return value.lower() in ("1", "true", "yes")
    return type(default)(value)


# Models
LLM_MODEL = _env("LLM_MODEL", "qwen3-vl:8b-instruct")   # Ollama; labels text roles + font kind
USE_LLM = _env("USE_LLM", True)                          # False = heuristic roles only
LAMA_URL = _env("LAMA_URL", "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt")
LAMA_PATH = Path(_env("LAMA_PATH", str(ROOT / "models" / "big-lama.pt")))

# Folders
OUTPUT_DIR = Path(_env("OUTPUT_DIR", str(ROOT / "outputs")))

# Default Figma fonts per kind of type (the UI can override them per job)
SANS_FONT = _env("SANS_FONT", "Inter")
SERIF_FONT = _env("SERIF_FONT", "Playfair Display")
SCRIPT_FONT = _env("SCRIPT_FONT", "Dancing Script")

# Detection
MIN_OCR_CONFIDENCE = _env("MIN_OCR_CONFIDENCE", 0.3)
# Text smaller than this share of the image's short side stays baked into the
# background (tiny print on packaging, photos of screens…).
MIN_TEXT_HEIGHT = _env("MIN_TEXT_HEIGHT", 0.011)
COLOR_TOLERANCE = _env("COLOR_TOLERANCE", 14)        # flood fill tolerance for button/pill shapes
# Interfaces (flat colour, or many separate text blocks) skip the "main subject" cut-out
UI_FLAT_SHARE = _env("UI_FLAT_SHARE", 0.5)
UI_MIN_TEXT_BLOCKS = _env("UI_MIN_TEXT_BLOCKS", 15)
# Photos inside a UI (product shots…): regions of one exact colour larger than this share of the
# image are interface surfaces; a container where at least this share of pixels match none of
# their neighbours is a photo, and text inside it stays in the picture.
UI_SURFACE_MIN = _env("UI_SURFACE_MIN", 0.005)
UI_PHOTO_SHARE = _env("UI_PHOTO_SHARE", 0.15)
MAX_IMAGE_SIDE = _env("MAX_IMAGE_SIDE", 4096)        # Figma's image limit

# Server
API_HOST = _env("API_HOST", "127.0.0.1")
API_PORT = _env("API_PORT", 8001)
