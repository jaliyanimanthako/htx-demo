"""
VLM latency benchmark
=====================
Measures how fast an endpoint answers the exit-detection prompt on real
frames, so self-hosted vLLM (4090) and OpenRouter can be compared.

For each image size it reports:
  - sequential: time to first token (TTFT), total latency, output tokens
  - concurrent: results/second with N calls in flight
and saves one annotated frame per size so you can check the boxes.

Usage (from the Mac, against the 4090 server):
  python bench_latency.py IMG_0450.MOV --provider local --base-url http://<server-ip>:8000/v1
  python bench_latency.py IMG_0450.MOV --provider local --sizes 448 640 --concurrency 1 4 8
  python bench_latency.py IMG_0450.MOV --provider openrouter          # baseline for comparison
"""

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from core.config import MAX_RESPONSE_TOKENS, PROVIDERS
from core.drawing import draw_detections, draw_status_banner
from core.parsing import parse_response
from core.prompts import USER_TEXT, build_system_prompt
from core.video_io import resize_for_api
from engines.qwen_engine import QwenEngine


def sample_frames(video: Path, n: int) -> list:
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * total / n))
        ok, f = cap.read()
        if ok:
            frames.append(f)
    cap.release()
    return frames


def call(engine: QwenEngine, frame, max_side: int, system_prompt: str) -> dict:
    """One streamed request; returns ttft, latency, output tokens, text (or error)."""
    t0 = time.time()
    try:
        return _call(engine, frame, max_side, system_prompt)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "total": time.time() - t0,
                "ttft": 0.0, "tokens": None, "prompt_tokens": None, "text": ""}


def _call(engine: QwenEngine, frame, max_side: int, system_prompt: str) -> dict:
    b64 = engine._encode_image(resize_for_api(frame, max_side))
    t0 = time.time()
    ttft, chunks, usage = None, [], None
    stream = engine.client.chat.completions.create(
        model=engine.model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": USER_TEXT},
            ]},
        ],
        max_tokens=engine.max_tokens,
        temperature=0,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=engine.extra_body,
    )
    for ch in stream:
        if ch.choices and ch.choices[0].delta.content:
            if ttft is None:
                ttft = time.time() - t0
            chunks.append(ch.choices[0].delta.content)
        if getattr(ch, "usage", None):
            usage = ch.usage
    return {
        "ttft":   ttft or 0.0,
        "total":  time.time() - t0,
        "tokens": usage.completion_tokens if usage else None,
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "text":   "".join(chunks),
    }


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))]


def main():
    ap = argparse.ArgumentParser(description="Benchmark VLM latency on video frames")
    ap.add_argument("video")
    ap.add_argument("--provider", default="local", choices=list(PROVIDERS))
    ap.add_argument("--model",    default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key",  default=None)
    ap.add_argument("--n",        type=int, default=10, help="Frames per test (default: 10)")
    ap.add_argument("--sizes",    type=int, nargs="+", default=[448, 640],
                    help="Image long-side sizes to test (default: 448 640)")
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8],
                    help="Calls in flight to test (default: 1 4 8)")
    ap.add_argument("--out-dir",  default="results/bench")
    args = ap.parse_args()

    preset  = PROVIDERS[args.provider]
    api_key = args.api_key or os.environ.get(preset["env_key"]) or preset.get("api_key")
    if not api_key:
        sys.exit(f"API key not found. Set {preset['env_key']} or pass --api-key.")
    engine = QwenEngine(api_key=api_key,
                        model_name=args.model or preset["model"],
                        base_url=args.base_url or preset["base_url"],
                        max_image_side=max(args.sizes),
                        max_tokens=MAX_RESPONSE_TOKENS,
                        extra_body=preset.get("extra_body"))
    prompt = build_system_prompt()
    frames = sample_frames(Path(args.video), args.n)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{len(frames)} frames from {args.video}\n")

    print("Warm-up call...")
    call(engine, frames[0], args.sizes[0], prompt)

    summary = []
    for size in args.sizes:
        for conc in args.concurrency:
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=conc) as pool:
                runs = list(pool.map(lambda f: call(engine, f, size, prompt), frames))
            wall = time.time() - t0

            errors = [r["error"] for r in runs if "error" in r]
            runs = [r for r in runs if "error" not in r]
            if not runs:
                print(f"  {size}px x{conc:<2}  all {len(errors)} calls failed: {errors[0]}")
                continue
            totals = [r["total"] for r in runs]
            ttfts  = [r["ttft"] for r in runs]
            toks   = [r["tokens"] for r in runs if r["tokens"]]
            parsed = 0
            for r in runs:
                try:
                    if parse_response(r["text"], 1000, 1000) or "NONE" in r["text"].upper():
                        parsed += 1
                except Exception:
                    pass
            row = {
                "size": size, "concurrency": conc,
                "latency_mean": round(statistics.mean(totals), 2),
                "latency_p50": round(pct(totals, 0.5), 2),
                "latency_p90": round(pct(totals, 0.9), 2),
                "ttft_mean": round(statistics.mean(ttfts), 2),
                "out_tokens_mean": round(statistics.mean(toks)) if toks else None,
                "prompt_tokens": runs[0]["prompt_tokens"],
                "results_per_s": round(len(runs) / wall, 2),
                "parsed": f"{parsed}/{len(runs)}",
                "errors": len(errors),
            }
            summary.append(row)
            print(f"  {size}px x{conc:<2}  latency {row['latency_mean']:.2f}s "
                  f"(p50 {row['latency_p50']:.2f} / p90 {row['latency_p90']:.2f})  "
                  f"ttft {row['ttft_mean']:.2f}s  out {row['out_tokens_mean']} tok  "
                  f"in {row['prompt_tokens']} tok  → {row['results_per_s']:.2f} results/s  "
                  f"parsed {row['parsed']}" + (f"  errors {len(errors)}" if errors else ""))

        # One annotated frame per size, to eyeball box quality
        r = call(engine, frames[len(frames) // 2], size, prompt)
        f = frames[len(frames) // 2].copy()
        try:
            dets = parse_response(r["text"], f.shape[1], f.shape[0])
        except Exception:
            dets = []
        draw_detections(f, dets, top_margin=60)
        draw_status_banner(f, dets)
        img_path = out_dir / f"bench_{args.provider}_{size}px.jpg"
        cv2.imwrite(str(img_path), f)
        print(f"  sample → {img_path}\n")

    model_tag = (args.model or preset["model"]).replace("/", "_")
    json_path = out_dir / f"bench_{args.provider}_{model_tag}.json"
    with open(json_path, "w") as fh:
        json.dump({"provider": args.provider, "model": args.model or preset["model"],
                   "base_url": args.base_url or preset["base_url"], "results": summary},
                  fh, indent=2)
    print(f"Saved {json_path}")


if __name__ == "__main__":
    main()
