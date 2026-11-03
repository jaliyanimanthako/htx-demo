"""Bare webcam check: open a camera with plain OpenCV and show it. No models, no project code.

Usage:
  conda run -n htx python tests/webcam_check.py [index]    # default index 0
  q / Esc quits.
"""

import glob
import os
import sys
import time

import cv2

index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

devices = sorted(glob.glob("/dev/video*"))
print("video devices:", devices or "none (attach the camera with usbipd-win first)")

dev = f"/dev/video{index}"
if os.path.exists(dev) and not os.access(dev, os.R_OK | os.W_OK):
    sys.exit(f"no permission for {dev}: run  sudo usermod -aG video $USER  then restart the WSL shell")

cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
if not cap.isOpened():
    sys.exit(f"could not open camera {index}")

print(f"opened camera {index}: "
      f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} "
      f"@ {cap.get(cv2.CAP_PROP_FPS):.0f} fps (reported)")

n, t0, fps = 0, time.time(), 0.0
while True:
    ok, frame = cap.read()
    if not ok:
        print("read failed")
        break
    n += 1
    dt = time.time() - t0
    if dt >= 1.0:
        fps, n, t0 = n / dt, 0, time.time()
    h, w = frame.shape[:2]
    cv2.putText(frame, f"{w}x{h}  {fps:.1f} fps", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.imshow("webcam check", frame)
    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
        break

cap.release()
cv2.destroyAllWindows()
