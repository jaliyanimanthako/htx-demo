"""
One full-frame exit/obstruction detection call to a Qwen (or other) VLM.
"""

import time

from PIL import Image

from core import depth_check
from core.parsing import parse_response
from core.prompts import USER_TEXT
from engines.qwen_engine import QwenEngine

def query_frame(engine: QwenEngine, image: Image.Image, system_prompt: str,
                 width: int, height: int, frame=None) -> dict:
    """frame: full-resolution BGR frame for depth-verification crops (optional)."""
    t1 = time.time()
    try:
        raw        = engine.query_image(image, system_prompt, user_text=USER_TEXT)
        detections = parse_response(raw, width, height)
        if depth_check.VERIFY_DEPTH and frame is not None:
            detections = depth_check.verify_depth(engine, detections, frame)
        error      = None
    except Exception as e:
        detections = []
        error      = str(e)
    return {"detections": detections, "error": error, "latency": time.time() - t1}
