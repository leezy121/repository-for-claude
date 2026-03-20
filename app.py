from flask import Flask, request, jsonify
import os, json, time, traceback, hashlib, hmac, base64
from urllib.request import urlopen, Request
from urllib.error import URLError

app = Flask(__name__)

CLOB_HOST   = "https://clob.polymarket.com"
API_KEY     = os.environ.get("POLY_API_KEY", "")
API_SECRET  = os.environ.get("POLY_API_SECRET", "")
PASSPHRASE  = os.environ.get("POLY_PASSPHRASE", "")
PRIVATE_KEY = os.environ.get("POLY_PRIVATE_KEY", "")
WEBHOOK_SEC = os.environ.get("WEBHOOK_SECRET", "")


def sign(method, path, body=""):
    ts  = str(int(time.time()))
    msg = ts + method.upper() + path + (body or "")
    try:
        secret = base64.b64decode(API_SECRET + "==")
    except Exception:
        secret = API_SECRET.encode()
    sig = base64.b64encode(
        hmac.new(secret, msg.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "POLY-API-KEY":    API_KEY,
        "POLY-SIGNATURE":  sig,
        "POLY-TIMESTAMP":  ts,
        "POLY-PASSPHRASE": PASSPHRASE,
        "Content-Type":    "application/json"
    }


def http_get(path):
    headers = sign("GET", path)
    req = Request(CLOB_HOST + path, headers=headers)
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def http_post(path, payload):
    body    = json.dumps(payload)
    headers = sign("POST", path, body)
    req     = Request(
        CLOB_HOST + path,
        data=body.encode(),
        headers=headers,
        method="POST"
    )
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def auth_ok(req):
    return req.headers.get("X-Webhook-Secret", "") == WEBHOOK_SEC


def parse_body(req):
    raw = req.get_data(as_text=True)
    try:
        d = json.loads(raw)
        if isinstance(d, str):
            d = json.loads(d)
        return d if isinstance(d, dict) else {}
    except Exception:
        return req.get_json(force=True, silent=True) or {}


@app.route("/health")
def health():
    return jsonify({
        "status":          "ok",
        "clob_available":  True,
        "message":         "Leez Polymarket executor is running",
        "api_key_set":     bool(API_KEY),
        "private_key_set": bool(PRIVATE_KEY)
    })


@app.route("/trade", methods=["POST"])
def trade():
    if not auth_ok(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        d         = parse_body(request)
        token_id  = str(d.get("token_id", ""))
        price     = float(d.get("price", 0))
        size      = float(d.get("size", 0))
        side      = str(d.get("side", "BUY"))
        market_id = str(d.get("market_id", ""))
        question  = str(d.get("question", ""))

        size = min(size, 1.60)
        if size < 0.10:
            return jsonify({"error": f"Size ${size} too small (min $0.10)"}), 400
        if not token_id or not price:
            return jsonify({"error": "Missing token_id or price"}), 400

        order = {
            "orderType":  "GTC",
            "tokenID":    token_id,
            "price":      str(round(price, 4)),
            "size":       str(round(size, 2)),
            "side":       side,
            "feeRateBps": "0",
            "nonce":      str(int(time.time() * 1000)),
            "expiration": "0"
        }

        result = http_post("/order", order)
        return jsonify({
            "success":   True,
            "order_id":  result.get("orderID", result.get("id", "unknown")),
            "status":    result.get("status", "submitted"),
            "market_id": market_id,
            "question":  question,
            "size":      size,
            "price":     price,
            "raw":       result
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error":   str(e),
            "trace":   traceback.format_exc()
        }), 500


@app.route("/outcome", methods=["POST"])
def outcome():
    if not auth_ok(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        d         = parse_body(request)
        market_id = str(d.get("market_id", ""))
        if not market_id:
            return jsonify({"error": "market_id required"}), 400

        market   = http_get(f"/markets/{market_id}")
        resolved = market.get("closed", False)
        out      = None
        if resolved:
            for t in market.get("tokens", []):
                if float(t.get("price", 0)) >= 0.99:
                    out = t.get("outcome")
        return jsonify({
            "market_id": market_id,
            "question":  market.get("question", ""),
            "resolved":  resolved,
            "outcome":   out
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/balance")
def balance():
    try:
        return jsonify(http_get("/balance-allowance?asset_type=USDC"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting Leez Polymarket executor on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
