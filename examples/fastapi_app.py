"""FastAPI signup endpoint protected by Tenkyu.  Run: uvicorn examples.fastapi_app:app
(requires `pip install fastapi uvicorn`; not a Tenkyu dependency)."""
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from tenkyu import Guard, signup_from_request

app = FastAPI()
guard = Guard()  # one per process; use a shared store for multi-server setups (roadmap)
TRUSTED_PROXIES = 1  # how many reverse proxies YOU run in front of this app (0 if none)


class SignupBody(BaseModel):
    email: str
    visitor_id: Optional[str] = None  # optional: Fingerprint/Stytch/Castle visitor id


@app.post("/signup")
def signup(body: SignupBody, request: Request):
    remote = request.client.host if request.client else None
    decision = guard.check(signup_from_request(body.email, request.headers, remote, TRUSTED_PROXIES, body.visitor_id))
    if decision.action == "block":
        raise HTTPException(status_code=403, detail="signup not allowed")
    credits = int(500 * decision.credit_fraction)  # 500 = your free grant
    return {"ok": True, "credits": credits, "needs_review": decision.action == "review"}
