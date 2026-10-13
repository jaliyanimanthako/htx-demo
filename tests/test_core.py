"""
Offline tests for the core logic (no GPU, no network):

    python tests/test_core.py        # or: python -m pytest tests
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
