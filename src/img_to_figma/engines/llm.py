"""Local vision LLM (Qwen3-VL via Ollama)."""
import logging
from pathlib import Path

import ollama

from .. import config

log = logging.getLogger(__name__)


def ask_text(prompt: str, *, images: list[Path] | None = None, max_tokens: int = 400) -> str:
    """Plain-text answer. Much shorter than JSON, so several times faster on a laptop."""
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = [str(p) for p in images]
    r = ollama.chat(model=config.LLM_MODEL, messages=[msg],
                    options={"temperature": 0.1, "num_ctx": 8192, "num_predict": max_tokens},
                    think=False, keep_alive="5m")
    return r.message.content


def unload() -> None:
    """Free the LLM's memory right away (so LaMa / rembg have room on a 16 GB Mac)."""
    try:
        ollama.generate(model=config.LLM_MODEL, prompt="", keep_alive=0)
    except Exception as e:
        log.debug("unload skipped: %s", e)
