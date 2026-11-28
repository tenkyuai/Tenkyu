# Tenkyu positioning (draft, untested)

**One line:** The open-source way to stop free-credit farming in your AI app, in ten lines of Python.

**Who it's for:** Solo builders and small teams shipping an AI product with free credits, who can't justify a $100+/mo fraud platform yet.

**The problem:** One person, fifty throwaway emails, your inference bill. Stripe reports AI startups with self-serve free trials see roughly 10x more attempted abuse than enterprise AI.

**Why Tenkyu:**
- Open source (MIT), runs inside your app. No vendor, no per-call fee, no data leaving your servers.
- Explainable: every decision lists the exact reasons, so you can tune it and tell a real user why.
- Built for credit farming specifically: email aliases, disposable domains, same-IP bursts, many accounts per device, lookalike emails.
- Works with what you have: pass a Fingerprint/Stytch/Castle visitor ID as `device_id` for stronger device linking.

**What it is not:** a device-fingerprinting service, an identity-verification product, or a wall against a determined attacker. It raises the cost of farming; pair it with credit limits and a card-on-file step for bigger grants.

**Business model (hypothesis):** free core; later a hosted option with a shared store across servers, a review dashboard and abuse-list updates. See INFINITY/notes.md for competitor pricing and sources.
