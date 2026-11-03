"""
Model choices: the YOLOE weights and Qwen3-VL checkpoints the runners can use, by
short name. config.toml [models] selects them (python run.py inspect --list-models).

    yoloe_file("11l")                    → "yoloe-11l-seg.pt"
    qwen_repo("4b-fp8")                  → "Qwen/Qwen3-VL-4B-Instruct-FP8"
    qwen_repo("qwen3-vl-4b-instruct")    → "Qwen/Qwen3-VL-4B-Instruct"

Ultralytics downloads YOLOE weights into models/ on first use. vLLM serves one Qwen
model at a time and downloads it when it starts with it (server/start_laptop.sh <name>,
server/serve_qwen.sh <name>). No third-party imports: the server scripts read this
file with the system Python.
"""

from pathlib import Path

# name → weights file. Text-prompt models only: the prompt-free ("-pf") ones can't take
# the class lists. Within a family, bigger (n < s < m < l < x) is slower and finds more.
YOLOE_MODELS = {
    "11s": "yoloe-11s-seg.pt",       # default: ~10 ms per frame on the RTX 4060 laptop
    "11m": "yoloe-11m-seg.pt",
    "11l": "yoloe-11l-seg.pt",
    "v8s": "yoloe-v8s-seg.pt",
    "v8m": "yoloe-v8m-seg.pt",
    "v8l": "yoloe-v8l-seg.pt",
    "26n": "yoloe-26n-seg.pt",
    "26s": "yoloe-26s-seg.pt",
    "26m": "yoloe-26m-seg.pt",
    "26l": "yoloe-26l-seg.pt",
    "26x": "yoloe-26x-seg.pt",
}
DEFAULT_YOLOE = "11s"

# name → (Hugging Face repo, GB of weights). Instruct models only: the Thinking ones
# reason at length first, and the one-word answers (yes/no, C/P/B) would be cut off.
# The 8 GB laptop fits the 2B models next to YOLOE; 4B / 8B need a bigger GPU.
QWEN_MODELS = {
    "2b":     ("Qwen/Qwen3-VL-2B-Instruct", 4.3),          # default
    "2b-fp8": ("Qwen/Qwen3-VL-2B-Instruct-FP8", 3.5),
    "4b":     ("Qwen/Qwen3-VL-4B-Instruct", 8.9),
    "4b-fp8": ("Qwen/Qwen3-VL-4B-Instruct-FP8", 6.0),
    "8b":     ("Qwen/Qwen3-VL-8B-Instruct", 17.5),
    "8b-fp8": ("Qwen/Qwen3-VL-8B-Instruct-FP8", 10.6),
}
DEFAULT_QWEN = "2b"


def yoloe_file(name: str) -> str:
    """Short name ("11l"), "yoloe-11l" or the file name → weights file. Other .pt files
    (your own weights in models/) pass through."""
    n = name.strip().lower().removesuffix(".pt")
    for key, file in YOLOE_MODELS.items():
        if n in (key, f"yoloe-{key}", file.removesuffix(".pt")):
            return file
    if n.endswith("-pf"):
        raise ValueError(f"{name}: prompt-free YOLOE models can't take the class prompts")
    if name.strip().endswith(".pt"):
        return name.strip()
    raise ValueError(f"unknown YOLOE model {name!r} — one of: {', '.join(YOLOE_MODELS)}")


def qwen_repo(name: str) -> str:
    """Short name ("4b-fp8"), repo name ("qwen3-vl-4b-instruct", any case) or repo id →
    repo id. Other ids with a "/" pass through (vLLM serves any Hugging Face model)."""
    n = name.strip().lower()
    for key, (repo, _) in QWEN_MODELS.items():
        if n in (key, repo.lower(), repo.split("/")[1].lower()):
            return repo
    if "/" in n:
        return name.strip()
    raise ValueError(f"unknown Qwen model {name!r} — one of: {', '.join(QWEN_MODELS)}, "
                     "or a Hugging Face repo id")


def qwen_gb(repo: str):
    """GB of weights of a catalog model, else None."""
    return next((gb for r, gb in QWEN_MODELS.values() if r == repo), None)


def yoloe_label(file: str) -> str:
    """'yoloe-11s-seg.pt' → 'YOLOE-11s' (panels, logs)."""
    stem = Path(file).name.removesuffix(".pt")
    return f"YOLOE-{stem[6:].removesuffix('-seg')}" if stem.startswith("yoloe-") else stem


def qwen_label(repo: str) -> str:
    """'Qwen/Qwen3-VL-2B-Instruct-FP8' → 'Qwen3-VL-2B-FP8' (panels, logs)."""
    return repo.split("/")[-1].replace("-Instruct", "")
