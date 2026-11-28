# Launch post drafts (WRITTEN, NOT POSTED)

Do not post any of these until the blockers in `docs/launch.md` are cleared and the install line is true. Posting is **YOU**. Read each community's current rules first; I did not verify them. Replace anything in [brackets] with your own words. Every claim below is taken from the README or `docs/benchmark.json`; if you change the library, re-check the numbers (`python3 INFINITY/tools/landing_check.py`).

Rules for yourself: post as the builder, say it is early, answer questions honestly, never ask for upvotes or stars, no second account, no sending the link to people to vote.

---

## 1. Show HN

**Title:** Show HN: Tenkyu: find out how many of your AI app's free credits went to farmers

**Body:**

[One line about you, in your words.]

AI apps give away free credits, and some people farm them with many signups. Before you add any protection, you probably want to know how bad it is. Tenkyu replays your own signup export offline and tells you:

    pip install tenkyu
    python -m tenkyu audit signups.csv --grant 3 --out flagged.csv

Nothing leaves your machine; the screen shows counts and reasons, and flagged.csv holds the rows to look at. `python -m tenkyu audit --sample` shows the output on bundled synthetic data.

If the number is worth acting on, the same library scores signups live (allow, review or block, with reasons), has a shadow mode that logs what it would have done without refusing anyone, and verifies Clerk and Supabase signup webhooks. Python, MIT, no dependencies, no network calls, heuristics only.

Honest numbers, from a synthetic benchmark I generated myself (so treat them as optimistic): F1 0.82 against 0.32 for email dedupe plus a disposable list; no honest user blocked, 3.7% sent to review. At default settings it catches none of the farmers who use a new inbox, home IP and device every time. `Guard.for_ai_apps()` halves what they get, at the cost of honest users starting with half a grant until they verify; only a link a farmer has to reuse (card, verified phone) closes the gap. The README has the table.

What I most want to know: if you run the audit on your own signups, was the number surprising?

Repo: [GitHub URL once confirmed]

---

## 2. r/Python

(Check the sub's rules first. If self-promotion must go in a showcase thread, post it there, not as a separate thread.)

**Title:** I wrote a dependency-free library to score signups for free-tier abuse (early access, feedback wanted)

**Body:**

I built Tenkyu because AI products that give free credits attract people who sign up many times. It is pure standard library Python: `Guard().check(Signup(email=..., ip=..., device_id=..., user_agent=...))` returns a Decision with an action (allow, review, block), a score, and the reasons.

Things I tried to get right: scores combine as a noisy-or so independent signals add up; every decision carries readable reasons; a shared device alone only reaches review, never block; custom signals are plain functions and malformed ones are ignored instead of crashing signup; storage is in-memory or SQLite.

It is tested on Python 3.11 and 3.13 (187 tests). The package metadata says 3.9+, which I have not tested. The benchmark is synthetic and written by me, so please treat it as a sanity check, not evidence. Details and the miss list (residential-IP farmers are not caught) are in the README.

Repo: [GitHub URL once confirmed]. I would like code review as much as feedback on the idea.

---

## 3. dev.to (longer; how it works)

**Title:** How I score signups for free-credit abuse without machine learning

**Outline to write in your own voice (about 600 to 900 words):**
1. The problem in one paragraph: free credits cost real tokens; one person, fifty emails.
2. Why exact email dedupe fails: `john@gmail.com`, `j.o.h.n+free1@gmail.com` and `john+free2@googlemail.com` are the same inbox. Show the demo table from the README.
3. The noisy-or: each signal gives a score from 0 to 1; combined risk is 1 minus the product of (1 minus score). Review at 0.35, block at 0.70. Credits: allow 100%, review 25%, block 0.
4. The part I got wrong and fixed: raising the history window to 30 days made three honest users get blocked by device reuse alone; I lowered the score for the third account on a device from 0.85 to 0.65 so a shared device only reaches review. (This is in CHANGELOG.md.)
5. The benchmark, with the caveat in the first sentence: synthetic, written by me, optimistic. Show recall by strategy including the 0% for residential.
6. What is not built: a shared store for multiple servers. Link docs/shared-store.md.
7. Ask: which signals would you add? Link the repo.

Do not paste the full README into the post. Link it.
