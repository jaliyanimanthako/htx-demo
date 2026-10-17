"""
All VLM prompts in one place.

  DETECTION_TASK + OUTPUT_FORMAT_*   full-frame exit/obstruction detection (Qwen runners)
  USER_TEXT                          per-frame instruction (repeats the format for small models)
  DEPTH_PROMPT                       close-up "floor or raised surface?" check
  NAME_PROMPT                        Qwen names objects at the door (for LocateAnything)
  CONFIRM_PROMPT / VERDICT_PROMPT    exit_monitor.py event questions

Answer formats are parsed by core/parsing.py — keep them in sync.
"""

# ── Task: exits and exit obstructions ─────────────────────────────────────────
DETECTION_TASK = """You are a fire-safety inspector reviewing a frame from a video walkthrough of a room or building interior.

TASK:
1. Find every EXIT visible in the frame.
   An exit is any way out of the room a person could use to leave:
   - doors (single, double, emergency/fire exit doors, push-bar doors, sliding doors)
   - open doorways and passage openings into a corridor or another room
   - stairwell entrances
   An illuminated or printed EXIT / running-man sign marks an exit — box the door or
   opening the sign refers to, not the sign itself.
   Do NOT count windows, cupboard/closet doors, cabinet doors, wall panels, mirrors,
   pictures or elevator doors as exits.

2. For each exit, look at its CLEARANCE ZONE: the doorway itself, the door leaf, and
   the floor directly in front of it (about one door-width deep and slightly wider
   than the door). Judge by POSITION and EFFECT, not by object type:
   ANY object that is not part of the building and sits in that zone so that it blocks
   or narrows the walking path, or would stop the door from opening fully, is an
   OBSTRUCTION — whatever it is. Do not assume a type; look at what is actually there.
   It can be, for example:
   - furniture: chairs, stools, tables, desks, sofas, shelves, cabinets, coat racks
   - equipment: machines, printers, ladders, tools, cleaning carts, mop buckets,
     vacuum cleaners, fans, heaters, water dispensers
   - wheeled items: trolleys, carts, pallet jacks, bicycles, scooters, parked wheelchairs
   - containers and materials: bins, bags, crates, pallets, barrels, boxes, planks,
     pipes, stacked goods, debris, rubbish
   - other: plants, sign stands, cones or barriers, hoses or cables across the floor
   - anything holding the door shut or blocking it: chains, padlocks, bars, wedges,
     ropes, items tied to or leaning against the door
   DEPTH CHECK — the picture is 2D, so an object that overlaps the door in the image
   may really be much closer to the camera. Before listing an object, decide what it
   physically RESTS ON and whether it is AT the door. Use these clues:
   - Look at the object's base: is there floor under it, or a desk / table / counter /
     shelf surface? An object on furniture is never a door obstruction.
   - Where does its base touch the floor? Compare with where the door meets the floor.
     If its base is clearly lower in the image (nearer the camera) with open floor
     between it and the door, it is in the foreground, not at the door.
   - Occlusion and size: an object at the door is about the scale you'd expect next to
     a door that size; a much bigger-looking object usually is closer to the camera.
   - An object leaning on, tied to, or hanging from the door counts as "door".
   Only list objects whose answer is "floor at the door" or "door".
   NOT obstructions: people walking through, a door that is simply closed, door
   hardware (handles, push bars, closers), signs or pictures on the wall, flat floor
   mats, and objects outside the clearance zone.

3. Box every OBSTRUCTION separately and name what it actually is (e.g. "office chair",
   "mop bucket", "bicycle"). Only report objects that obstruct an exit — ignore other
   items in the room.

Mark an exit "partially_obstructed" if the path is narrowed but a person can still pass,
and "obstructed" if passage is blocked or the door cannot open. If you cannot tell,
prefer "partially_obstructed" over "clear".
Be consistent: an exit with any obstruction listed is never "clear", and a "clear"
exit has no obstructions listed."""


# ── Output formats (parsed by core/parsing.py) ────────────────────────────────
# COMPACT (default): one short text line per item. Answer length is what drives
# latency (each JSON item costs ~45 tokens, a compact line ~18), so this is
# several times faster on a self-hosted GPU.
OUTPUT_FORMAT_COMPACT = """
RULES:
1. ONLY report exits and obstructions clearly visible in this frame. Do NOT hallucinate.
2. One line per exit, then one line per obstructing object. A stack or pile of the same
   items is ONE line with one box around the whole pile. At most 6 obstruction lines.
3. If you cannot localise an item, leave it out — do NOT guess.

OUTPUT FORMAT — plain text lines only. No JSON, no markdown, no explanation.
Exit line:         E <status> <x1> <y1> <x2> <y2>
  status:          C = clear, P = partially obstructed, B = obstructed (blocked)
Obstruction line:  O <n> <rests_on> <label> <x1> <y1> <x2> <y2>
  n:               which exit it blocks (1 = the first E line, 2 = the second, ...)
  rests_on:        ONE word — what the object physically stands on:
                   floor (on the floor at the door), door (touching / on the door),
                   or the surface it is really on: desk, table, counter, shelf, cabinet
  label:           what the blocking object actually is, 1-2 words
Coordinates: integers 0-1000 relative to the image, (0,0) = top-left,
(1000,1000) = bottom-right. Exit boxes cover the full door / opening, floor to top.
If no exit is visible, output exactly: NONE

Example (format only — the objects and numbers are not from this image):
E B 120 210 395 930
O 1 floor trolley 140 600 330 935
O 1 door ladder 300 300 420 930
E C 610 250 780 900"""

# JSON: original verbose format (kept for comparison / other providers)
OUTPUT_FORMAT_JSON = """
RULES:
1. ONLY report exits and obstructions clearly visible in this frame. Do NOT hallucinate.
2. One entry per exit and one entry per obstructing object.
3. If no exit is visible, return an empty "detections" list.
4. Keep every "label" to at most 4 words. Output compact single-line JSON (no indentation).

BOUNDING BOX RULE:
- For every detection include a "bbox" field with normalised coordinates
  in the format [x1, y1, x2, y2] where each value is an integer between 0 and 999.
  (0,0) is the top-left corner; (999,999) is the bottom-right corner.
- Exit boxes cover the full door / opening frame from floor to top.
- If you cannot localise an item to a specific region, omit it — do NOT guess.

IMPORTANT: Your response must be ONLY the JSON object. No preamble, no explanation, no markdown fences.
Start your response with '{' and end with '}'.

Fields:
- exit:        type, label (e.g. "fire exit door", "open doorway"), status (EXACTLY one of:
               clear, partially_obstructed, obstructed), bbox, confidence
- obstruction: type, label (the blocking object, e.g. "stacked boxes"), blocks_exit (label
               of the exit it blocks), bbox, confidence

Respond with valid JSON on ONE line, exactly in this shape:
{"detections":[{"type":"exit","label":"fire exit door","status":"partially_obstructed","bbox":[360,144,664,788],"confidence":0.9},{"type":"obstruction","label":"stacked boxes","blocks_exit":"fire exit door","bbox":[411,584,583,788],"confidence":0.8}]}"""

OUTPUT_FORMATS = {"compact": OUTPUT_FORMAT_COMPACT, "json": OUTPUT_FORMAT_JSON}


def build_system_prompt(task: str = DETECTION_TASK, fmt: str = "compact") -> str:
    return f"{task.strip()}\n{OUTPUT_FORMATS[fmt]}"


USER_TEXT = ("Detect every exit in this frame, whether each is obstructed, and any obstructing "
             "objects. Answer ONLY with the plain text lines from the OUTPUT FORMAT "
             "(E ... / O ... / NONE) - no JSON, no markdown, no other text.")


NAME_PROMPT = (
    "Look at the exit door(s) in this image. List every separate object that is AT the "
    "door: on the floor in front of it, against it, or in the doorway. Ignore the door "
    "itself, walls, signs, and things on desks or shelves. Answer with a comma-separated "
    "list of short object names (1-2 words each), or NONE.")


CONFIRM_PROMPT = ("Is the thing in the middle of this image a door or opening that a person "
                  "could walk through to leave the room (not a cupboard, window, wall panel, "
                  "glass partition or pillar)? Answer yes or no.")


# "furniture" made chairs/tables ("it IS furniture") and stacked boxes ("on top of
# boxes") come back wrong → ask about a raised surface and say stacks count as floor
DEPTH_PROMPT = ("Look at the {label} in the middle of this image. What is directly underneath "
                "it - the floor, or a raised surface such as a desk, table, counter or shelf? "
                "Objects standing on the floor, and items stacked on other items that stand on "
                "the floor, count as floor. Answer with one word: floor or raised.")


VERDICT_PROMPT = ("Look at the door in the middle of this image. Is anything on the floor in "
                  "front of it, in the doorway or against it that blocks or narrows the way "
                  "through? Ignore things on desks or far in front. Answer with one letter: "
                  "C = clear, P = partially blocked (a person can still pass), B = blocked.")


# ── Fire-extinguisher mode (extinguisher_monitor.py; parsed by core/expiry.py) ──
# Two steps: find the dated sticker / tag on the extinguisher crop, then read a tight,
# upscaled crop of it. Qwen only COPIES the date and the words next to it; the meaning
# (next service, last service, expiry, …) is decided in code (core/expiry.py) — a 4B
# model mixes up "next" and "last" when asked to interpret.
EXTINGUISHER_SYSTEM = "You read dates on fire-extinguisher labels and tags. Never guess digits."

EXTINGUISHER_LOCATE_PROMPT = (
    "Find every label, sticker or tag on this fire extinguisher that has a handwritten or "
    "printed DATE on it (service / inspection / expiry sticker or tag). Ignore the printed "
    "instructions. Answer one line per sticker: x1 y1 x2 y2 on a 0-1000 scale of this "
    "image. If there is none, answer NONE.")

EXTINGUISHER_PROMPT = (
    "Read this service / inspection sticker of a fire extinguisher.\n"
    "For every date on it, output one line:\n"
    "DATE <date exactly as written> | <the words printed just before or above that date>\n"
    "Copy the words, do not interpret them. Dates may be handwritten.\n"
    "If there is no readable date, output exactly: NONE <too_small|blurry>\n"
    "Never guess digits. No other text.")
