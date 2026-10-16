"""
FastAPI app
===========

  GET    /api/health                         no auth — liveness + whether a token is required
  GET    /api/info                           model servers up/down, videos, sessions
  GET    /api/videos                         video files a session can play
  GET    /api/sessions                       all sessions
  POST   /api/sessions                       start one  {source, loop, use_yoloe, use_locate, use_qwen, options}
  GET    /api/sessions/{id}                  status + latest state (exits, events)
  DELETE /api/sessions/{id}                  stop it (report is written)
  GET    /api/sessions/{id}/stream/{name}    MJPEG: original | annotated | combined
  GET    /api/sessions/{id}/report           report.json once the session has ended
  WS     /api/sessions/{id}/events           pushes {"type": "event" | "state" | "end", ...}
  WS     /api/live                           LIVE MODE: browser sends frames, gets JSON boxes
                                             (see backend/live.py for the message format)
  POST   /api/sessions/{id}/command          chat / command box — not implemented yet (501)
  GET    /                                   the web app (frontend/), for convenience

Auth: if BACKEND_TOKEN is set, send "Authorization: Bearer <token>" or ?token=<token>
(query form needed for <img> streams and WebSockets).
"""

import asyncio
import json
import secrets
import traceback
import urllib.request
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import settings
from backend.sessions import STREAMS, SessionError, SessionManager, list_videos

app = FastAPI(title="Exit Monitor backend", version="1.0")


@app.middleware("http")
async def report_errors(request: Request, call_next):
    """Unexpected errors → JSON {"detail": "<Type>: <message>"} the web app can show,
    full traceback in the backend log. Registered before CORS so the error response
    still carries CORS headers (otherwise the browser hides it)."""
    try:
        return await call_next(request)
    except Exception as e:
        print(f"  ERROR {request.method} {request.url.path}:", flush=True)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"detail": f"{type(e).__name__}: {e}"})


app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])
manager = SessionManager()


# ── auth ──────────────────────────────────────────────────────────────────────

def _token_ok(token: Optional[str]) -> bool:
    return not settings.TOKEN or (token is not None and secrets.compare_digest(token, settings.TOKEN))


def require_token(request: Request):
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token")
    if not _token_ok(token):
        raise HTTPException(401, "missing or wrong token")


def _session_or_404(sid: str):
    s = manager.get(sid)
    if s is None:
        raise HTTPException(404, f"no session {sid}")
    return s


def _up(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:
        return False


# ── API ───────────────────────────────────────────────────────────────────────

class SessionRequest(BaseModel):
    source: str = Field(..., description="video file name in the video folder, or rtsp:// URL")
    loop: bool = True
    use_yoloe: bool = True
    use_locate: bool = True
    use_qwen: bool = True
    options: dict = Field(default_factory=dict,
                          description="hold, la_interval, name_every, recheck, confirm_timeout")


class CommandRequest(BaseModel):
    text: str


@app.get("/api/health")
def health():
    return {"ok": True, "auth_required": bool(settings.TOKEN)}


@app.get("/api/info", dependencies=[Depends(require_token)])
def info():
    return {"models": {"locate_anything": {"url_set": bool(settings.LOCATE_URL),
                                           "up": _up(settings.LOCATE_URL + "/health")},
                       "qwen": {"url_set": bool(settings.QWEN_URL),
                                "up": _up(settings.QWEN_URL + "/models")}},
            "videos": list_videos(), "max_sessions": settings.MAX_SESSIONS,
            "sessions": [s.info() for s in manager.sessions.values()],
            "live_sessions": [{"id": l.id, "frames": l.frames} for l in manager.live.values()]}


@app.get("/api/videos", dependencies=[Depends(require_token)])
def videos():
    return {"videos": list_videos()}


@app.get("/api/sessions", dependencies=[Depends(require_token)])
def sessions():
    return {"sessions": [s.info() for s in manager.sessions.values()]}


@app.post("/api/sessions", dependencies=[Depends(require_token)], status_code=201)
def create_session(req: SessionRequest):
    try:
        s = manager.create(req.source, req.loop, req.use_yoloe, req.use_locate, req.use_qwen,
                           req.options)
    except SessionError as e:
        raise HTTPException(409 if "already running" in str(e) else 400, str(e))
    return s.info()


@app.get("/api/sessions/{sid}", dependencies=[Depends(require_token)])
def get_session(sid: str):
    return _session_or_404(sid).info()


@app.delete("/api/sessions/{sid}", dependencies=[Depends(require_token)])
def stop_session(sid: str):
    s = _session_or_404(sid)
    s.stop()
    return s.info()


@app.get("/api/sessions/{sid}/report", dependencies=[Depends(require_token)])
def get_report(sid: str):
    s = _session_or_404(sid)
    path = s.monitor.report_path if s.monitor else None
    if not path:
        raise HTTPException(409, "report is written when the session ends")
    report = json.loads(path.read_text())
    report["source"] = s.source_name           # don't expose server paths to clients
    return report


@app.post("/api/sessions/{sid}/command", dependencies=[Depends(require_token)], status_code=501)
def command(sid: str, req: CommandRequest):
    _session_or_404(sid)
    return {"detail": "chat / commands are not implemented yet", "received": req.text}


@app.get("/api/sessions/{sid}/stream/{name}", dependencies=[Depends(require_token)])
async def stream(sid: str, name: str):
    s = _session_or_404(sid)
    if name not in STREAMS:
        raise HTTPException(404, f"stream must be one of {', '.join(STREAMS)}")

    async def frames():
        last = -1
        while True:
            jpeg, seq = s.frame(name)
            if jpeg is not None and seq != last:
                last = seq
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
            elif s.done:
                break
            else:
                await asyncio.sleep(0.02)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-cache"})


@app.websocket("/api/sessions/{sid}/events")
async def events(ws: WebSocket, sid: str, token: Optional[str] = None):
    if not _token_ok(token):
        await ws.close(code=4401)
        return
    s = manager.get(sid)
    if s is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    sent, last_state = 0, 0.0
    try:
        await ws.send_json({"type": "hello", "session": s.info()})
        while True:
            for ev in s.events_since(sent):
                await ws.send_json({"type": "event", "event": ev})
                sent += 1
            now = asyncio.get_running_loop().time()
            if now - last_state >= 0.5:
                info = s.info()
                await ws.send_json({"type": "state", "status": info["status"],
                                    "state": info["state"]})
                last_state = now
            if s.done:
                await ws.send_json({"type": "end", "session": s.info()})
                break
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.websocket("/api/live")
async def live(ws: WebSocket, token: Optional[str] = None):
    """
    1. client → {"type": "start", "use_yoloe"?, "use_locate"?, "use_qwen"?, "options"?}
       server → {"type": "ready", "id": ...}          (or {"type": "error", "detail": ...})
    2. client → binary frame (uint32 seq | float64 video time | JPEG), one at a time
       server → {"type": "result", "seq", "t", "exits": [...], "obstacles": [...],
                 "events": [...new...], "proc_ms", ...}   boxes are 0..1 of the frame
    3. client → {"type": "reset"} (new video) or {"type": "stop"}
    """
    if not _token_ok(token):
        await ws.close(code=4401)
        return
    await ws.accept()
    session = None
    try:
        start = await ws.receive_json()
        if start.get("type") != "start":
            await ws.send_json({"type": "error", "detail": "first message must be {type: start}"})
            return
        try:
            session = await asyncio.to_thread(
                manager.create_live, start.get("use_yoloe", True), start.get("use_locate", True),
                start.get("use_qwen", True), start.get("options") or {})
        except Exception as e:
            await ws.send_json({"type": "error", "detail": f"{type(e).__name__}: {e}"})
            return
        await ws.send_json({"type": "ready", "id": session.id,
                            "models": {"yoloe": not session.args.no_yoloe,
                                       "locate_anything": bool(session.args.locate_url),
                                       "qwen": bool(session.args.qwen_url)},
                            "skipped": session.skipped})
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                try:
                    result = await asyncio.to_thread(session.process, msg["bytes"])
                except ValueError as e:
                    result = {"type": "error", "detail": str(e)}
                await ws.send_json(result)
            elif msg.get("text"):
                cmd = json.loads(msg["text"]).get("type")
                if cmd == "reset":
                    await asyncio.to_thread(session.reset)
                    await ws.send_json({"type": "reset_done"})
                elif cmd == "stop":
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        traceback.print_exc()
        try:
            await ws.send_json({"type": "error", "detail": f"{type(e).__name__}: {e}"})
        except Exception:
            pass
    finally:
        if session:
            await asyncio.to_thread(manager.close_live, session)
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.on_event("shutdown")
def _shutdown():
    manager.stop_all()


# ── web app (optional: the frontend can also be opened from anywhere) ─────────
if settings.FRONTEND_DIR.is_dir():
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(settings.FRONTEND_DIR / "index.html")

    app.mount("/", StaticFiles(directory=settings.FRONTEND_DIR), name="frontend")
