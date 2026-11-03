"""
Single entry point for every program in apps/ (and the backend):

    python run.py                          list the commands
    python run.py <command> -h             options of one command
    python run.py inspect IMG_0451.MOV     exits + fire extinguishers (settings: config.toml)
    python run.py exits rtsp://<windows-host>:8554/live/x2

Old script names work as commands too (python run.py exit_monitor …).
"""

import importlib
import sys

COMMANDS = {
    # command          module                          what it does
    "inspect":       ("apps.fire_safety_inspection", "exits + fire extinguishers on the same frames (config.toml)"),
    "exits":         ("apps.exit_monitor",           "exit / obstruction monitor: YOLOE + LocateAnything + Qwen"),
    "extinguishers": ("apps.extinguisher_monitor",   "fire extinguishers + expiry dates (video, camera, photos)"),
    "camera-check":  ("apps.camera_check",           "show a camera / stream without models: fps, drops, latency"),
    "yoloe":         ("apps.yoloe_infer",            "YOLOE-only exit detection (no model server)"),
    "locate-live":   ("apps.locate_live",            "LocateAnything (+ optional Qwen assist) live"),
    "live":          ("apps.live_infer",             "Qwen-only live detection"),
    "video":         ("apps.video_infer",            "Qwen-only offline video analysis"),
    "locate-test":   ("apps.locate_test",            "LocateAnything on sampled frames → annotated grid"),
    "bench":         ("apps.bench_latency",          "benchmark a VLM endpoint (latency, throughput)"),
    "backend":       ("backend.__main__",            "FastAPI backend for the web app (= python -m backend)"),
}
ALIASES = {module.removeprefix("apps."): cmd for cmd, (module, _) in COMMANDS.items()
           if module.startswith("apps.")}


def usage() -> str:
    rows = "\n".join(f"  {cmd:<14} {what}" for cmd, (_, what) in COMMANDS.items())
    return f"usage: python run.py <command> [options]   (python run.py <command> -h)\n\n{rows}"


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(usage())
        return
    cmd = sys.argv[1].removesuffix(".py")
    cmd = ALIASES.get(cmd, cmd)
    if cmd not in COMMANDS:
        sys.exit(f"unknown command: {sys.argv[1]}\n\n{usage()}")
    sys.argv = [f"run.py {cmd}", *sys.argv[2:]]       # argparse then shows "usage: run.py <command> …"
    importlib.import_module(COMMANDS[cmd][0]).main()


if __name__ == "__main__":
    main()
