"""
LocateAnything-3B HTTP server
=============================
Wraps NVIDIA's LocateAnythingWorker (from the model card) in a small FastAPI app
so clients (your computer or the server) can call it like the Qwen vLLM server.

Endpoints
  GET  /health                       → model, device, GPU name
  POST /detect  {image, categories, mode?, max_new_tokens?, temperature?}
  POST /ground  {image, phrase, multi?, mode?, ...}
     image: base64 JPEG/PNG (no data: prefix needed)
  Response: {"answer": raw model text,
             "boxes": [{"label", "x1","y1","x2","y2"}  (0-1000 normalised)],
             "latency_s": float}

Run via server/serve_locate.sh (handles GPU / port selection).
"""

import argparse
import base64
import io
import re
import threading
import time

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel
from transformers import AutoModel, AutoProcessor, AutoTokenizer

MODEL_ID = "nvidia/LocateAnything-3B"


# ── Worker (from the model card, verbose off) ─────────────────────────────────

class LocateAnythingWorker:
    def __init__(self, model_path: str, device: str = "cuda", dtype=torch.bfloat16):
        self.device, self.dtype = device, dtype
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        self.processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
        self.model = AutoModel.from_pretrained(
            model_path, torch_dtype=dtype, trust_remote_code=True,
        ).to(device).eval()

    @torch.no_grad()
    def predict(self, image: Image.Image, question: str, generation_mode: str = "hybrid",
                max_new_tokens: int = 2048, temperature: float = 0.7) -> str:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": question},
        ]}]
        text = self.processor.py_apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        images, videos = self.processor.process_vision_info(messages)
        inputs = self.processor(text=[text], images=images, videos=videos,
                                return_tensors="pt").to(self.device)
        response = self.model.generate(
            pixel_values=inputs["pixel_values"].to(self.dtype),
            input_ids=inputs["input_ids"],
            attention_mask=inputs["attention_mask"],
            image_grid_hws=inputs.get("image_grid_hws", None),
            tokenizer=self.tokenizer,
            max_new_tokens=max_new_tokens,
            use_cache=True,
            generation_mode=generation_mode,
            temperature=temperature,
            do_sample=True,
            top_p=0.9,
            repetition_penalty=1.1,
            verbose=False,
        )
        return response[0] if isinstance(response, tuple) else response


# ── Output parsing ────────────────────────────────────────────────────────────

# Boxes appear as <box><x1><y1><x2><y2></box> (parser in the model card) or as
# "<box> x1, y1, x2, y2 </box>" (format described in the card). The text before
# a run of boxes is the semantic label they belong to.
_BOX = re.compile(r"<box>\s*<?(\d+)>?[\s,]*<?(\d+)>?[\s,]*<?(\d+)>?[\s,]*<?(\d+)>?\s*</box>")
_TAG = re.compile(r"<[^>]*>")


def parse_boxes(answer: str) -> list:
    boxes, label, pos = [], "object", 0
    for m in _BOX.finditer(answer):
        between = _TAG.sub(" ", answer[pos:m.start()])
        between = re.sub(r"[\s,;:.]+", " ", between).strip()
        if between:
            label = between
        x1, y1, x2, y2 = (int(g) for g in m.groups())
        boxes.append({"label": label, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
        pos = m.end()
    return boxes


# ── API ───────────────────────────────────────────────────────────────────────

class DetectRequest(BaseModel):
    image: str
    categories: list[str]
    mode: str = "hybrid"          # fast | hybrid | slow
    max_new_tokens: int = 1024
    temperature: float = 0.7


class GroundRequest(BaseModel):
    image: str
    phrase: str
    multi: bool = True
    mode: str = "hybrid"
    max_new_tokens: int = 1024
    temperature: float = 0.7


app = FastAPI(title="LocateAnything-3B")
worker: LocateAnythingWorker = None
gpu_lock = threading.Lock()   # one generation at a time on the GPU


def _decode(b64: str) -> Image.Image:
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    except Exception as e:
        raise HTTPException(400, f"bad image: {e}")


def _run(image: Image.Image, prompt: str, mode: str, max_new_tokens: int,
         temperature: float) -> dict:
    t0 = time.time()
    with gpu_lock:
        answer = worker.predict(image, prompt, generation_mode=mode,
                                max_new_tokens=max_new_tokens, temperature=temperature)
    return {"answer": answer, "boxes": parse_boxes(answer),
            "latency_s": round(time.time() - t0, 3)}


@app.get("/health")
def health():
    return {"model": MODEL_ID, "device": str(worker.device),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}


@app.post("/detect")
def detect(req: DetectRequest):
    prompt = ("Locate all the instances that matches the following description: "
              + "</c>".join(req.categories) + ".")
    return _run(_decode(req.image), prompt, req.mode, req.max_new_tokens, req.temperature)


@app.post("/ground")
def ground(req: GroundRequest):
    kind = "all the instances that match" if req.multi else "a single instance that matches"
    prompt = f"Locate {kind} the following description: {req.phrase}."
    return _run(_decode(req.image), prompt, req.mode, req.max_new_tokens, req.temperature)


def main():
    global worker
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--model", default=MODEL_ID)
    args = ap.parse_args()

    print(f"Loading {args.model} ...")
    t0 = time.time()
    worker = LocateAnythingWorker(args.model)
    print(f"Loaded in {time.time() - t0:.0f}s on "
          f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}")

    # Warm-up so the first real request isn't slow
    _run(Image.new("RGB", (640, 480), "gray"),
         "Locate all the instances that matches the following description: door.",
         "hybrid", 64, 0.7)
    print(f"Ready: http://{args.host}:{args.port}  (POST /detect, /ground; GET /health)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
