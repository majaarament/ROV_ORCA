// ROV-6 · ORCA: the headset page.
//
// The phone is the headset, the Mac is the robot. This page
//   shows    the picture the Mac draws for each eye, re-aimed at 60 Hz to wherever the head points now
//   sends    head orientation, the joints of the hand its rear camera sees, and microphone sound
//   plays    ORCA's voice, and shows the small HUD (once per eye)
// The camera picture never leaves the phone: only the 21 joints of each hand are sent.

const $ = (id) => document.getElementById(id);
const DEBUG = new URLSearchParams(location.search).has("debug");
const CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21";
const RAD = Math.PI / 180;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const wrap = (deg) => ((((deg + 180) % 360) + 360) % 360) - 180;

let cfg = null;                 // the Mac's settings, sent when we connect
let ws = null;
let started = false;            // START VR EXPERIENCE has been pressed
let perms = {};
let state = { phase: "waiting", yaw0: 0, pitch0: 0, hud: {} };
let audio = null;

// ====================================================================== link to the Mac
function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { if (started) sendStart(); };
  ws.onmessage = (e) => (typeof e.data === "string" ? onMessage(JSON.parse(e.data)) : onBinary(e.data));
  ws.onclose = () => { ready(false); setTimeout(connect, 1000); };
}
const send = (m) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };
const sendStart = () => send({ t: "start", perms, ua: navigator.userAgent, debug: DEBUG });
const report = (what, error) => {
  console.warn(what, error);
  send({ t: "error", what, text: `${what}: ${(error && error.message) || error}` });
};

function onMessage(m) {
  if (m.t === "config") { cfg = m; ready(true); }
  else if (m.t === "state") {
    state = m;
    $("hud").style.setProperty("--shift", `${(m.nudge || 0) * 50}vw`);     // the HUD moves with the pictures
    drawHud(m.hud);
    if (DEBUG) readout(m);
  }
}

function onBinary(buffer) {
  const kind = new Uint8Array(buffer, 0, 1)[0];
  const view = new DataView(buffer);
  if (kind === 1) showFrame(view.getFloat32(4, true), view.getFloat32(8, true), buffer.slice(12));
  else if (kind === 2) speak(view.getUint32(4, true), buffer.slice(8));
}

function ready(yes) {
  const b = $("start");
  if (started) return;
  b.disabled = !yes;
  b.textContent = yes ? (DEBUG ? "START DEBUG VIEW" : "START VR EXPERIENCE") : "CONNECTING...";
}

// ====================================================================== head: the orientation sensors
// yaw to the right, pitch up and roll anticlockwise are positive, in degrees.
const head = { y: 0, p: 0, r: 0, sensor: false, t: 0 };
const trail = [];               // where the head pointed over the last half second

function screenAngle() {
  if (screen.orientation && typeof screen.orientation.angle === "number") return screen.orientation.angle;
  return ((window.orientation || 0) + 360) % 360;
}

// The sensors give three angles (alpha, beta, gamma) that turn the earth's axes into the phone's.
// From them: which way the back of the phone (and so the wearer) faces, and how the screen is tilted.
export function orientation(alpha, beta, gamma, angle) {
  const [cA, sA, cB, sB, cG, sG] = [Math.cos(alpha * RAD), Math.sin(alpha * RAD), Math.cos(beta * RAD),
    Math.sin(beta * RAD), Math.cos(gamma * RAD), Math.sin(gamma * RAD)];
  // the direction the rear camera looks, as (east, north, up)
  const east = -(cG * sA * sB + cA * sG), north = -(sA * sG - cA * cG * sB), up = -(cB * cG);
  // how far the screen's own "right" and "up" point at the sky, for the way the phone is being held
  const [c, s] = [Math.cos(angle * RAD), Math.sin(angle * RAD)];
  const rightUp = c * (-cB * sG) - s * sB, upUp = s * (-cB * sG) + c * sB;
  return { y: Math.atan2(east, north) / RAD, p: Math.asin(clamp(up, -1, 1)) / RAD, r: Math.atan2(rightUp, upUp) / RAD };
}

function onOrientation(e) {
  if (e.alpha === null || e.beta === null || e.gamma === null || !cfg) return;
  const o = orientation(e.alpha, e.beta, e.gamma, screenAngle());
  head.y = o.y * cfg.signs[0]; head.p = o.p * cfg.signs[1]; head.r = o.r * cfg.signs[2];
  head.sensor = true;
}

function headTick(now) {
  if (!cfg) return;
  if (head.y !== head.sy || head.p !== head.sp || head.r !== head.sr || now - head.t > 200) {
    send({ t: "head", y: +head.y.toFixed(2), p: +head.p.toFixed(2), r: +head.r.toFixed(2), ts: now });
    [head.sy, head.sp, head.sr, head.t] = [head.y, head.p, head.r, now];
  }
  trail.push([now, head.y, head.p, head.r]);
  while (trail.length > 40) trail.shift();
}

// where the head pointed a moment ago: the camera picture is always a little old
function headAt(time) {
  for (let i = trail.length - 1; i >= 0; i--) if (trail[i][0] <= time) return trail[i].slice(1);
  return [head.y, head.p, head.r];
}

// no motion sensor (a laptop, or permission refused): dragging looks around instead
function dragToLook(el) {
  let last = null;
  el.addEventListener("pointerdown", (e) => { last = [e.clientX, e.clientY]; el.setPointerCapture(e.pointerId); });
  el.addEventListener("pointerup", () => { last = null; });
  el.addEventListener("pointermove", (e) => {
    if (!last || head.sensor) return;
    head.y = wrap(head.y - (e.clientX - last[0]) * 0.15);
    head.p = clamp(head.p + (e.clientY - last[1]) * 0.15, -80, 80);
    last = [e.clientX, e.clientY];
  });
}

// the same sum the Mac does: head direction -> which part of the sea is in view
function cameraFor(yaw, pitch) {
  return [clamp(cfg.cx0 + wrap(yaw - state.yaw0) * cfg.ppd, cfg.xmin, cfg.xmax),
    clamp(cfg.cy0 - (pitch - state.pitch0) * cfg.ppd, cfg.ymin, cfg.ymax)];
}

// ====================================================================== the picture, once per eye
const VERTEX = `attribute vec2 a; varying vec2 v;
void main() { v = vec2(a.x * 0.5 + 0.5, 0.5 - a.y * 0.5); gl_Position = vec4(a, 0.0, 1.0); }`;
// For every pixel of the screen: which eye it belongs to, and which point of that eye's picture it shows.
// On the way it bulges the picture to cancel the viewer's lenses, keeps the horizon level when the head
// tilts, and slides the picture by however far the head has turned since the Mac drew it.
const FRAGMENT = `precision highp float;
uniform sampler2D tex; uniform vec2 res, shift, lens; uniform float roll, zoom, aspect, mono, nudge;
varying vec2 v;
void main() {
  float eye = mono > 0.5 ? 0.0 : step(0.5, v.x);
  float eyes = mono > 0.5 ? 1.0 : 2.0;
  vec2 p = vec2((v.x - 0.5 * eye) * eyes, v.y) - 0.5;
  p.x += nudge * (2.0 * eye - 1.0);      // each picture slid towards the nose, so its middle sits behind its lens
  float shape = res.x / eyes / res.y;
  vec2 q = vec2(p.x * shape, p.y);
  float r2 = dot(q, q) * 4.0;
  q *= 1.0 + lens.x * r2 + lens.y * r2 * r2;
  q = vec2(q.x * cos(roll) + q.y * sin(roll), -q.x * sin(roll) + q.y * cos(roll));
  float tall = zoom * max(1.0, shape / aspect);
  vec2 uv = vec2(q.x / (tall * aspect), q.y / tall) + 0.5 + shift;
  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) { gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }
  gl_FragColor = texture2D(tex, vec2((uv.x + eye) * 0.5, uv.y));
}`;

const canvas = $("view");
const gl = canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "high-performance" });
const uniforms = {};
const frame = { camx: 0, camy: 0, have: false, at: 0, busy: false, next: null, count: 0 };

function setupGl() {
  const program = gl.createProgram();
  for (const [type, source] of [[gl.VERTEX_SHADER, VERTEX], [gl.FRAGMENT_SHADER, FRAGMENT]]) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    gl.attachShader(program, shader);
  }
  gl.linkProgram(program);
  gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const a = gl.getAttribLocation(program, "a");
  gl.enableVertexAttribArray(a);
  gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
  gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
  for (const [k, val] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
    [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, val);
  for (const name of ["res", "shift", "lens", "roll", "zoom", "aspect", "mono", "nudge"]) uniforms[name] = gl.getUniformLocation(program, name);
}

// a new picture from the Mac. Unpacking takes a few ms: if another arrives meanwhile, only the newest is kept.
function showFrame(camx, camy, jpeg) {
  if (frame.busy) { frame.next = [camx, camy, jpeg]; return; }
  frame.busy = true;
  createImageBitmap(new Blob([jpeg], { type: "image/jpeg" })).then((bitmap) => {
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, bitmap);
    bitmap.close();
    Object.assign(frame, { camx, camy, have: true, at: performance.now(), count: frame.count + 1 });
  }).catch((e) => console.warn("picture", e)).finally(() => {
    frame.busy = false;
    const next = frame.next;
    frame.next = null;
    if (next) showFrame(...next);
  });
}

function draw(now) {
  requestAnimationFrame(draw);
  headTick(now);
  if (!started || !cfg) return;
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  const [w, h] = [Math.round(canvas.clientWidth * scale), Math.round(canvas.clientHeight * scale)];
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  gl.viewport(0, 0, w, h);
  if (!frame.have) { gl.clearColor(0, 0.04, 0.07, 1); gl.clear(gl.COLOR_BUFFER_BIT); return; }
  // how far has the head turned since this picture was drawn? Slide it by that much, now, without
  // waiting for the next one: this is what keeps head movement smooth over Wi-Fi.
  const [camx, camy] = cameraFor(head.y, head.p);
  gl.uniform2f(uniforms.shift, clamp((camx - frame.camx) / cfg.w, -0.3, 0.3), clamp((camy - frame.camy) / cfg.h, -0.3, 0.3));
  gl.uniform1f(uniforms.roll, clamp(head.r, -cfg.rollLimit, cfg.rollLimit) * RAD);
  gl.uniform2f(uniforms.res, w, h);
  gl.uniform2f(uniforms.lens, DEBUG ? 0 : cfg.lens[0], DEBUG ? 0 : cfg.lens[1]);
  gl.uniform1f(uniforms.zoom, DEBUG ? 1 : cfg.overscan);
  gl.uniform1f(uniforms.aspect, cfg.w / cfg.h);
  gl.uniform1f(uniforms.mono, DEBUG ? 1 : 0);
  gl.uniform1f(uniforms.nudge, DEBUG ? 0 : state.nudge || 0);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

// ====================================================================== the HUD, once per eye
const eyes = [];
function buildHud() {
  for (const side of ["left", "right"]) {
    const eye = document.createElement("div");
    eye.className = `eye ${side}`;
    eye.innerHTML = `<div class="in">
      <div class="orca">ORCA<span class="dot"></span></div>
      <div class="card" hidden><div class="steps"></div><div class="title"></div><div class="sub"></div></div>
      <div class="flash" hidden></div>
      <div class="warn" hidden><span></span><small></small></div>
      <div class="subtitle" hidden><span></span></div>
      <div class="chips"><span class="chip"></span> <span class="hold"></span></div></div>`;
    $("hud").appendChild(eye);
    eyes.push(eye);
  }
}

function drawHud(hud) {
  if (!hud) return;
  for (const eye of eyes) {
    const q = (s) => eye.querySelector(s);
    q(".dot").className = `dot ${hud.orca || ""}`;
    q(".card").hidden = !hud.card;
    if (hud.card) {
      q(".card").classList.toggle("ok", !!hud.card.ok);
      q(".steps").textContent = hud.card.step <= hud.card.of ? `STEP ${hud.card.step} OF ${hud.card.of}` : "";
      q(".title").textContent = hud.card.title;
      q(".sub").textContent = hud.card.sub;
    }
    // one thing at a time in the middle of the view: a warning beats a passing message
    q(".warn").hidden = !hud.warn || !!hud.card;
    if (hud.warn) { q(".warn span").textContent = hud.warn[0]; q(".warn small").textContent = hud.warn[1]; }
    q(".flash").hidden = !hud.flash || !!hud.card;
    if (hud.flash) { q(".flash").textContent = hud.flash[0]; q(".flash").style.color = `rgb(${hud.flash[1].join(",")})`; }
    q(".subtitle").hidden = !hud.sub;
    q(".subtitle span").textContent = hud.sub || "";
    q(".chip").textContent = hud.chip || "";
    q(".hold").textContent = hud.hold ? `HOLDING · ${hud.hold}` : "";
  }
}

// ====================================================================== hand: rear camera -> MediaPipe -> joints
const video = $("cam");
const hands = { fps: 0, last: 0, frameTime: -1, tracker: null };

async function startHands() {
  let vision, base = "./vendor", model = "./vendor/hand_landmarker.task";
  try {
    vision = await import("./vendor/vision_bundle.mjs");     // served by the Mac, so it works without internet
  } catch (e) {
    vision = await import(`${CDN}/vision_bundle.mjs`);
    base = CDN;
    model = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
  }
  const files = await vision.FilesetResolver.forVisionTasks(`${base}/wasm`);
  const make = (delegate) => vision.HandLandmarker.createFromOptions(files, {
    baseOptions: { modelAssetPath: model, delegate }, runningMode: "VIDEO", numHands: 2,
    minHandDetectionConfidence: 0.5, minHandPresenceConfidence: 0.5, minTrackingConfidence: 0.5 });
  try { hands.tracker = await make("GPU"); } catch (e) { hands.tracker = await make("CPU"); }
  handTick();
}

function handTick() {
  setTimeout(handTick, 1000 / cfg.handFps);         // slower than the picture on purpose: head movement comes first
  if (video.readyState < 2 || video.currentTime === hands.frameTime) return;
  hands.frameTime = video.currentTime;
  const now = performance.now();
  let result;
  try { result = hands.tracker.detectForVideo(video, now); } catch (e) { return; }
  const labels = result.handedness || result.handednesses || [];
  const found = (result.landmarks || []).map((lm, i) => ({
    label: labels[i] && labels[i][0] ? labels[i][0].categoryName : "Right",
    lm: lm.flatMap((q) => [+q.x.toFixed(4), +q.y.toFixed(4)]) }));
  if (hands.last) hands.fps += 0.2 * (1000 / (now - hands.last) - hands.fps);
  hands.last = now;
  send({ t: "hand", ts: now, a: video.videoWidth / video.videoHeight, head: headAt(now - cfg.camLag),
    hands: found, fps: +hands.fps.toFixed(1) });
  if (DEBUG) drawMarks(found);
}

// debug view only: the joints over the camera picture
const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];
function drawMarks(found) {
  const c = $("marks"), g = c.getContext("2d");
  if (c.width !== video.videoWidth) { c.width = video.videoWidth; c.height = video.videoHeight; }
  g.clearRect(0, 0, c.width, c.height);
  g.lineWidth = 2;
  for (const hand of found) {
    const at = (i) => [hand.lm[i * 2] * c.width, hand.lm[i * 2 + 1] * c.height];
    g.strokeStyle = "#78dcff";
    for (const [a, b] of BONES) { g.beginPath(); g.moveTo(...at(a)); g.lineTo(...at(b)); g.stroke(); }
    g.fillStyle = "#ffdc78";
    for (const i of [0, 4, 8]) { g.beginPath(); g.arc(...at(i), 5, 0, 7); g.fill(); }      // wrist, thumb tip, index tip
  }
}

function readout(m) {
  const d = m.dbg || {};
  $("readout").textContent = [
    `phase        ${m.phase}${m.hud.card ? " · " + m.hud.card.title : ""}`,
    `head         yaw ${d.yaw}  pitch ${d.pitch}  roll ${d.roll}  (${head.sensor ? "sensor" : "no sensor: drag the view"})`,
    `hand         ${d.hand}   shape ${d.shape}   gripper ${d.gesture}   x${d.scale}`,
    `hand tracker ${hands.tracker ? hands.fps.toFixed(0) + " fps" : "loading..."}   camera ${video.videoWidth}x${video.videoHeight}`,
    `picture      ${d.fps} fps from the Mac, quality ${d.q}`,
    `ORCA         ${m.hud.orca}   ${m.hud.sub || ""}`,
    `permissions  ${JSON.stringify(perms)}`,
  ].join("\n");
}

// ====================================================================== voice: microphone out, ORCA back
async function startMic(stream) {
  const source = audio.createMediaStreamSource(stream);
  await audio.audioWorklet.addModule("mic-worklet.js");
  const mic = new AudioWorkletNode(audio, "mic");
  mic.port.onmessage = (e) => { if (ws && ws.readyState === 1 && cfg.mic) ws.send(e.data); };
  const silent = audio.createGain();              // it has to lead somewhere to keep running, but must not be heard
  silent.gain.value = 0;
  source.connect(mic).connect(silent).connect(audio.destination);
}

function speak(id, data) {
  const done = () => send({ t: "played", id });
  if (!audio) return done();
  audio.decodeAudioData(data, (buffer) => {
    const source = audio.createBufferSource();
    source.buffer = buffer;
    source.connect(audio.destination);
    source.onended = done;
    source.start();
  }, done);
}

// ====================================================================== START VR EXPERIENCE
function check(text, cls) {
  const li = document.createElement("li");
  li.textContent = text;
  li.className = cls || "";
  $("checks").appendChild(li);
}

async function fullscreen() {
  const root = document.documentElement;
  try {
    if (!document.fullscreenElement && !document.webkitFullscreenElement) {
      await (root.requestFullscreen || root.webkitRequestFullscreen).call(root);
    }
    await screen.orientation.lock("landscape");
  } catch (e) { /* iPhones have neither: turn the phone by hand */ }
}

async function start() {
  $("start").disabled = true;
  $("start").textContent = "STARTING...";
  // sound has to be switched on inside the tap itself, before anything is waited for
  try { audio = new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); } catch (e) { audio = null; }

  if (!DEBUG) fullscreen();

  // 1. motion
  perms.motion = "unavailable";
  try {
    if (window.DeviceOrientationEvent && typeof DeviceOrientationEvent.requestPermission === "function") {
      perms.motion = await DeviceOrientationEvent.requestPermission();        // iPhone asks; "granted" or "denied"
    } else if (window.DeviceOrientationEvent) {
      perms.motion = "granted";
    }
  } catch (e) { perms.motion = "denied"; }
  if (perms.motion === "granted") {
    window.addEventListener("deviceorientation", onOrientation);
    await new Promise((r) => setTimeout(r, 400));
    if (!head.sensor) perms.motion = "unavailable";        // a laptop says yes and then sends nothing
  }
  check(`head tracking: ${perms.motion}`, perms.motion === "granted" ? "ok" : "bad");

  // 2. rear camera, 3. microphone
  const wantVideo = { facingMode: { ideal: "environment" }, width: { ideal: cfg.camSize[0] },
    height: { ideal: cfg.camSize[1] }, frameRate: { ideal: 30 } };
  const wantAudio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };
  const ask = (c) => navigator.mediaDevices.getUserMedia(c).catch((e) => e);
  let media = navigator.mediaDevices ? await ask({ video: wantVideo, audio: cfg.mic ? wantAudio : false }) : new Error("not a secure page");
  let camera = media, mic = media;
  if (!(media instanceof MediaStream)) {      // one of the two was refused: ask for each alone to find out which
    camera = await ask({ video: wantVideo });
    mic = cfg.mic ? await ask({ audio: wantAudio }) : null;
  }
  const answer = (s, kind) => (s instanceof MediaStream && s[kind]().length ? "granted" : (s && s.name) || "unavailable");
  perms.camera = answer(camera, "getVideoTracks");
  perms.mic = cfg.mic ? answer(mic, "getAudioTracks") : "on the Mac";
  check(`rear camera: ${perms.camera}`, perms.camera === "granted" ? "ok" : "bad");
  check(`microphone: ${perms.mic}`, perms.mic === "granted" || !cfg.mic ? "ok" : "bad");

  if (perms.camera === "granted") {
    video.srcObject = camera;
    video.play().catch(() => {});
    startHands().catch((e) => report("hand", e));
  }
  if (cfg.mic && perms.mic === "granted" && audio) startMic(mic).catch((e) => report("mic", e));

  // into VR: full screen, sideways, and don't let the screen sleep
  if (!DEBUG) {
    await fullscreen();
    canvas.addEventListener("pointerup", fullscreen);       // a tap brings it back if the phone dropped out of it
    $("hud").hidden = false;
  } else {
    document.body.classList.add("debug");
    $("viewpane").appendChild(canvas);
    $("camwrap").prepend(video);
    video.classList.remove("unseen");
    $("debug").hidden = false;
    for (const b of document.querySelectorAll("#debug button")) b.onclick = () => send({ t: "cmd", cmd: b.dataset.cmd });
  }
  const awake = () => navigator.wakeLock && navigator.wakeLock.request("screen").catch(() => {});
  awake();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) awake(); });
  const turned = () => { $("rotate").hidden = DEBUG || innerWidth >= innerHeight; };
  addEventListener("resize", turned);
  turned();

  started = true;
  $("setup").hidden = true;
  sendStart();
}

// ====================================================================== go
if (gl) {
  setupGl();
  buildHud();
  dragToLook(canvas);
  if (DEBUG) $("debuglink").textContent = "back to the VR start screen", $("debuglink").href = location.pathname;
  $("start").onclick = start;
  connect();
  requestAnimationFrame(draw);
} else {
  $("start").textContent = "THIS BROWSER HAS NO WEBGL";
}
