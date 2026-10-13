"""
LocateAnything category lists and helpers to interpret its labelled boxes.
"""

DOOR_LABELS = ["door", "glass door", "doorway"]   # any label containing "door" is a door


SIGN_LABELS = ["exit sign"]


OBSTACLE_LABELS = [
    "chair", "stool", "table", "cardboard box", "crate", "trolley", "cart", "trash bin",
    "bag", "plant", "ladder", "bicycle", "bucket", "equipment",
]   # "cable" left out: mostly fires on desk cables; add with --obstacles if needed


def norm_box_to_pixels(b: dict, W: int, H: int) -> dict:
    """0-1000 normalised server box → pixel bounding_box dict."""
    return {"x1": int(b["x1"] / 1000 * W), "y1": int(b["y1"] / 1000 * H),
            "x2": int(b["x2"] / 1000 * W), "y2": int(b["y2"] / 1000 * H)}


def kind_of(label: str, door_labels: list, sign_labels: list) -> str:
    lab = label.lower()
    if any(s in lab for s in sign_labels):
        return "sign"
    if "door" in lab or any(d in lab for d in door_labels):
        return "door"
    return "obstacle"
