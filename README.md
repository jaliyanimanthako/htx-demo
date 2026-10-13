# Exit Obstruction Monitor

Detects **exits** (doors, glass doors, doorways) in video from a handheld camera and
decides, in near real time, whether each one is **clear**, **partially obstructed** or
**obstructed**, and by what (boxes, chairs, trolleys, …).

Detection runs on a GPU server (2× RTX 4090) with three models working at different
speeds. The client — your laptop, or the server itself — plays the video, tracks
objects every frame and shows the result live.

| Model | Role | Runs |
|---|---|---|
| **YOLOE-11s** (Ultralytics, open-vocabulary) | Fast boxes + tracking | every frame (client GPU) |
| **LocateAnything-3B** (NVIDIA) | Accurate boxes, finds what YOLOE misses | ~1 per 1.5–2 s (server GPU 1) |
| **Qwen3-VL-4B-Instruct** (vLLM) | Scene understanding: is it an exit? on the floor? blocked? | only on events (server GPU 0) |

> The recorded test videos (`IMG_04xx.MOV`) stand in for the live camera. Any script
> also accepts a webcam index (`0`) or an RTSP URL, so switching to the real camera
> needs no code changes.

---

## Contents

1. [Architecture](#architecture)
2. [Repository layout](#repository-layout)
3. [Setup](#setup) — client, GPU server, `.env`
4. [Running](#running) — the main monitor and the other runners
5. [How decisions are made](#how-decisions-are-made)
6. [Measured performance](#measured-performance)
7. [Troubleshooting](#troubleshooting)
8. [Limitations, reproducibility, licences](#limitations)

---

## Architecture

`exit_monitor.py` is the main program. Three loops run at different speeds and meet in
a per-exit memory; nothing in the per-frame loop ever waits for a model on the server.

```
 video / camera ─► FAST LOOP (every frame, ~30–80 ms) ─────────────────────► window / browser
                   YOLOE ─► tracker (IDs, camera motion) ─► position rule ─► draw
                        ▲ boxes            │ events            ▲ verdicts
                        │                  ▼                   │
          LocateAnything worker      EXIT MEMORY  (one record per exit)
          (~1 / 1.5–2 s, GPU 1)      status · since when · blockers · snapshot · history
          Qwen names extend its           │ events ▲ verdicts
          category list                   ▼        │
                                     Qwen-4B worker (on events, GPU 0)
                                     • new door           → "is it an exit?"
                                     • new obstacle there → "floor or raised surface?"
                                     • blockers changed / every 8 s → verdict C / P / B
                                                  │
                                                  ▼
                                   report.json + snapshot per status change
```

- **Fast loop** – YOLOE detects doors, EXIT signs and obstacles from text prompts;
  a tracker keeps stable IDs and moves boxes with the camera (optical flow) between
  detections; a geometric rule gives a per-frame guess of each exit's status.
- **LocateAnything** – the worker always takes the *newest* frame, gets high-quality
  labelled boxes, maps them to the current frame (camera motion) and merges them into
  the tracks. Qwen periodically names objects at the door; names not already covered
  are added to LocateAnything's categories (open-world).
- **Qwen** – asked only when something changes. Its answers are stored on the
  track/exit, so a chair confirmed on the floor stays confirmed while it is tracked.
- **Exit memory** – an exit's status changes only after the new status has held for
  `--hold` s (default 1.5), and only once Qwen has confirmed it is an exit (or after
  `--confirm-timeout` s). Each change is logged with a snapshot.

Simpler variants are also included (see [Other runners](#other-runners)).

---

## Repository layout

```
exit_monitor.py      MAIN: YOLOE + LocateAnything + Qwen, exit memory, panel, report
locate_live.py       LocateAnything (+ optional Qwen assist) live, position rule
live_infer.py        Qwen-only live (prompt → boxes + status); --delay buffered mode
video_infer.py       Qwen-only offline: process a recorded video, side-by-side output
yoloe_infer.py       YOLOE-only live (local, no server)
bench_latency.py     Benchmark a VLM endpoint (TTFT, latency, throughput)
locate_test.py       Run LocateAnything on sampled frames, save an annotated grid

core/                shared library (no entry points)
  config.py          .env loading, VLM provider presets
  prompts.py         every prompt in one place
  parsing.py         VLM answer → detections (compact E/O lines or JSON) + sanity checks
  rules.py           geometric obstruction rule (clearance zone, occlusion, double doors)
  depth_check.py     close-up "floor or furniture?" check with a position cache
  qwen_detect.py     one full-frame Qwen detection call
  qwen_assist.py     Qwen naming objects for LocateAnything + depth check
  labels.py          LocateAnything categories
  video_io.py        LiveSource (file paced as live / webcam / RTSP), camera motion
  drawing.py         boxes, banner, captions
  stream_view.py     MJPEG browser view (--stream)
engines/
  qwen_engine.py     OpenAI-compatible VLM client (vLLM, OpenRouter, DashScope, OpenAI)
  locate_engine.py   LocateAnything server client + answer parser
server/              GPU-server scripts (setup, serve, run) — see below
tests/test_core.py   offline tests for parsing / rules / LocateAnything output
```

Outputs go to `results/` (git-ignored); model weights to `models/` (git-ignored).

---

## Setup

### 1. Client (Mac / laptop)

Tested with Python 3.14 on macOS (Apple Silicon; YOLOE runs on the MPS GPU).

```bash
cd htx_demo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.sample .env          # then fill in the server addresses
.venv/bin/python tests/test_core.py    # offline sanity check: should print 11/11 passed
```

The first YOLOE run downloads `yoloe-11s-seg.pt` and the MobileCLIP text encoder
(~630 MB) into `models/`.

### 2. GPU server (Linux, NVIDIA driver)

Copy the project to the server (from the Mac):

```bash
cd <folder that contains htx_demo>
rsync -av --exclude .venv --exclude models --exclude results --exclude __pycache__ \
      htx_demo/ <user>@<server>:~/qwe/
```

Each model gets its **own Python environment** (their pinned packages conflict).
Start servers inside **tmux** so they survive logging out
(`Ctrl+B` then `D` to detach, `tmux attach -t <name>` to look again).

**Qwen (vLLM, OpenAI-compatible API)**

```bash
cd ~/qwe
bash server/setup_vllm.sh                 # once: ~/qwen-vllm/.venv with vllm==0.31.0
tmux new -s qwen
bash server/serve_qwen.sh 4b              # Qwen/Qwen3-VL-4B-Instruct on port 8000+ (first free)
```

`serve_qwen.sh` also accepts `2b`, `8b-fp8`, `8b`. It picks the first free port from
8000 upward, lowers `gpu_memory_utilization` to what is free on a shared GPU, and
disables the FlashInfer sampler (see [Troubleshooting](#troubleshooting)).
Choose the GPU with `CUDA_VISIBLE_DEVICES=1 bash server/serve_qwen.sh 4b`.

**LocateAnything-3B (transformers, small FastAPI server)**

```bash
bash server/setup_locate.sh               # once: ~/locate-anything/.venv (Python 3.11) + weights
tmux new -s locate
bash server/serve_locate.sh               # GPU 1 by default, port 8020+ (first free)
```

Endpoints: `GET /health`, `POST /detect {image, categories, mode}`,
`POST /ground {image, phrase}`. Boxes come back on a 0–1000 scale.

**Optional — run the monitor on the server too**

```bash
bash server/setup_monitor.sh              # once: ~/exit-monitor/.venv (torch, ultralytics, …)
bash server/run_monitor.sh IMG_0451.MOV   # finds both servers, loops the video,
                                          # browser view on http://<server>:8090
```

### 3. `.env`

All scripts load `.env` from the project root. The important entries:

```bash
LOCATE_URL=http://<server-ip>:8020        # LocateAnything
QWEN_URL=http://<server-ip>:8010/v1       # Qwen vLLM (port printed by serve_qwen.sh)
LOCAL_VLM_URL=http://<server-ip>:8010/v1  # same server, for --provider local
LOCAL_VLM_MODEL=Qwen/Qwen3-VL-4B-Instruct
OPENROUTER_API_KEY=...                    # only for the hosted-API option
```

Check that both servers answer before running:

```bash
curl http://<server-ip>:8010/v1/models
curl http://<server-ip>:8020/health
```

If the ports are not reachable from the client, tunnel them:
`ssh -L 8010:localhost:8010 -L 8020:localhost:8020 <user>@<server>` and use `localhost`.

---

## Running

### Main: `exit_monitor.py`

```bash
.venv/bin/python exit_monitor.py IMG_0451.MOV            # URLs from .env
.venv/bin/python exit_monitor.py IMG_0451.MOV --loop --save results/run.mp4
.venv/bin/python exit_monitor.py IMG_0451.MOV --no-yoloe # LocateAnything + Qwen only
.venv/bin/python exit_monitor.py IMG_0451.MOV --no-display --stream   # browser :8090
```

Window: original | annotated video, and a panel listing each exit (status, for how
long, blockers) plus the latest events. Keys: `q`/`Esc` quit, `space` pause.

| Option | Default | Meaning |
|---|---|---|
| `--locate-url`, `--qwen-url` | from `.env` | model servers (either can be omitted) |
| `--no-yoloe` | off | no per-frame detector; tracks come from LocateAnything only |
| `--la-interval` | 1.0 s | minimum gap between LocateAnything calls |
| `--name-every` | 3.0 s | how often Qwen names objects at the door |
| `--hold` | 1.5 s | a new status must hold this long before it is shown |
| `--recheck` | 8.0 s | re-ask Qwen for a verdict at least this often per exit |
| `--verdict-ttl` | 6.0 s | how long a Qwen verdict counts |
| `--confirm-timeout` | 6.0 s | show a door's status without Qwen's exit confirmation after this |
| `--proc-width` | 960 | processing width in px |
| `--quiet` | off | no per-frame log line |
| `--stream [PORT]`, `--save FILE`, `--loop`, `--no-display`, `--out-dir` | | output options |

**Terminal log** – one line per processed frame, plus events:

```
f   98 t=  3.27s   76ms | YOLOE  8 det  54ms | LA: result back: 21 boxes (2.2s); sent frame; merged 21 boxes | Qwen: -> depth#24 -> verdict#9 | tracks: 2 door, 16 obstacle
f  137 t=  4.57s   73ms | YOLOE  7 det  69ms | LA: - | Qwen: <- confirm#9=yes | tracks: 3 door, 12 obstacle
[   4.8s] EXIT #9: CHECKING -> BLOCKED (chair, stool, cardboard box x4)
```

`->` = a Qwen job started (`confirm` = is it an exit, `depth` = floor or raised,
`verdict` = C/P/B), `<-` = its answer. LocateAnything runs back to back: it sends the
newest frame as soon as the previous answer is back (and `--la-interval` has passed).

**Output** – `results/exit_monitor_<video>/report.json` (status history, blockers and a
snapshot per exit, all events) and `exit<id>_<time>_<status>.jpg` snapshots.

### Other runners

| Script | What it does | Example |
|---|---|---|
| `locate_live.py` | LocateAnything every ~1.5–2 s + position rule; `--qwen-url` adds Qwen naming + depth check | `python locate_live.py IMG_0451.MOV --url http://<srv>:8020 --qwen-url http://<srv>:8010/v1` |
| `live_infer.py` | Qwen detects exits + obstructions from the prompt; results shown live, boxes motion-compensated | `python live_infer.py IMG_0451.MOV --provider local --interval 0.2 --workers 4` |
| `live_infer.py --delay N` | buffered: plays N s behind live so every frame has a fresh result | `python live_infer.py IMG_0451.MOV --provider local --delay 4` |
| `video_infer.py` | offline: parallel calls over a recorded video → side-by-side mp4 + JSON | `python video_infer.py IMG_0450.MOV --provider local --play` |
| `yoloe_infer.py` | YOLOE + position rule only, no server | `python yoloe_infer.py IMG_0450.MOV` |

`--provider` for the Qwen runners: `local` (your vLLM server, from `LOCAL_VLM_URL`),
`openrouter` (default, `qwen/qwen3-vl-8b-instruct`), `qwen` (DashScope), `openai`.
All live runners accept `--stream`, `--save`, `--loop`, `--no-display`.

### Tools

```bash
python bench_latency.py IMG_0450.MOV --provider local --sizes 448 640 --concurrency 1 4 8
python locate_test.py IMG_0450.MOV --url http://<srv>:8020 --mode fast
python tests/test_core.py
bash server/run_realtime_test.sh IMG_0450.MOV   # on the server: benchmark + live run + verdict
bash server/watch_live.sh IMG_0450.MOV          # on the server: live_infer → browser
```

---

## How decisions are made

**Exits.** Doors come from YOLOE / LocateAnything (`door`, `glass door`, `doorway`).
Side-by-side leaves are merged into one door; a door with an EXIT sign just above it is
labelled `EXIT`. Qwen confirms each new door is a way out (rejects TVs, cupboards,
glass partitions) before any status is shown.

**Obstructions** (`core/rules.py`). For each door, a *clearance zone* covers the lower
part of the door and the floor in front of it (a bit wider than the door). An object
obstructs the door if
- it stands on the floor inside that zone (its bottom edge is in the zone), or
- it hides the bottom edge of the door (occlusion rule — needed because detectors box
  only the visible part of a door partly hidden by boxes).

Status from how much of the door width the obstructions cover:
≥ 60 % → **obstructed**, any → **partially obstructed**, none → **clear**.
Qwen's verdict (C/P/B on a close-up of the door), when recent, is combined with the
rule — the more severe one wins.

**Depth check.** A 2D image can't tell a plant on a desk in front of the door from a
plant at the door. Each new obstruction gets a close-up of its base and the question
"floor or raised surface?" (`DEPTH_PROMPT` in `core/prompts.py`); objects on raised
surfaces are ignored for as long as they are tracked.

**Prompts.** All prompts live in `core/prompts.py`. The full-frame detection prompt
asks for a compact line format (`E P x1 y1 x2 y2`, `O 1 floor chair x1 y1 x2 y2`) —
about 3× shorter than JSON, which matters because answer length dominates latency on a
self-hosted GPU.

---

## Measured performance

Measured during development (RTX 4090 server shared with another user; Mac = Apple
Silicon client over the LAN). Treat as indicative.

| Component | Measured |
|---|---|
| Qwen3-VL-4B, full-frame detection (compact format) | 0.8–1.3 s per call |
| Qwen3-VL-4B, short answers (name objects / floor check / yes-no) | 0.2–0.7 s |
| Qwen3-VL-8B (bf16), full-frame detection | 2–3 s (~44 tokens/s) |
| Qwen3-VL-8B via OpenRouter | 2.3–4 s, occasional 10 s+ outliers |
| LocateAnything-3B, `hybrid`, 960 px | 1.5–1.9 s model time, 2–2.5 s round trip |
| YOLOE-11s on the Mac (MPS) | 45–75 ms per frame |
| `exit_monitor.py` fast loop on the Mac | ~75 ms per frame (~13 fps) |

YOLOE on the 4090 (`server/run_monitor.sh`) is expected to be several times faster but
has not been benchmarked yet.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Connection refused` on 8010 / 8020 | Server not running (often stopped when its SSH session closed) → start it in **tmux**. `run_monitor.sh` prints the exact command. |
| `Address already in use` | Port taken on the shared server → scripts move to the next free port automatically; read the printed URL. |
| `Free memory on device … less than desired GPU memory utilization` | GPU shared → `serve_qwen.sh` lowers it automatically; or `GPU_UTIL=0.5`, or use the other GPU with `CUDA_VISIBLE_DEVICES=1`. |
| `nvcc fatal: Unknown option '--compress-mode=size'` | FlashInfer JIT with an old system CUDA → `serve_qwen.sh` sets `VLLM_USE_FLASHINFER_SAMPLER=0`. |
| All results `exits=0` with Qwen-4B | The model answered in its own JSON layout → handled by `core/parsing.py`; make sure the client code is up to date. |
| `Qwen assist: OFF` | `--qwen-url` not given and `QWEN_URL` not in `.env`. |
| Browser view not reachable | `ssh -L 8090:localhost:8090 <user>@<server>` → `http://localhost:8090`. |
| LocateAnything setup fails at the PyTorch step | `TORCH_INDEX=https://download.pytorch.org/whl/cu121 bash server/setup_locate.sh` |

---

## Limitations

- **Depth from 2D.** The floor-vs-raised check is a VLM judgement on a crop; it can
  drop a real chair standing next to a cabinet, or keep a desk item. A depth model or
  RGB-D camera would make this geometric.
- **Fixed furniture.** Cabinets/desks near a door may be labelled `table` and counted
  as obstructions.
- **Double doors** are sometimes tracked as one leaf.
- **Handheld motion.** Fast pans can briefly mis-place boxes until the next detection.
- **Status lag.** A change is shown ~1.5 s (hold) after it is detected; LocateAnything
  results are 1.5–2.5 s old when they arrive (boxes are motion-compensated).

## Reproducibility

- Client packages are pinned in `requirements.txt`; server environments are created by
  `server/setup_*.sh` (vLLM 0.31.0, transformers 4.57.1 for LocateAnything,
  ultralytics 8.4.176).
- Models: `Qwen/Qwen3-VL-4B-Instruct` (also tested: 2B, 8B, 8B-FP8),
  `nvidia/LocateAnything-3B`, `yoloe-11s-seg.pt` — downloaded automatically.
- Qwen calls use `temperature=0`, so the same frame gives the same answer.
  LocateAnything uses the sampling settings from its model card (temperature 0.7), so
  its boxes can vary slightly between runs.
- Live runs pace the video to wall-clock time and drop frames when processing is slow,
  so frame-level results depend on hardware speed; `video_infer.py` (offline) does not
  drop frames.

## Licences

- **LocateAnything-3B** – NVIDIA licence, **non-commercial research use only**.
- **YOLOE / Ultralytics** – AGPL-3.0 (commercial use needs an Ultralytics licence).
- **Qwen3-VL** – Apache-2.0.

Fine for research; check before any product use.
