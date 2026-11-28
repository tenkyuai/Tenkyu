# Friend trial: two weeks in shadow mode (draft, not sent)

Goal: one real app runs Tenkyu without it refusing anyone, then we compare what it would have flagged with what the owner knows was abuse. That case study is the launch post.

## Message to send (edit into your own words)

Hey, I built a small open-source library that spots people farming free credits with many signups. Could I try it on your app for two weeks? It runs in "shadow mode": it never blocks anyone, it only writes a log on your server. Two steps:

1. Run `python -m tenkyu audit your_signups.csv` once on an export of your signups. It runs on your machine, nothing is sent anywhere. Tell me the counts if you're comfortable.
2. Add three lines to your signup code (below). After two weeks, run `python -m tenkyu report decisions.jsonl` and send me the output. It holds counts and reasons, no emails.

```python
from tenkyu import DecisionLog, Guard, Signup

guard = Guard(shadow=True, log=DecisionLog("decisions.jsonl"))


def on_signup(email, ip, user_agent):  # call this from your signup handler; ignore the result for now
    guard.check(Signup(email=email, ip=ip, user_agent=user_agent))
```

## What to collect after two weeks
- The `tenkyu report` output.
- The owner's own view: which of the flagged accounts were really abuse, and any abuse it missed.
- Permission to quote the numbers, named or anonymous.
