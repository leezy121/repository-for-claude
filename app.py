from flask import Flask, request, jsonify
import os, json, time, traceback

app = Flask(__name__)

WEBHOOK_SEC = os.environ.get("WEBHOOK_SECRET", "")
PRIVATE_KEY = os.environ.get("POLY_PRIVATE_KEY", "")
API_KEY     = os.environ.get("POLY_API_KEY", "")
API_SECRET  = os.environ.get("POLY_API_SECRET", "")
PASSPHRASE  = os.environ.get("POLY_PASSPHRASE", "")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, Side
    from py_clob_client.constants import POLYGON
    CLOB_OK = True
except ImportError:
    CLOB_OK = False

def get_client():
    return ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=PRIVATE_KEY,
        creds={
            "apiKey":     API_KEY,
            "secret":     API_SECRET,
            "passphrase": PASSPHRASE,
        }
    )

def auth_ok(req):
    return req.headers.get("X-Webhook-Secret", "") == WEBHOOK_SEC

def parse_body(req):
    raw = req.get_data(as_text=True)
    try:
        d = json.loads(raw)
        if isinstance(d, str):
            d = json.loads(d)
        return d
    except Exception:
        return req.get_json(force=True, silent=True) or {}

@app.route("/health")
def health():
    return jsonify({
        "status":          "ok",
        "clob_available":  CLOB_OK,
        "message":         "Leez Polymarket executor is running",
        "api_key_set":     bool(API_KEY),
        "private_key_set": bool(PRIVATE_KEY)
    })

@app.route("/trade", methods=["POST"])
def trade():
    if not auth_ok(request):
        return jsonify({"error": "Unauthorized"}), 401
    if not CLOB_OK:
        return jsonify({"error": "py_clob_client not installed"}), 500
    try:
        d         = parse_body(request)
        token_id  = str(d.get("token_id", ""))
        price     = float(d.get("price", 0))
        size      = float(d.get("size", 0))
        market_id = d.get("market_id", "")
        question  = d.get("question", "")

        # Safety limits
        size = min(size, 1.60)  # max 20% of $8
        if size < 0.10:
            return jsonify({"error": f"Size ${size} too small"}), 400
        if not token_id or not price:
            return jsonify({"error": "Missing token_id or price"}), 400

        client = get_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=Side.BUY,
        )
        signed_order = client.create_order(order_args)
        result       = client.post_order(signed_order, OrderType.GTC)

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
    if not CLOB_OK:
        return jsonify({"error": "py_clob_client not installed"}), 500
    try:
        d         = parse_body(request)
        market_id = d.get("market_id", "")
        if not market_id:
            return jsonify({"error": "market_id required"}), 400

        client   = get_client()
        market   = client.get_market(market_id)
        resolved = market.get("closed", False)
        outcome  = None
        if resolved:
            for t in market.get("tokens", []):
                if float(t.get("price", 0)) >= 0.99:
                    outcome = t.get("outcome")
        return jsonify({
            "market_id": market_id,
            "question":  market.get("question", ""),
            "resolved":  resolved,
            "outcome":   outcome
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/balance")
def balance():
    if not CLOB_OK:
        return jsonify({"error": "py_clob_client not installed"}), 500
    try:
        client = get_client()
        return jsonify(client.get_balance())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting Leez Polymarket executor on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
