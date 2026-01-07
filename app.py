# api/index.py
# -*- coding: utf-8 -*-
"""
NgânMiu.Store — Shopee QR Login API (Vercel/Flask)

FIXES:
✅ Không còn lỗi data["data"] = None gây 500
✅ Sửa bug URL: endpoint đã có "?" thì phải dùng "&_=" (trước đây bị "??_=")
✅ Lưu cookies từ gen_qrcode / status / login để ổn định hơn
✅ request.json có thể None → dùng get_json(silent=True)
✅ Parse SPC_ST fallback từ Set-Cookie nếu r.cookies không có
⚠️ Lưu ý: Vercel serverless có thể thay instance → SESSIONS RAM có thể mất.
   Muốn ổn định tuyệt đối: dùng Vercel KV / Upstash Redis / hoặc chạy API trên VPS/Render.
"""

import time
import re
import requests
import urllib.parse
from flask import Flask, request, jsonify

app = Flask(__name__)

SHOPEE_API = "https://shopee.vn"
SESSION_TTL = 300          # 5 phút
QR_COOLDOWN = 60           # 60s / user
RATE_LIMIT = 10            # 10 req / 60s

# ================= MEMORY (RAM) =================
SESSIONS = {}      # sid -> session data
RATE = {}          # key -> [timestamps]

# ================= UTILS =================
def now() -> int:
    return int(time.time())

def clean_rate(key: str) -> None:
    RATE[key] = [t for t in RATE.get(key, []) if now() - t < 60]

def hit_rate(key: str) -> int:
    clean_rate(key)
    RATE.setdefault(key, []).append(now())
    return len(RATE[key])

def headers():
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://shopee.vn/buyer/login/qr",
    }

def _build_url(endpoint: str) -> str:
    """
    Fix BUG: endpoint có sẵn query (?a=b) thì phải nối thêm &_=... chứ không phải ?_=
    """
    endpoint = endpoint or ""
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    sep = "&" if "?" in endpoint else "?"
    return f"{SHOPEE_API}{endpoint}{sep}_={int(time.time()*1000)}"

def call(endpoint: str, method: str = "GET", data=None, cookies=None):
    url = _build_url(endpoint)
    if method.upper() == "GET":
        return requests.get(url, headers=headers(), cookies=cookies, timeout=10)
    return requests.post(url, headers=headers(), json=data, cookies=cookies, timeout=10)

def _merge_cookies(sess: dict, resp: requests.Response) -> None:
    """
    Lưu cookies về dict để lần sau gửi lại.
    """
    try:
        jar = resp.cookies
        if jar:
            d = jar.get_dict()
            if d:
                sess["cookies"] = {**(sess.get("cookies") or {}), **d}
    except Exception:
        pass

def cleanup_sessions() -> None:
    t = now()
    for sid in list(SESSIONS.keys()):
        try:
            if t - int(SESSIONS[sid].get("created", 0)) > SESSION_TTL:
                del SESSIONS[sid]
        except Exception:
            try:
                del SESSIONS[sid]
            except Exception:
                pass

def _json_safe(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return None

def _extract_spc_st(resp: requests.Response) -> str:
    """
    Ưu tiên resp.cookies.get('SPC_ST'), fallback parse Set-Cookie.
    """
    try:
        v = resp.cookies.get("SPC_ST")
        if v:
            return v
    except Exception:
        pass

    try:
        sc = resp.headers.get("set-cookie") or ""
        m = re.search(r"SPC_ST=([^;]+)", sc)
        if m:
            return m.group(1)
    except Exception:
        pass

    return ""

# ================= API =================

@app.before_request
def before():
    cleanup_sessions()

@app.route("/api/qr/create", methods=["POST"])
def create_qr():
    ip = request.headers.get("x-forwarded-for", request.remote_addr) or "0.0.0.0"
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id", "anon")
    key = f"{ip}:{user_id}"

    if hit_rate(key) > RATE_LIMIT:
        return jsonify(success=False, error="Rate limit"), 429

    # cooldown
    for s in SESSIONS.values():
        if str(s.get("user_id")) == str(user_id) and now() - int(s.get("created", 0)) < QR_COOLDOWN:
            return jsonify(success=False, error="Please wait before creating new QR"), 429

    r = call("/api/v2/authentication/gen_qrcode")
    if r.status_code != 200:
        return jsonify(success=False, error=f"Shopee API error: {r.status_code}"), 502

    data = _json_safe(r)
    if not isinstance(data, dict):
        return jsonify(success=False, error="Invalid response from Shopee"), 502

    if data.get("error") != 0:
        # có thể Shopee trả error_msg
        return jsonify(success=False, error=data.get("error_msg") or "Shopee error"), 502

    d = data.get("data")
    if not isinstance(d, dict):
        return jsonify(success=False, error="Shopee returned empty data"), 502

    qrcode_id = d.get("qrcode_id")
    qr_b64 = d.get("qrcode_base64")
    if not qrcode_id or not qr_b64:
        return jsonify(success=False, error="Shopee missing qrcode_id/qr_base64"), 502

    sid = str(now())

    SESSIONS[sid] = {
        "user_id": user_id,
        "qrcode_id": qrcode_id,
        "qrcode_token": "",
        "cookies": {},        # dict cookie
        "created": now(),
        "spc": ""
    }

    # lưu cookies nếu Shopee set
    _merge_cookies(SESSIONS[sid], r)

    return jsonify(
        success=True,
        session_id=sid,
        qr_image="data:image/png;base64," + qr_b64
    )

@app.route("/api/qr/status/<sid>", methods=["GET"])
def qr_status(sid):
    sess = SESSIONS.get(sid)
    if not sess:
        return jsonify(success=False, status="NOT_FOUND"), 404

    try:
        # ⚠️ endpoint có query → call() đã fix nối &_=
        qid = urllib.parse.quote(str(sess.get("qrcode_id") or ""))
        if not qid:
            return jsonify(success=False, status="INVALID_SESSION", error="Missing qrcode_id"), 400

        r = call(
            f"/api/v2/authentication/qrcode_status?qrcode_id={qid}",
            cookies=sess.get("cookies") or {}
        )

        if r.status_code != 200:
            return jsonify(success=False, status="API_ERROR", error=f"Shopee API error: {r.status_code}"), 502

        data = _json_safe(r)
        if not isinstance(data, dict):
            return jsonify(success=False, status="API_ERROR", error="Invalid JSON from Shopee"), 502

        # Lưu cookies nếu có
        _merge_cookies(sess, r)

        # Shopee có thể trả {"error":0,"data":null} khi chưa sẵn sàng → đừng 500
        if data.get("error") not in (0, None):
            return jsonify(success=False, status="API_ERROR", error=data.get("error_msg") or "Shopee error"), 502

        d = data.get("data")
        if not isinstance(d, dict):
            # trả pending thay vì crash
            return jsonify(success=True, status="PENDING", has_token=bool(sess.get("qrcode_token")))

        status = d.get("status") or "PENDING"
        token = d.get("qrcode_token") or ""
        if token:
            sess["qrcode_token"] = token

        return jsonify(
            success=True,
            status=status,
            has_token=bool(sess.get("qrcode_token"))
        )

    except Exception as e:
        return jsonify(success=False, status="CHECK_ERROR", error=f"Internal error: {str(e)}"), 500

@app.route("/api/qr/login/<sid>", methods=["POST"])
def qr_login(sid):
    sess = SESSIONS.get(sid)
    if not sess:
        return jsonify(success=False, error="Invalid session"), 404

    token = sess.get("qrcode_token") or ""
    if not token:
        return jsonify(success=False, error="Token not ready"), 409

    payload = {
        "qrcode_token": token,
        "device_sz_fingerprint": "fixed_fp"
    }

    r = call("/api/v2/authentication/qrcode_login", "POST", payload, sess.get("cookies") or {})

    if r.status_code != 200:
        return jsonify(success=False, error=f"Shopee API error: {r.status_code}"), 502

    data = _json_safe(r)
    if isinstance(data, dict) and data.get("error") not in (0, None):
        return jsonify(success=False, error=data.get("error_msg") or "Login failed"), 502

    # cập nhật cookies
    _merge_cookies(sess, r)

    spc = _extract_spc_st(r)
    if not spc:
        return jsonify(success=False, error="Login failed (no SPC_ST)"), 502

    cookie = f"SPC_ST={spc}"
    sess["spc"] = cookie

    return jsonify(success=True, cookie=cookie)
