"""
The link to the phone.

Serves the headset page (vr/web) over https, because phones only hand out their camera,
microphone and motion sensors to secure pages, and then keeps one WebSocket open to it:

  phone -> Mac   head orientation, hand joints, microphone sound
  Mac -> phone   the picture for both eyes, the headset HUD, ORCA's voice

It makes its own certificate the first time, so the phone shows a warning once: choose
"Advanced" / "Show details", then "Proceed" / "Visit this website".
"""
import asyncio
import json
import os
import socket
import ssl
import struct
import subprocess
import threading
import time

import config

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
CERT_DIR = os.path.join(HERE, "cert")
FRAME, AUDIO = 1, 2                 # first byte of a binary message to the phone


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))        # nothing is sent: this only asks which network card would be used
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def certificate(ip):
    """A self-signed certificate for this Mac's address, made once and remade if the address changes."""
    cert, key, made_for = (os.path.join(CERT_DIR, n) for n in ("cert.pem", "key.pem", "address.txt"))
    try:
        with open(made_for) as f:
            fresh = f.read().strip() == ip and os.path.exists(cert) and os.path.exists(key)
    except OSError:
        fresh = False
    if not fresh:
        os.makedirs(CERT_DIR, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                        "-keyout", key, "-out", cert, "-subj", "/CN=ROV-6 ORCA",
                        "-addext", f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost"],
                       check=True, capture_output=True)
        with open(made_for, "w") as f:
            f.write(ip)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


class Server:
    def __init__(self, session):
        self.session = session
        self.url = self.error = ""
        self.connected = False
        self.fps = 0.0                          # pictures per second actually sent
        self.quality = float(config.VR_JPEG_QUALITY[1])
        self._loop = self._ws = None
        self._frame = None
        self._frame_ready = threading.Event()
        self._in_flight = 0
        self._last_sent = time.time()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._encode, daemon=True).start()

    # ---------- the web server (its own thread) ----------
    def _run(self):
        try:
            from aiohttp import web
        except ImportError:
            self.error = "aiohttp is missing: pip install -r requirements.txt"
            print(self.error)
            return
        ip = lan_ip()
        try:
            context = certificate(ip)
        except Exception as e:
            self.error = f"couldn't make an https certificate ({e})"
            print(self.error)
            return

        @web.middleware
        async def fresh(request, handler):          # the phone must never run yesterday's page
            response = await handler(request)
            if not request.path.startswith("/vendor/"):
                response.headers["Cache-Control"] = "no-store"
            return response

        async def index(request):
            return web.FileResponse(os.path.join(WEB, "index.html"))

        app = web.Application(middlewares=[fresh])
        app.router.add_get("/", index)
        app.router.add_get("/ws", self._socket)
        app.router.add_static("/", WEB)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            runner = web.AppRunner(app, access_log=None)
            self._loop.run_until_complete(runner.setup())
            self._loop.run_until_complete(web.TCPSite(runner, "0.0.0.0", config.VR_PORT, ssl_context=context).start())
        except OSError as e:
            self.error = f"port {config.VR_PORT} is busy ({e})"
            print(self.error)
            return
        self.url = f"https://{ip}:{config.VR_PORT}"
        print("Phone VR: open", self.url, "on the phone (same Wi-Fi), accept the certificate warning once.")
        self._loop.run_forever()

    async def _socket(self, request):
        from aiohttp import WSMsgType, web
        ws = web.WebSocketResponse(heartbeat=8, max_msg_size=4 * 2 ** 20)
        await ws.prepare(request)
        old, self._ws = self._ws, ws            # one headset at a time: the newest wins
        if old is not None:
            await old.close()
        self._in_flight = 0
        self.connected = True
        self.session.on_connect(request.remote)
        await ws.send_json(self.session.client_config())
        try:
            async for msg in ws:
                try:
                    if msg.type == WSMsgType.TEXT:
                        self.session.on_message(json.loads(msg.data))
                    elif msg.type == WSMsgType.BINARY:
                        self.session.on_audio(msg.data)
                except Exception as e:
                    print("Phone message error:", repr(e))
        finally:
            if self._ws is ws:
                self._ws, self.connected = None, False
                self.session.on_disconnect()
        return ws

    # ---------- sending (called from other threads) ----------
    def send(self, message):
        self._post(json.dumps(message))

    def send_audio(self, clip, data):
        self._post(struct.pack("<BxxxI", AUDIO, clip) + data)

    def _post(self, data, frame=False):
        if self._loop is not None and self._ws is not None:
            self._loop.call_soon_threadsafe(self._write, data, frame)

    def _write(self, data, frame):
        ws = self._ws
        if ws is None or ws.closed:
            return
        task = asyncio.ensure_future(ws.send_str(data) if isinstance(data, str) else ws.send_bytes(data))
        if frame:
            self._in_flight += 1
        task.add_done_callback(lambda t: self._written(t, frame))

    def _written(self, task, frame):
        if frame:
            self._in_flight = max(0, self._in_flight - 1)
        task.exception()                        # a phone that just left is not an error worth printing

    # ---------- the picture ----------
    def send_frame(self, camx, camy, raw, size):
        """Both eyes side by side as raw RGB. Returns at once: squeezing and sending happen elsewhere."""
        if self.connected:
            self._frame = (camx, camy, raw, size)
            self._frame_ready.set()

    def _encode(self):
        import cv2
        import numpy as np
        low, high = config.VR_JPEG_QUALITY
        while True:
            self._frame_ready.wait()
            self._frame_ready.clear()
            if self._in_flight >= 2:
                # the Wi-Fi is behind: skip this picture and make the next ones lighter. An old
                # picture arriving late is worse in a headset than a slightly soft one on time.
                self.quality = max(low, self.quality - 4)
                continue
            camx, camy, raw, (w, h) = self._frame
            rgb = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
            ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                                   [cv2.IMWRITE_JPEG_QUALITY, int(self.quality)])
            if not ok:
                continue
            self._post(struct.pack("<Bxxxff", FRAME, camx, camy) + jpg.tobytes(), frame=True)
            self.quality = min(high, self.quality + 0.2)
            now = time.time()
            self.fps = 0.9 * self.fps + 0.1 / max(now - self._last_sent, 1e-3)
            self._last_sent = now
