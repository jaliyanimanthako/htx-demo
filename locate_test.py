"""
LocateAnything quick test
=========================
Runs LocateAnything-3B (server/serve_locate.sh) on frames sampled from a video,
prints the raw answer + timing per frame, and saves a grid of annotated frames.

Usage:
  python locate_test.py IMG_0450.MOV --url http://<server-ip>:8020
  python locate_test.py IMG_0450.MOV --url ... --mode fast --n 6
  python locate_test.py IMG_0450.MOV --url ... --categories "door,exit sign,office chair,cardboard box"
"""

import argparse
import hashlib
import statistics
from pathlib import Path

import cv2
from PIL import Image

from engines.locate_engine import LocateEngine

DEFAULT_CATEGORIES = [
    "door", "exit sign", "chair", "stool", "table", "cardboard box", "trolley", "cart",
    "trash bin", "bag", "plant", "ladder", "bicycle", "cable",
]


def _color(label: str) -> tuple:
    h = hashlib.md5(label.encode()).digest()
    return (60 + h[0] % 196, 60 + h[1] % 196, 60 + h[2] % 196)


def draw(frame, dets: list):
    out = frame.copy()
    scale = max(0.5, frame.shape[0] / 1080)
    for d in dets:
        bb, c = d["bounding_box"], _color(d["label"])
        thick = 6 if d["label"] == "door" else 3
        cv2.rectangle(out, (bb["x1"], bb["y1"]), (bb["x2"], bb["y2"]), c, thick)
        (tw, th), _ = cv2.getTextSize(d["label"], cv2.FONT_HERSHEY_SIMPLEX, 0.9 * scale, 2)
        y = max(th + 8, bb["y1"])
        cv2.rectangle(out, (bb["x1"], y - th - 8), (bb["x1"] + tw + 8, y), c, -1)
        cv2.putText(out, d["label"], (bb["x1"] + 4, y - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9 * scale, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser(description="Test LocateAnything-3B on video frames")
    ap.add_argument("video")
    ap.add_argument("--url", required=True, help="e.g. http://<server-ip>:8020")
    ap.add_argument("--n", type=int, default=6, help="Frames to sample (default: 6)")
    ap.add_argument("--mode", default="hybrid", choices=["fast", "hybrid", "slow"])
    ap.add_argument("--max-side", type=int, default=1280)
    ap.add_argument("--categories", default=",".join(DEFAULT_CATEGORIES),
                    help="Comma-separated categories to locate")
    ap.add_argument("--out", default="results/locate")
    args = ap.parse_args()

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    engine = LocateEngine(args.url, max_image_side=args.max_side, mode=args.mode)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(args.video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    tiles, lat = [], []
    print(f"{len(cats)} categories, mode={args.mode}, {args.max_side}px\n")
    for i in range(args.n):
        fi = int((i + 0.5) * total / args.n)
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.read()
        if not ok:
            continue
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        res = engine.detect_raw(img, cats)
        dets = engine._to_pixels(res["boxes"], *img.size)
        lat.append(res["latency_s"])
        print(f"frame {fi:>5}: model {res['latency_s']:.2f}s, round trip {res['round_trip_s']:.2f}s, "
              f"{len(dets)} boxes: {sorted({d['label'] for d in dets})}")
        print(f"   raw: {res['answer'][:300]}{'...' if len(res['answer']) > 300 else ''}")
        tiles.append(cv2.resize(draw(frame, dets), (640, 360)))
    cap.release()

    if tiles:
        while len(tiles) % 3:
            tiles.append(tiles[-1] * 0)
        grid = cv2.vconcat([cv2.hconcat(tiles[i:i + 3]) for i in range(0, len(tiles), 3)])
        path = out_dir / f"{Path(args.video).stem}_locate_{args.mode}.jpg"
        cv2.imwrite(str(path), grid)
        print(f"\nmodel latency mean {statistics.mean(lat):.2f}s "
              f"(min {min(lat):.2f} / max {max(lat):.2f}) | grid → {path}")


if __name__ == "__main__":
    main()
