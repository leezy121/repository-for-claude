"""
Leez – Polymarket Trade Executor
Deploy this on Replit (free). n8n calls it to place trades.
Required env vars in Replit Secrets:
  POLY_API_KEY       = your Polymarket API key
  POLY_API_SECRET    = your Polymarket API secret
  POLY_PASSPHRASE    = your Polymarket passphrase
  POLY_PRIVATE_KEY   = your L2 private key (from Polymarket settings)
  WEBHOOK_SECRET     = any random string e.g. "leez123" (security token)
"""

from flask import Flask, request, jsonify
import os
import json
import traceback

app = Flask(__name__)

# ---- Install: py_clob_client ----
# In Replit, add to requirements.txt:
# flask
# py_clob_client

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, Side
    from py_clob_client.constants import POLYGON
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    print("WARNING: py_clob_client not installed. Run: pip install py_clob_client")


def get_client():
    """Create authenticated Polymarket client"""
    return ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=os.environ.get("POLY_PRIVATE_KEY"),
        creds={
            "apiKey": os.environ.get("POLY_API_KEY"),
            "secret": os.environ.get("POLY_API_SECRET"),
            "passphrase": os.environ.get("POLY_PASSPHRASE"),
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "clob_available": CLOB_AVAILABLE,
        "message": "Leez Polymarket executor is running"
    })


@app.route("/trade", methods=["POST"])
def place_trade():
    # Security check
    incoming_secret = request.headers.get("X-Webhook-Secret", "")
    expected_secret = os.environ.get("WEBHOOK_SECRET", "")
    if incoming_secret != expected_secret:
        return jsonify({"error": "Unauthorized"}), 401

    if not CLOB_AVAILABLE:
        return jsonify({"error": "py_clob_client not installed"}), 500

    try:
        data = request.get_json()
        token_id   = data.get("token_id")       # YES or NO token ID
        side       = data.get("side")            # "BUY"
        price      = float(data.get("price"))    # e.g. 0.62
        size       = float(data.get("size"))     # e.g. 0.35 (dollar amount)
        market_id  = data.get("market_id")
        question   = data.get("question", "")

        if not all([token_id, side, price, size]):
            return jsonify({"error": "Missing required fields: token_id, side, price, size"}), 400

        # Safety limits — protect the $8
        BANKROLL      = 8.00
        MAX_TRADE     = BANKROLL * 0.05  # 5% max = $0.40
        MIN_TRADE     = 0.10             # min $0.10
        STOP_BALANCE  = 5.00             # stop trading if balance drops below $5

        if size > MAX_TRADE:
            size = MAX_TRADE
        if size < MIN_TRADE:
            return jsonify({"error": f"Trade size ${size} too small (min ${MIN_TRADE})"}), 400

        # Place the order
        client = get_client()

        # Check current balance first
        try:
            balance_info = client.get_balance()
            current_balance = float(balance_info.get("balance", 0))
            if current_balance < STOP_BALANCE:
                return jsonify({
                    "error": f"Balance ${current_balance} below stop limit ${STOP_BALANCE}. Trading halted.",
                    "halt": True
                }), 400
        except Exception:
            pass  # If balance check fails, continue anyway

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=Side.BUY if side == "BUY" else Side.SELL,
        )

        signed_order = client.create_order(order_args)
        response     = client.post_order(signed_order, OrderType.GTC)

        return jsonify({
            "success": True,
            "order_id": response.get("orderID", "unknown"),
            "market_id": market_id,
            "question": question,
            "side": side,
            "price": price,
            "size": size,
            "status": response.get("status", "submitted"),
            "raw": response
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }), 500


@app.route("/outcome", methods=["POST"])
def check_outcome():
    """Check if a market has resolved and what the result was"""
    incoming_secret = request.headers.get("X-Webhook-Secret", "")
    expected_secret = os.environ.get("WEBHOOK_SECRET", "")
    if incoming_secret != expected_secret:
        return jsonify({"error": "Unauthorized"}), 401

    if not CLOB_AVAILABLE:
        return jsonify({"error": "py_clob_client not installed"}), 500

    try:
        data      = request.get_json()
        market_id = data.get("market_id")
        if not market_id:
            return jsonify({"error": "market_id required"}), 400

        client = get_client()
        market = client.get_market(market_id)

        resolved  = market.get("closed", False)
        outcome   = None
        if resolved:
            tokens = market.get("tokens", [])
            for t in tokens:
                if float(t.get("price", 0)) >= 0.99:
                    outcome = t.get("outcome", "Unknown")

        return jsonify({
            "market_id": market_id,
            "question":  market.get("question", ""),
            "resolved":  resolved,
            "outcome":   outcome,
            "tokens":    market.get("tokens", [])
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting Leez Polymarket executor on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
