from flask import Flask, request, jsonify
import os, json, time, traceback, hashlib, hmac, base64
from urllib.request import urlopen, Request

app = Flask(__name__)

CLOB_HOST   = "https://clob.polymarket.com"
API_KEY     = os.environ.get("POLY_API_KEY", "")
API_SECRET  = os.environ.get("POLY_API_SECRET", "")
PASSPHRASE  = os.environ.get("POLY_PASSPHRASE", "")
PRIVATE_KEY = os.environ.get("POLY_PRIVATE_KEY", "")
WEBHOOK_SEC = os.environ.get("WEBHOOK_SECRET", "")

# Try importing eth_account for EIP-712 signing
try:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    ETH_OK = True
except ImportError:
    ETH_OK = False


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


def get_api_headers(method, path, body=""):
    """HMAC headers for read-only API calls"""
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
    headers = get_api_headers("GET", path)
    req = Request(CLOB_HOST + path, headers=headers)
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def build_and_sign_order(token_id, price, size, side):
    """Build and sign order using EIP-712"""
    if not ETH_OK:
        raise Exception("eth_account not available")

    pk = PRIVATE_KEY.strip()
if not pk.startswith("0x"):
    pk = "0x" + pk
account = Account.from_key(pk)

    # Order struct for Polymarket CTF Exchange
    order = {
        "salt":        int(time.time() * 1000),
        "maker":       account.address,
        "signer":      account.address,
        "taker":       "0x0000000000000000000000000000000000000000",
        "tokenId":     int(token_id),
        "makerAmount": int(float(size) * 1e6),  # USDC has 6 decimals
        "takerAmount": int(float(size) * float(price) * 1e6),
        "expiration":  0,
        "nonce":       0,
        "feeRateBps":  0,
        "side":        0 if side == "BUY" else 1,
        "signatureType": 0
    }

    # EIP-712 domain for Polymarket
    domain = {
        "name":              "CTF Exchange",
        "version":           "1",
        "chainId":           137,  # Polygon
        "verifyingContract": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    }

    types = {
        "Order": [
            {"name": "salt",          "type": "uint256"},
            {"name": "maker",         "type": "address"},
            {"name": "signer",        "type": "address"},
            {"name": "taker",         "type": "address"},
            {"name": "tokenId",       "type": "uint256"},
            {"name": "makerAmount",   "type": "uint256"},
            {"name": "takerAmount",   "type": "uint256"},
            {"name": "expiration",    "type": "uint256"},
            {"name": "nonce",         "type": "uint256"},
            {"name": "feeRateBps",    "type": "uint256"},
            {"name": "side",          "type": "uint8"},
            {"name": "signatureType", "type": "uint8"}
        ]
    }

    # Sign the order
    structured_data = {
        "types":       types,
        "domain":      domain,
        "primaryType": "Order",
        "message":     order
    }

    signed = Account.sign_typed_data(
        account.key,
        domain_data=domain,
        message_types={"Order": types["Order"]},
        message_data=order
    )

    order["signature"] = signed.signature.hex()
    return order


@app.route("/health")
def health():
    return jsonify({
        "status":          "ok",
        "clob_available":  True,
        "eth_account_ok":  ETH_OK,
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
            return jsonify({"error": f"Size ${size} too small"}), 400
        if not token_id or not price:
            return jsonify({"error": "Missing token_id or price"}), 400

        # Build signed order
        signed_order = build_and_sign_order(token_id, price, size, side)

        # Post to Polymarket
        ts  = str(int(time.time()))
        msg = ts + "POST" + "/order" + json.dumps(signed_order)
        try:
            secret = base64.b64decode(API_SECRET + "==")
        except Exception:
            secret = API_SECRET.encode()
        sig = base64.b64encode(
            hmac.new(secret, msg.encode(), hashlib.sha256).digest()
        ).decode()

        headers = {
            "POLY-API-KEY":    API_KEY,
            "POLY-SIGNATURE":  sig,
            "POLY-TIMESTAMP":  ts,
            "POLY-PASSPHRASE": PASSPHRASE,
            "Content-Type":    "application/json"
        }

        body    = json.dumps({"order": signed_order, "orderType": "GTC"})
        req_obj = Request(
            CLOB_HOST + "/order",
            data=body.encode(),
            headers=headers,
            method="POST"
        )
        with urlopen(req_obj, timeout=15) as r:
            result = json.loads(r.read().decode())

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
