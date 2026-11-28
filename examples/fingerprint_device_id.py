"""Use a device-fingerprint vendor's visitor id as Tenkyu's device_id.

Tenkyu does not fingerprint browsers itself. Services such as Fingerprint, Stytch or
Castle give your frontend a stable visitor id; send it to your server with the signup
form and pass it through. Tenkyu then flags many accounts on one visitor id.
"""
from tenkyu import Guard, signup_from_request

guard = Guard()

# What your web handler receives (fake values here so the example runs offline):
form = {"email": "new.user@acme.io", "visitor_id": "vis_8f3a2c"}
headers = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/120.0 Safari/537.36", "X-Forwarded-For": "198.51.100.23"}

signup = signup_from_request(form["email"], headers, remote_addr="10.0.0.5", trusted_proxies=1,
                             device_id=form["visitor_id"])
decision = guard.check(signup)
print(decision.action, decision.credit_fraction, decision.reasons)
