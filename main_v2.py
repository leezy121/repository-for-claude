from flask import Flask, request, jsonify
import os, requests, json, time, traceback
from eth_account import Account
from eth_account.messages import encode_defunct
import hashlib, hmac, base64

app = Flask(__name__)

CLOB_HOST = "https://clob.polymarket.com"
API_KEY = os.environ.get("POLY_API_KEY", "")
API_SECRET = os.environ.get("POLY_API_SECRET", "")
API_PASSPHRASE = os.environ.get("POLY_PASSPHRASE", "")
PRIVATE_KEY = os.environ.get("POLY_PRIVATE_KEY", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

def get_auth_headers(method, path, body=""):
    timestamp = str(int(time.time()))
    message = timestamp + method.upper() + path + (body or "")
    secret_bytes = base64.b64decode(API_SECRET + "==")
    signature = base64.b64encode(
        hmac.new(secret_bytes, message.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return {
        "POLY-API-KEY": API_KEY,
        "POLY-SIGNATURE": signature,
        "POLY-TIMESTAMP": timestamp,
        "POLY-PASSPHRASE": API_PASSPHRASE,
        "Content-Type": "application/json"
    }

def check_secret(req):
    return req.headers.get("X-Webhook-Secret", "") == WEBHOOK_SECRET

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "clob_available": True,
        "message": "Leez Polymarket executor is running",
        "api_key_set": bool(API_KEY),
        "private_key_set": bool(PRIVATE_KEY)
    })

@app.route("/trade", methods=["POST"])
def place_trade():
    if not check_secret(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json()
        token_id = data.get("token_id")
        price    = float(data.get("price", 0))
        size     = float(data.get("size", 0))
        side     = data.get("side", "BUY")
        market_id = data.get("market_id", "")
        question  = data.get("question", "")

        # Safety limits
        size = min(size, 0.40)
        if size < 0.10:
            return jsonify({"error": f"Size ${size} too small (min $0.10)"}), 400

        if not token_id or not price:
            return jsonify({"error": "Missing token_id or price"}), 400

        # Build order payload
        order = {
            "orderType": "GTC",
            "tokenID": token_id,
            "price": str(price),
            "size": str(size),
            "side": side,
            "feeRateBps": "0",
            "nonce": str(int(time.time() * 1000)),
            "expiration": "0"
        }

        order_json = json.dumps(order)
        path = "/order"
        headers = get_auth_headers("POST", path, order_json)

        response = requests.post(
            CLOB_HOST + path,
            headers=headers,
            data=order_json,
            timeout=15
        )

        result = response.json()

        return jsonify({
            "success": response.status_code == 200,
            "order_id": result.get("orderID", result.get("id", "unknown")),
            "status": result.get("status", "submitted"),
            "market_id": market_id,
            "question": question,
            "size": size,
            "price": price,
            "raw": result
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }), 500


@app.route("/outcome", methods=["POST"])
def check_outcome():
    if not check_secret(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json()
        market_id = data.get("market_id")
        if not market_id:
            return jsonify({"error": "market_id required"}), 400

        path = f"/markets/{market_id}"
        headers = get_auth_headers("GET", path)
        response = requests.get(CLOB_HOST + path, headers=headers, timeout=15)
        market = response.json()

        resolved = market.get("closed", False)
        outcome = None
        if resolved:
            for t in market.get("tokens", []):
                if float(t.get("price", 0)) >= 0.99:
                    outcome = t.get("outcome", "Unknown")

        return jsonify({
            "market_id": market_id,
            "question": market.get("question", ""),
            "resolved": resolved,
            "outcome": outcome
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/balance", methods=["GET"])
def get_balance():
    try:
        path = "/balance-allowance?asset_type=USDC"
        headers = get_auth_headers("GET", path)
        response = requests.get(CLOB_HOST + path, headers=headers, timeout=15)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting Leez Polymarket executor on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
