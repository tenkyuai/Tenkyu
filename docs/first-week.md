# Your first week with Tenkyu

A plain plan for the first seven days: watch first, label what you can, read your accuracy, then decide whether to enforce. In shadow mode Tenkyu scores every signup and records what it would have done, but nobody is refused and everybody gets the full free grant. Nothing about your users changes while shadow mode is on.

## Day 0: integrate in shadow mode

**Hosted.** A new account starts in shadow mode for 7 days **counted from when the account was created, not from when you finish integrating** (an account that existed before shadow mode was added is not in it). If you sign up for Tenkyu today but integrate on day 5, shadow mode has 2 days left, and the "7 days of data" this plan assumes is not there. So on the day you integrate, open Settings, find Shadow mode, pick "Keep shadow mode on for another" 7, 14 or 30 days and press Extend (shadow mode can run at most 30 days from today, and it cannot be switched back on once it has ended). Aim for about a week of live signups after your integration is deployed. Shadow mode belongs to the account, not to a key, and it applies to live keys; decisions made with a test key are practice and are left out of the accuracy numbers. Add the check to your signup route and ship it. Nobody is blocked while shadow mode is on. It ends by itself on its end date, shown in Settings; extend it before then.

**Library.** Turn on shadow mode and keep a decision log:

```python
from tenkyu import DecisionLog, Guard

guard = Guard(shadow=True, log=DecisionLog("decisions.jsonl"))
```

The log holds a short hash of each email and a decision id. It never holds a raw email, IP or device id.

## Days 1 to 6: label what you can

A label says whether a signup was a farmer, a real user, or "not sure". You do not need to label everything. Label the ones you know: accounts you banned for abuse, customers who paid, people you talked to. Skip the rest.

**Hosted.** On the Decisions page or the Review queue, label a decision with one click (farmer, real or not sure). Or, on the "Your accuracy" page, upload a CSV with the columns `decision_id,label`, where label is `farmer`, `real` or `not sure` (up to 10,000 rows and 1 MB). You can also download your labelled decisions from that page as a CSV, newest first.

**Library.** Make a labels file from your log, then fill in the last column:

```text
$ python -m tenkyu shadow-report decisions.jsonl --template labels.csv
```

The file has the columns `decision_id,time,action,would_action,shadow,score,reasons,label`. Write `farmer`, `real` or `not sure` in `label` (`1`/`true`/`yes` also mean farmer and `0`/`false`/`no` real). Rows you mark `not sure` are skipped and counted, and so are rows you leave blank or write something unrecognised in. To find out who a decision was, look up the time in your own signup table; the log and the labels file never contain the email.

## Day 7: read your accuracy

**Hosted.** Open the "Your accuracy" page. It counts only your live decisions that you labelled.

**Library:**

```text
$ python -m tenkyu shadow-report decisions.jsonl labels.csv --grant 3
```

`--grant` is the dollar value of one free grant (default 3). The join key is `decision_id`. Exit code 0 means a report was printed (or the labels template was written); 2 means the log or the labels file could not be used. The same view comes from `tenkyu evaluate` on the hosted label export (a CSV with `action` and `label` columns and no email or ip). It detects that file by itself, or you can force it with `--decisions`.

## What to look for

Flagged means review or block. In shadow mode the decision that counts is what Tenkyu would have done, not the logged `allow`.

- **Honest users wrongly flagged.** The most important line. Each one is a real person who would have been held back or refused. Look at the blocked-only number separately: a review is a softer check, a block is a refusal.
- **Money withheld.** Compare "withheld from farmers" with "withheld from real users". You want the first to be much larger than the second. The hosted page and the library report use the credit fraction recorded with each decision (on the hosted page, times the free credit per signup you had set in Settings when the decision was made, or the figure you type on that page). Only `tenkyu evaluate` on the hosted export has no credit fraction, so it estimates from the action (review holds back 75% of the grant, block 100%).
- **Precision and recall.** Precision is the share of flagged accounts that were farmers. Recall is the share of farmers that were flagged. Labels you chose to give are not a random sample, so treat both as a guide, not a guarantee.

## The caveat: fewer than 50 labels

With fewer than 50 labelled farmer or real decisions ("not sure" does not count) the percentages are too noisy to act on. One mislabelled row moves them by whole points. The hosted page and the library report both print a warning when you are under 50. If you have little data, extend the shadow period or label more before you enforce. They also warn when your labels cover only farmers or only real users, because then precision, recall or the share of real people flagged cannot be read at all.

## Then decide

- Few honest users flagged, farmers' money clearly withheld: turn shadow mode off (hosted: in Settings, under Shadow mode, choose "Switch to live now"; it applies to the whole account, and it also ends by itself on its end date; library: `Guard(shadow=False)`) and start enforcing.
- Too many honest users flagged: stay in shadow mode, look at the reasons behind those decisions, and tune before you enforce. Review can be your first step: by default it releases only a quarter of the free credit and sends the signup to a check, instead of refusing it.
- Not enough labels to say: keep watching and label more.

Share your numbers (counts and rates only, never emails, IPs or logs) with the [shadow-week results form](https://github.com/tenkyuai/tenkyu/issues/new?template=shadow-week-results.yml). If honest users were flagged, see [What to do about honest users who get partial credit](https://github.com/tenkyuai/tenkyu/blob/main/docs/flagged-users.md).
