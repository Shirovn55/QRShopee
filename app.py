# api/index.py
import time, json, requests, urllib.parse
from flask import Flask, request, jsonify

app = Flask(__name__)

SHOPEE_API = "https://shopee.vn"
SESSION_TTL = 300          # 5 phút
QR_COOLDOWN = 60           # 60s / user
RATE_LIMIT = 10            # 10 req / 60s

# ================= MEMORY =================
SESSIONS = {}      # sid -> session data
RATE = {}          # key -> [timestamps]

# ================= UTILS =================
def now():
    return int(time.time())

def clean_rate(key):
    RATE[key] = [t for t in RATE.get(key, []) if now() - t < 60]

def hit_rate(key):
    clean_rate(key)
    RATE.setdefault(key, []).append(now())
    return len(RATE[key])

def headers():
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://shopee.vn/buyer/login/qr"
    }

def call(endpoint, method="GET", data=None, cookies=None):
    url = f"{SHOPEE_API}{endpoint}?_={int(time.time()*1000)}"
    if method == "GET":
        return requests.get(url, headers=headers(), cookies=cookies, timeout=10)
    return requests.post(url, headers=headers(), json=data, cookies=cookies, timeout=10)

def cleanup_sessions():
    for sid in list(SESSIONS.keys()):
        if now() - SESSIONS[sid]["created"] > SESSION_TTL:
            del SESSIONS[sid]

# ================= API =================

@app.before_request
def before():
    cleanup_sessions()

@app.route("/api/qr/create", methods=["POST"])
def create_qr():
    ip = request.headers.get("x-forwarded-for", request.remote_addr)
    user_id = request.json.get("user_id", "anon")
    key = f"{ip}:{user_id}"

    if hit_rate(key) > RATE_LIMIT:
        return jsonify(success=False, error="Rate limit")

    # cooldown
    for s in SESSIONS.values():
        if s["user_id"] == user_id and now() - s["created"] < QR_COOLDOWN:
            return jsonify(success=False, error="Please wait before creating new QR")

    r = call("/api/v2/authentication/gen_qrcode")
    data = r.json()

    if data.get("error") != 0:
        return jsonify(success=False, error="Shopee error")

    sid = str(now())
    SESSIONS[sid] = {
        "user_id": user_id,
        "qrcode_id": data["data"]["qrcode_id"],
        "qrcode_token": "",
        "cookies": {},
        "created": now(),
        "spc": ""
    }

    return jsonify(
        success=True,
        session_id=sid,
        qr_image="data:image/png;base64," + data["data"]["qrcode_base64"]
    )

@app.route("/api/qr/status/<sid>")
def qr_status(sid):
    sess = SESSIONS.get(sid)
    if not sess:
        return jsonify(success=False, status="NOT_FOUND")

    try:
        r = call(
            f"/api/v2/authentication/qrcode_status?qrcode_id={urllib.parse.quote(sess['qrcode_id'])}",
            cookies=sess["cookies"]
        )
        
        # THÊM XỬ LÝ LỖI
        if r.status_code != 200:
            return jsonify(success=False, error=f"Shopee API error: {r.status_code}")
        
        data = r.json()
        
        # THÊM CHECK None và key "data"
        if not data or "data" not in data:
            return jsonify(success=False, error="Invalid response from Shopee")
        
        status = data["data"].get("status", "UNKNOWN")
        token = data["data"].get("qrcode_token")
        
        if token:
            sess["qrcode_token"] = token
            
        return jsonify(
            success=True,
            status=status,
            has_token=bool(sess["qrcode_token"])
        )
        
    except Exception as e:
        # THÊM XỬ LÝ EXCEPTION
        return jsonify(success=False, error=f"Internal error: {str(e)}")

@app.route("/api/qr/login/<sid>", methods=["POST"])
def qr_login(sid):
    sess = SESSIONS.get(sid)
    if not sess or not sess["qrcode_token"]:
        return jsonify(success=False, error="Invalid session")

    payload = {
        "qrcode_token": sess["qrcode_token"],
        "device_sz_fingerprint": "fixed_fp"
    }

    r = call("/api/v2/authentication/qrcode_login", "POST", payload, sess["cookies"])
    spc = r.cookies.get("SPC_ST")

    if not spc:
        return jsonify(success=False, error="Login failed")

    cookie = f"SPC_ST={spc}"
    sess["spc"] = cookie

    return jsonify(success=True, cookie=cookie)
