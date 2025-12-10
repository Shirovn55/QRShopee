# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, render_template_string
import requests, base64

app = Flask(__name__)

BASE = "https://us-central1-get-feedback-a0119.cloudfunctions.net/app/api/shopee"

# ==============================
#   HTML + CSS + JS (INLINE)
# ==============================
PAGE = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>NgânMiu.Store — QR Login Shopee</title>
<style>
body { background:#f7f7f7; font-family:'Segoe UI',sans-serif; margin:0; }
.header {
    background:#EE4D2D; padding:15px; color:#fff; font-size:22px;
    font-weight:700; text-align:center;
}
.card {
    background:white; width:380px; padding:25px; margin:30px auto;
    border-radius:18px; box-shadow:0 6px 25px rgba(0,0,0,0.08);
    text-align:center;
}
.btn {
    width:100%; padding:12px; border:none; border-radius:10px;
    font-size:16px; font-weight:600; cursor:pointer;
    margin-top:10px;
}
.btn-main { background:#EE4D2D; color:white; }
.btn-save { background:#0d6efd; color:white; }
.btn-copy { background:#28a745; color:white; }
.qr-box img { width:260px; margin:20px 0; border-radius:12px; }
.cookie-box {
    width:100%; padding:10px; border-radius:8px; border:1px solid #ccc;
    font-size:14px; margin-top:10px;
}
.lbl { font-size:14px; font-weight:600; text-align:left; display:block; margin-top:10px; }
.status { margin-top:15px; font-size:14px; color:#666; }
</style>
</head>

<body>

<div class="header">Đăng nhập Shopee bằng QR — NgânMiu.Store</div>

<div class="card">

    <button id="btnGen" class="btn btn-main">Tạo mã QR</button>

    <div id="qrBox" class="qr-box"></div>

    <button id="btnSave" class="btn btn-save" style="display:none;">Lưu mã QR</button>

    <label class="lbl">Cookie:</label>
    <input id="cookieOut" class="cookie-box" readonly placeholder="SPC_ST sẽ hiển thị tại đây...">

    <button id="btnCopy" class="btn btn-copy" style="display:none;">Copy Cookie</button>

    <div id="status" class="status"></div>

</div>

<script>
let qrcode_id = "";
let real_token = "";
let timer = null;

// ----------- TẠO QR -----------
document.getElementById("btnGen").onclick = async () => {
    document.getElementById("status").innerText = "Đang tạo QR...";

    const res = await fetch("/api/generate");
    const j = await res.json();

    qrcode_id = j.qrcode_id;

    const img = "data:image/png;base64," + j.qr_base64;
    document.getElementById("qrBox").innerHTML = `<img src="${img}">`;

    document.getElementById("btnSave").style.display = "block";
    document.getElementById("status").innerText = "Đang chờ quét...";

    if (timer) clearInterval(timer);
    timer = setInterval(checkStatus, 2000);
};

// ----------- LƯU QR -----------
document.getElementById("btnSave").onclick = () => {
    const img = document.querySelector("#qrBox img").src;
    const a = document.createElement("a");
    a.href = img;
    a.download = "qr-login.png";
    a.click();
};

// ----------- COPY COOKIE -----------
document.getElementById("btnCopy").onclick = () => {
    const c = document.getElementById("cookieOut").value;
    navigator.clipboard.writeText(c);
    alert("Đã copy cookie thành công!");
};

// ----------- CHECK STATUS -----------
async function checkStatus() {
    if (!qrcode_id) return;

    const res = await fetch(`/api/status?qrcode_id=${encodeURIComponent(qrcode_id)}`);
    const j = await res.json();

    const st = j.status;
    const token = j.qrcode_token || "";

    if (st === "NEW" || st === "PENDING") {
        document.getElementById("status").innerText = "Chưa quét...";
    }
    else if (st === "SCANNED") {
        document.getElementById("status").innerText = "Đã quét — chờ xác nhận...";
    }
    else if (st === "CONFIRMED") {
        clearInterval(timer);
        document.getElementById("status").innerText = "Đã xác nhận — đang lấy cookie...";
        real_token = token;
        loginQR();
    }
    else if (st === "EXPIRED") {
        clearInterval(timer);
        document.getElementById("status").innerText = "QR đã hết hạn — tạo lại!";
    }
}

// ----------- LOGIN → LẤY COOKIE -----------
async function loginQR() {
    const res = await fetch("/api/login", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ qrcodeToken: real_token })
    });

    const j = await res.json();

    if (j.cookie) {
        document.getElementById("cookieOut").value = j.cookie;
        document.getElementById("btnCopy").style.display = "block";
        document.getElementById("status").innerHTML =
            "<span style='color:green;font-weight:700;'>Đăng nhập thành công!</span>";
    } else {
        document.getElementById("status").innerHTML =
            "<span style='color:red;'>Không lấy được cookie!</span>";
    }
}
</script>

</body>
</html>
"""

# ===========================
#       BACKEND API
# ===========================

@app.get("/")
def home():
    return render_template_string(PAGE)


@app.get("/api/generate")
def api_generate():
    r = requests.get(f"{BASE}/generate-qr-code", timeout=10)
    js = r.json()

    data = js.get("data") or {}

    return jsonify({
        "qrcode_id": data.get("qrcode_id", ""),
        "qr_base64": data.get("qrcode_base64", "")
    })


@app.get("/api/status")
def api_status():
    qid = request.args.get("qrcode_id", "")

    r = requests.get(f"{BASE}/check-qr-status?qrcode_id={qid}", timeout=10)
    js = r.json()

    data = js.get("data") or {}

    return jsonify({
        "status": data.get("status", "NEW"),
        "qrcode_token": data.get("qrcode_token", "")
    })


@app.post("/api/login")
def api_login():
    body = request.get_json() or {}
    token = body.get("qrcodeToken", "")

    r = requests.post(f"{BASE}/login-qr",
                      json={"qrcodeToken": token},
                      timeout=10)

    js = r.json()  # giữ nguyên trả về
    return jsonify(js)

# ===========================
#       RUN SERVER
# ===========================
if __name__ == "__main__":
    app.run(debug=True, port=5000)
