# Launch plan (DRAFT, nothing here has been done)

Written overnight on 2026-11-18 by the build loop. Every action that goes outside this folder (push, publish, post, submit, enable a setting) is marked **YOU**. Nothing was pushed, published, posted or sent, and nobody was contacted.

The one user: a solo builder or small team shipping an AI product with free credits, who is paying for tokens that farmers consume. The one pain: one person, many throwaway signups.

## 1. Ship checklist, run against the real repo

Status key: PASS = I ran it and it passed. FAIL = I ran it and it failed. UNKNOWN = could not be checked from here. NOT-DONE = needs you. Blocker = do not go public without it.

### Repo
| Check | Status | Evidence | Blocker? |
|---|---|---|---|
| README quickstart works copy-pasted | PASS | Extracted README python block 0, ran it with `python -I` from a clean venv that had only the built wheel installed: printed `allow []`, then `block [...]`, exit 0 | yes (passed) |
| `python -m tenkyu demo` and `tenkyu --help` work from the installed wheel | PASS | ran in the same clean venv | no |
| LICENSE present | PASS | MIT, holder "Tenkyu contributors" | **YOU** decide the holder name |
| .gitignore sane | PASS | pycache, egg-info, build, dist, venvs, db files, `INFINITY/` | no |
| Secrets in the working tree | PASS | pattern grep (sk-, AKIA, ghp_, api_key=) found nothing; scoreboard `secrets_found` is 0; no `.env` file | yes (passed) |
| Secrets in git history | NOT-DONE | this folder is not a git repo, so there is no history to scan. When you create the repo, `git log -p` once or run a scanner before the first push | yes, **YOU** |
| Issue templates, CI | NOT-DONE | no `.github/` folder | no |

### Package (PyPI)
| Check | Status | Evidence | Blocker? |
|---|---|---|---|
| Name `tenkyu` is free on PyPI | PASS at 2026-11-18 01:08Z | `GET https://pypi.org/pypi/tenkyu/json` returned 404. A name can be taken at any time, so re-check right before you upload | yes |
| Name free on TestPyPI | NOT-DONE | not checked | no |
| Metadata correct | PASS | name, version 0.1.0, MIT, Alpha, keywords, scripts entry. Fixed tonight: `license` as a table and the License classifier were deprecated and would stop building after 2027-02-18; now SPDX `license = "MIT"` and `setuptools>=77`. Build prints 0 warnings | no |
| Project URLs correct | PASS | URLs point at github.com/tenkyuai/tenkyu (organization created 2026-11-26) | no |
| Build sdist and wheel | PASS | `python -m build` produced both; wheel holds only `tenkyu/*` and the LICENSE | no |
| Install the built wheel in a clean venv | PASS | `pip list` there shows only tenkyu, pip, setuptools; zero dependencies | no |
| TestPyPI dry run | NOT-DONE | uploading is publishing: **YOU** (`python -m twine upload --repository testpypi dist/*`) | yes |
| PyPI page renders README links | FAIL | README links to `docs/...`, `examples/`, `CONTRIBUTING.md` etc. are relative. They work on GitHub and break on the PyPI page. Fix: absolute GitHub URLs, once the repo URL is confirmed | nice-to-have |
| Python 3.9 claim | UNTESTED | `requires-python >=3.9`, but tests ran on 3.11 and 3.13 only (README says so). Either test 3.9 or set `>=3.11` | nice-to-have |
| Typed package marker (`py.typed`) | NOT-DONE | not added | nice-to-have |

### Website (the landing page)
| Check | Status | Evidence | Blocker? |
|---|---|---|---|
| Numbers on the page match the code | PASS | `INFINITY/tools/landing_check.py`, 19 checks | yes (passed) |
| Works at phone width | UNTESTED | never rendered or screenshotted; **YOU** open https://claude.ai/artifact/M3P3PJYGvNWQGBWSffZwPK on your phone | yes |
| Install command copyable and true | FAIL until published | there is no `pip install tenkyu` until the package is on PyPI; the page and README correctly say "install from source" today | yes |
| "Preview build, not published" chip removed | NOT-DONE | remove before launch | yes |
| Logo | NOT-DONE | you said you design it; page uses a text wordmark | yes |
| Title, description, social preview image, favicon | NOT-DONE | the page is an artifact, not a site on a domain | no for a repo-first launch |
| Pricing unambiguous | PASS (by omission) | no prices shown; `docs/pricing.md` is a draft with untested numbers | no |

### Payments
Not applicable yet: nothing is for sale. The Cloud tiers in `docs/pricing.md` are not built. Before any checkout: test-mode purchase with webhook, refund path, terms and privacy pages (all NOT-DONE).

### Security basics
| Check | Status | Evidence |
|---|---|---|
| Dependency audit | N/A | zero runtime dependencies (verified in the clean venv). `pytest` is dev-only |
| Keys in client-side code | PASS | landing page is static; no keys, no data URIs (landing_check) |
| Rate limits on public endpoints | N/A | the library exposes no server |
| Vulnerability reporting path | NOT-DONE | `SECURITY.md` points to GitHub private vulnerability reporting; **YOU** enable it in the repo settings |

### After launch
| Check | Status |
|---|---|
| A place for feedback | NOT-DONE until the repo is public: GitHub issues |
| One number to watch | GitHub stars for week 1, PyPI downloads after publish |

**Verdict: not ready to go public yet, and the blockers are all yours to clear**: confirm the repo URL, scan git history at first push, check the landing page on a phone, remove the preview chip, publish to PyPI (so the install line is true), enable private vulnerability reporting.

## 2. Channel plan (zero outreach)

Pick at most two channels for week 1. Recommended: **GitHub** plus **one launch post (Show HN)**. Blunt version: a first Show HN post with an alpha library often gets no traction. That is normal; it still leaves a permanent, searchable page. Do not post the same text in several places on the same day.

| Channel | What goes there | Who does it | Status |
|---|---|---|---|
| GitHub repo | README (done), topics (see below), a release tagged v0.1.0 with the CHANGELOG text, `examples/` (done) | **YOU** | ready to push after the checklist blockers |
| GitHub topics | `fraud-prevention`, `abuse-prevention`, `signup`, `free-tier`, `python`, `llm`, `saas` | **YOU** | not set |
| PyPI page | description and keywords already in pyproject | **YOU** upload | not published |
| Show HN | `docs/launch-posts.md` draft 1 | **YOU** post; read the current Show HN guidelines first | written, not posted |
| r/Python | draft 2 | **YOU** read the subreddit's current self-promotion rules first; some require a specific showcase thread | written, not posted |
| dev.to | draft 3 (longer, how it works) | **YOU** | written, not posted |
| Search content | pages that answer what builders search. Candidates: "stop free trial abuse in an AI app", "gmail plus alias signup abuse", "detect disposable email at signup python". Each can be a short doc in `docs/` that links the library | me (can draft) / **YOU** publish | ideas only |
| Directories and awesome-lists | open a pull request to relevant awesome lists (fraud prevention, Python security) | **YOU** (a PR is outreach to maintainers: your call; they are not cold DMs) | to-do |
| Framework examples | `examples/flask_app.py`, `fastapi_app.py` exist (compile-checked, not run against a live server) | done | run them once for real before announcing |
| Friends feedback | template in `docs/pricing.md` | **YOU** | not started |

## 3. The 7-day experiment (pre-registered)

- Target: 25 GitHub stars and 3 people (not you) who run `python -m tenkyu demo` or open an issue, within 7 days of the first post. These numbers are a guess, not a forecast.
- Check: stars on the repo page; for installs, PyPI download counts include mirrors and bots, so treat them as an upper bound.
- After 7 days: if both are below half the target, the problem is more likely the pitch or the audience than the code. Rewrite the first two lines of the README and try one other channel, not all of them.

**Kill / pivot rule (set before launch so it is not decided later):** if, 60 days after the PyPI release, there are fewer than 5 non-author GitHub issues from real production use and fewer than 3 unprompted requests for a hosted version, do not build the paid cloud. Keep Tenkyu a free library and spend the time on the next project. See `docs/stress-test.md`.

## 4. What to do the morning after you read this
1. Open the landing link on your phone and PC. Tell me what looks wrong.
2. The repo is github.com/tenkyuai/tenkyu (moved from oriazuti100-a11y on 2026-11-26).
3. Scan history, enable private vulnerability reporting, fix the URLs if they differ.
4. Decide: publish to TestPyPI first (recommended), then PyPI.
5. Only then post, one channel at a time, from `docs/launch-posts.md`.
