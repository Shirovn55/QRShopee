# api/index.py
# -*- coding: utf-8 -*-
"""
NgânMiu.Store — Shopee QR Login API (Vercel/Flask) - FIXED VERSION

✅ FIXED: Cập nhật header Android app mới (giống tool GUI)
✅ FIXED: Thêm CSRF token random 32 ký tự
✅ FIXED: Thêm Cookie ban đầu với csrftoken + SPC_F
✅ FIXED: Trả về FULL cookies thay vì chỉ SPC_ST

Changes từ version cũ:
- Header: Mozilla → Android app Shopee appver=33016
- Thêm: X-CSRFToken, Cookie ban đầu
- Response: Trả full cookies dict thay vì chỉ cookie string
"""

import time
import re
import requests
import urllib.parse
import random
import string
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

def generate_random_32() -> str:
    """Generate random 32 characters for CSRF token"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=32))

def clean_rate(key: str) -> None:
    RATE[key] = [t for t in RATE.get(key, []) if now() - t < 60]

def hit_rate(key: str) -> int:
    clean_rate(key)
    RATE.setdefault(key, []).append(now())
    return len(RATE[key])

def headers(csrf_token: str = None):
    """
    ✅ FIXED: Header Android app mới với CSRF token
    """
    if not csrf_token:
        csrf_token = generate_random_32()
    
    return {
        "Accept": "application/json",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Cookie": f"csrftoken={csrf_token}; SPC_F=YPByHuJJks2b7GpDwIdZp6ONQwyaN4yv;",
        "Host": "shopee.vn",
        "Origin": "https://shopee.vn",
        "Referer": "https://shopee.vn/buyer/login/qr",
        "User-Agent": "Android app Shopee appver=33016 app_type=1",
        "X-CSRFToken": csrf_token
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

def call(endpoint: str, csrf_token: str, method: str = "GET", data=None, cookies=None):
    """
    ✅ FIXED: Thêm csrf_token vào headers
    """
    url = _build_url(endpoint)
    hdrs = headers(csrf_token)
    
    # Merge cookies vào header Cookie nếu có
    if cookies:
        cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
        if cookie_str:
            hdrs["Cookie"] = f"csrftoken={csrf_token}; SPC_F=YPByHuJJks2b7GpDwIdZp6ONQwyaN4yv; {cookie_str}"
    
    if method.upper() == "GET":
        return requests.get(url, headers=hdrs, timeout=10)
    return requests.post(url, headers=hdrs, json=data, timeout=10)

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
    
    # ✅ FIXED: Parse từ Set-Cookie header
    try:
        set_cookie = resp.headers.get("set-cookie", "")
        if set_cookie:
            # Parse multiple cookies
            for cookie_part in set_cookie.split(","):
                for item in cookie_part.split(";"):
                    if "=" in item:
                        key, val = item.strip().split("=", 1)
                        if key and not key.startswith("Path") and not key.startswith("Domain") and not key.startswith("Expires"):
                            sess["cookies"][key] = val
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

    # ✅ FIXED: Tạo CSRF token mới cho session
    csrf_token = generate_random_32()
    
    r = call("/api/v2/authentication/gen_qrcode", csrf_token)
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
        "csrf_token": csrf_token,  # ✅ FIXED: Lưu csrf token
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

        csrf_token = sess.get("csrf_token") or generate_random_32()
        
        r = call(
            f"/api/v2/authentication/qrcode_status?qrcode_id={qid}",
            csrf_token,
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

    csrf_token = sess.get("csrf_token") or generate_random_32()
    
    r = call("/api/v2/authentication/qrcode_login", csrf_token, "POST", payload, sess.get("cookies") or {})

    if r.status_code != 200:
        return jsonify(success=False, error=f"Shopee API error: {r.status_code}"), 502

    data = _json_safe(r)
    if isinstance(data, dict) and data.get("error") not in (0, None):
        return jsonify(success=False, error=data.get("error_msg") or "Login failed"), 502

    # cập nhật cookies
    _merge_cookies(sess, r)

    spc = _extract_spc_st(r)
    if not spc:
        # ✅ FIXED: Có thể SPC_ST đã trong cookies dict
        spc = sess.get("cookies", {}).get("SPC_ST", "")
    
    if not spc:
        return jsonify(success=False, error="Login failed (no SPC_ST)"), 502

    # ✅ FIXED: Lưu SPC_ST vào cookies dict
    sess["cookies"]["SPC_ST"] = spc
    cookie = f"SPC_ST={spc}"
    sess["spc"] = cookie

    # ✅ FIXED: Trả về FULL cookies thay vì chỉ SPC_ST
    return jsonify(
        success=True,
        cookie=cookie,
        cookies=sess.get("cookies", {}),  # Full cookies dict
        cookie_string="; ".join([f"{k}={v}" for k, v in sess.get("cookies", {}).items()])  # Full cookie string
    )

# ================= NEW ENDPOINT: Get Full Cookies =================
@app.route("/api/qr/cookies/<sid>", methods=["GET"])
def get_cookies(sid):
    """
    ✅ NEW: Endpoint mới để lấy full cookies của session
    """
    sess = SESSIONS.get(sid)
    if not sess:
        return jsonify(success=False, error="Invalid session"), 404
    
    cookies = sess.get("cookies", {})
    if not cookies:
        return jsonify(success=False, error="No cookies available"), 404
    
    cookie_string = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    
    return jsonify(
        success=True,
        cookies=cookies,
        cookie_string=cookie_string,
        has_spc_st=bool(cookies.get("SPC_ST"))
    )

# ================= HEALTH CHECK =================
@app.route("/", methods=["GET"])
def index():
    return jsonify(
        status="OK",
        version="2.0-FIXED",
        message="Shopee QR Login API with Android headers",
        endpoints=[
            "POST /api/qr/create - Tạo QR code",
            "GET /api/qr/status/<sid> - Kiểm tra trạng thái QR", 
            "POST /api/qr/login/<sid> - Đăng nhập với QR token",
            "GET /api/qr/cookies/<sid> - Lấy full cookies"
        ]
    )

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
