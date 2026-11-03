"""
Offline tests for the core logic (no GPU, no network):

    python tests/test_core.py        # or: python -m pytest tests
"""

import contextlib
import io
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date                                             # noqa: E402

import cv2                                                            # noqa: E402
import numpy as np                                                    # noqa: E402

from core import expiry, video_io                                     # noqa: E402
from core import models                                               # noqa: E402
from core.labels import kind_of                                      # noqa: E402
from core.parsing import parse_response                               # noqa: E402
from core.rules import assess, covers_door_base, merge_double_doors  # noqa: E402
from engines.locate_engine import parse_answer                        # noqa: E402

W, H = 1920, 1080


def test_compact_lines():
    d = parse_response("E P 300 100 600 900\nO 1 floor chair 350 500 500 905", W, H)
    assert [x["label"] for x in d] == ["door", "chair"]
    assert d[0]["status"] == "partially_obstructed"
    assert d[1]["blocks_exit"] == "door"


def test_none_and_prose():
    assert parse_response("NONE", W, H) == []
    assert parse_response("Example:\nExit found\nE B 1 2 3", W, H) == []


def test_furniture_items_dropped():
    d = parse_response("E P 500 110 720 930\nO 1 desk plant 520 560 650 780", W, H)
    assert [x["type"] for x in d] == ["exit"]


def test_json_fallback_layout():
    raw = ('{"exits": [{"status": "B", "box": [310, 60, 710, 980]}], "obstructions": ['
           '{"exit": 1, "rests_on": "floor", "label": "cardboard box", "box": [360, 680, 540, 980]},'
           '{"exit": 1, "rests_on": "desk", "label": "plant", "box": [400, 600, 500, 800]}]}')
    d = parse_response(raw, W, H)
    assert [(x["label"], x.get("status")) for x in d] == [("door", "obstructed"), ("cardboard box", None)]


def test_repeated_lines_deduplicated():
    lines = ["E P 315 50 730 990"] + [f"O 1 floor cardboard box 380 {700 - i} 530 {990 - i}" for i in range(10)]
    d = parse_response("\n".join(lines), W, H)
    assert sum(x["type"] == "obstruction" for x in d) == 1


def test_far_obstruction_dropped():
    d = parse_response("E C 775 0 1000 999\nO 1 floor office chair 230 725 430 999", W, H)
    assert [x["type"] for x in d] == ["exit"] and d[0]["status"] == "clear"


def test_model_names():
    assert models.yoloe_file("11L") == models.yoloe_file("yoloe-11l-seg.pt") == "yoloe-11l-seg.pt"
    assert models.yoloe_file("my-weights.pt") == "my-weights.pt"            # own weights pass through
    assert models.qwen_repo("4b-fp8") == "Qwen/Qwen3-VL-4B-Instruct-FP8"
    assert models.qwen_repo("qwen3-vl-4b-instruct") == "Qwen/Qwen3-VL-4B-Instruct"
    assert models.qwen_repo("org/other-vl") == "org/other-vl"
    assert models.yoloe_label("yoloe-26s-seg.pt") == "YOLOE-26s"
    assert models.qwen_label("Qwen/Qwen3-VL-2B-Instruct-FP8") == "Qwen3-VL-2B-FP8"
    for bad in (lambda: models.yoloe_file("12s"), lambda: models.yoloe_file("yoloe-11s-seg-pf.pt"),
                lambda: models.qwen_repo("3b")):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_rule_occlusion_counts_hidden_base():
    door = {"x1": 600, "y1": 100, "x2": 1000, "y2": 600}       # door box cut off by boxes
    boxes = {"x1": 650, "y1": 500, "x2": 900, "y2": 900}
    assert covers_door_base(boxes, door)
    exits = [{"type": "exit", "label": "door", "bounding_box": door}]
    blocking = assess(exits, [{"type": "obstruction", "label": "box", "bounding_box": boxes}], W, H)
    assert len(blocking) == 1 and exits[0]["status"] != "clear"


def test_rule_ignores_far_objects():
    exits = [{"type": "exit", "label": "door", "bounding_box": {"x1": 600, "y1": 100, "x2": 1000, "y2": 900}}]
    far = {"type": "obstruction", "label": "chair", "bounding_box": {"x1": 50, "y1": 500, "x2": 200, "y2": 1000}}
    assert assess(exits, [far], W, H) == [] and exits[0]["status"] == "clear"


def test_double_door_leaves_merge():
    leaves = [{"box": [300, 100, 500, 900], "conf": 1, "signed": False, "weak": False},
              {"box": [505, 100, 700, 900], "conf": 1, "signed": False, "weak": False}]
    merged = merge_double_doors(leaves)
    assert len(merged) == 1 and merged[0]["box"] == [300, 100, 700, 900]


def test_locate_anything_answer():
    a = ("<ref>door</ref><box><327><72><720><886></box><ref>chair</ref><box><252><433><383><988></box>"
         "<box><538><511><709><999></box><ref>stool</ref><box>None</box>"
         "<ref>table</ref><box><723><725><280><999></box><|im_end|>")
    boxes = parse_answer(a)
    assert [b["label"] for b in boxes] == ["door", "chair", "chair"]   # broken table box dropped


def test_label_kinds():
    labels = (["door", "glass door"], ["exit sign"])
    assert kind_of("glass door", *labels) == "door"
    assert kind_of("exit sign", *labels) == "sign"
    assert kind_of("office chair", *labels) == "obstacle"



def test_expiry_date_formats():
    p = expiry.parse_date
    assert p("15/03/2027") == (date(2027, 3, 15), "day")
    assert p("03/15/2027", dayfirst=False) == (date(2027, 3, 15), "day")
    assert p("13/05/2027", dayfirst=False) == (date(2027, 5, 13), "day")   # only valid order
    assert p("2027-03-15") == (date(2027, 3, 15), "day")
    assert p("03/2027") == p("3/27") == p("MAR 2027") == (date(2027, 3, 31), "month")
    assert p("15 March 2027") == (date(2027, 3, 15), "day")
    assert p("2027") == (date(2027, 12, 31), "year")
    assert p("13/2027") is None and p("no date") is None
    assert p("14-5-2") is None and p("1/1/1850") is None                     # not plausible years


def test_expiry_answer_and_status():
    today = date(2026, 10, 17)
    a = expiry.parse_answer("DATE LAST_SERVICE 03/2025\nDATE MANUFACTURED 2019")
    r = expiry.assess(a["dates"], today)
    assert r["status"] == "expired" and r["due"] == date(2026, 3, 31)      # last service + 12 months
    a = expiry.parse_answer("DATE NEXT_SERVICE 11/2026")
    assert expiry.assess(a["dates"], today)["status"] == "valid"             # 44 days left
    assert expiry.assess(a["dates"], today, soon_days=60)["status"] == "expiring_soon"
    a = expiry.parse_answer("DATE NEXT_SERVICE 12/2030\nDATE MANUFACTURED 2016")
    assert expiry.assess(a["dates"], today)["due"] == date(2026, 12, 31)     # end of life first
    a = expiry.parse_answer("JUNE 2016 | HAS BEEN\nJUNE 2027 | HAS BEEN")       # words on the wrong date
    assert expiry.assess(a["dates"], today)["due"] == date(2027, 6, 30)     # future "last" = next
    a = expiry.parse_answer("DATE JUNE-2026 | ON\nDATE JUNE-2027 | NEXT RECOMMENDED SERVICING")
    assert [d["kind"] for d in a["dates"]] == ["LAST_SERVICE", "NEXT_SERVICE"]
    a = expiry.parse_answer("DATE UNKNOWN 05/2027")
    assert expiry.assess(a["dates"], today)["status"] == "unclear"
    assert expiry.parse_answer("NONE too_small") == {"dates": [], "none": "too_small"}
    assert expiry.parse_answer("I cannot see it")["none"] == "unreadable"


@contextlib.contextmanager
def _fast_reconnect():
    delay, video_io.RECONNECT_DELAY = video_io.RECONNECT_DELAY, 0.05
    try:
        yield
    finally:
        video_io.RECONNECT_DELAY = delay


def test_live_source_stream_mode():
    # a "file:" URL is not a local path, so it takes the webcam / stream code path;
    # at the end of the file the reader sees a dropped stream and reconnects
    with tempfile.TemporaryDirectory() as d, _fast_reconnect():
        path = Path(d) / "synth.avi"
        out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (320, 240))
        for i in range(60):
            out.write(np.full((240, 320, 3), i * 4, np.uint8))
        out.release()
        halt, log = threading.Event(), io.StringIO()
        with contextlib.redirect_stdout(log):
            src = video_io.LiveSource(f"file:{path}", halt=halt, timeout=5)
            try:
                assert (src.width, src.height, src.fps) == (320, 240, 25.0)
                last, deadline = -1, time.time() + 5
                while src.idx < 150 and time.time() < deadline:   # 60-frame file → reconnected
                    idx, frame, skipped = src.read()
                    assert idx == last + skipped + 1 and frame.shape == (240, 320, 3)
                    last = idx
                assert src.idx >= 150 and src.reconnects >= 2 and src.video_time() > 0
                halt.set()
                assert src.read() == (None, None, 0)
            finally:
                src.release()
        assert not src._thread.is_alive()
        assert "stream lost" in log.getvalue() and "stream back" in log.getvalue()


def test_live_source_no_stream():
    t, level = time.time(), cv2.utils.logging.getLogLevel()
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)   # one warning per attempt
    with _fast_reconnect(), contextlib.redirect_stdout(io.StringIO()):
        try:
            video_io.LiveSource("file:/nonexistent/none.avi", timeout=1)
        except RuntimeError as e:
            assert "No frames" in str(e)
        else:
            raise AssertionError("expected RuntimeError")
        finally:
            cv2.utils.logging.setLogLevel(level)
    assert time.time() - t < 3


def test_follow_box_moves_and_zooms():
    from apps.extinguisher_monitor import follow_box
    rng = np.random.default_rng(0)
    scene = cv2.GaussianBlur(rng.integers(0, 255, (480, 640), dtype=np.uint8), (5, 5), 0)
    A = cv2.getRotationMatrix2D((320, 240), 0, 1.1)       # walking closer: 10 % zoom …
    A[:, 2] += (12, -8)                                    # … and a small move
    nxt = cv2.warpAffine(scene, A, (640, 480))
    box = follow_box(scene, nxt, [500, 200, 700, 400])     # partly out of the frame
    want = [500 * 1.1 - 320 * 0.1 + 12, 200 * 1.1 - 240 * 0.1 - 8,
            700 * 1.1 - 320 * 0.1 + 12, 400 * 1.1 - 240 * 0.1 - 8]
    assert box is not None and max(abs(a - b) for a, b in zip(box, want)) < 3, box
    assert follow_box(scene, np.zeros_like(scene), [100, 100, 300, 300]) is None   # nothing to follow


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name} {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
