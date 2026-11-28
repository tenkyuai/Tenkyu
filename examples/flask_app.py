"""Flask signup endpoint protected by Tenkyu.  Run: flask --app examples.flask_app run
(requires `pip install flask`; not a Tenkyu dependency)."""
from flask import Flask, jsonify, request

from tenkyu import Guard, signup_from_request

app = Flask(__name__)
guard = Guard()
TRUSTED_PROXIES = 1  # number of reverse proxies you control in front of this app (0 if none)


@app.post("/signup")
def signup():
    data = request.get_json(silent=True) or {}
    s = signup_from_request(data.get("email"), request.headers, request.remote_addr, TRUSTED_PROXIES, data.get("visitor_id"))
    decision = guard.check(s)
    if decision.action == "block":
        return jsonify(error="signup not allowed"), 403
    return jsonify(ok=True, credits=int(500 * decision.credit_fraction), needs_review=decision.action == "review")
