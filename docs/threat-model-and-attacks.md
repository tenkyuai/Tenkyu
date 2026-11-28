# Threat model and attack drills

What Tenkyu defends against, what it does not, and every attack drill run against it on 2026-11-26 and 2026-11-27. Source: the overnight build log of 2026-11-26 and 2026-11-27 (kept outside the repo), `CHANGELOG.md`, `docs/heldout-benchmark.md` and the tests named below.

How to read the drills:

- The attackers were AI red-team agents (Claude instances), not human penetration testers. Each night-1 run was repeated 3 times. On night 2 each phase had one or two attack rounds and a fresh review, and the full test suite ran 3 times green at the end of the phase.
- Every attack ran against Tenkyu's own code, on localhost, with mocked GitHub, payment and Gemini services. Nothing outside Tenkyu was touched.
- Library tests are in `tests/`. Hosted tests are in `hosted/tests/` and need Postgres (`TENKYU_TEST_PG_DSN`), so they run in the CI Postgres job and skip on a plain checkout. `tests/attacks/` replays the key library attacks on every release.
- The numbers are from synthetic data until real customers label real signups.

## 1. What Tenkyu defends against

| Threat | Covered? | How, and how well | Evidence |
|---|---|---|---|
| One inbox under many addresses (Gmail dots and plus, `googlemail.com`, Yahoo hyphen, Fastmail subdomain, iCloud, Proton) | Yes | Addresses are normalized to one identity; the repeat is blocked | `lazy_alias` 91.7% flagged (held-out) |
| Look-alike letters from fullwidth forms, soft hyphen, zero-width and bidi characters | Yes | NFKC, then format characters removed; `obfuscated_email` scores what was hidden | `unicode_trick` group 95.7% flagged on the night-1 set, which Phase 9 was tuned on (37.8% before tuning); `unicode_local` 99.1% on the blind v2 set |
| Look-alike letters from other scripts (Cyrillic "а" in `gmаil.com`), `0` for `o`, one-typo and `.con` domains | Yes, flagged; not merged | `lookalike_domain` (0.75) and `mixed_script_email` (0.50), from a TR39 confusables subset. The address is still a different address from the real one | `lookalike_domain` 87.0% flagged (38.6% before) on the blind v2 set; `tests/test_lookalike.py`; verify email ownership yourself |
| Throwaway email domains | Partly | Bundled list: 100% of domains on it, 65% (46 to 81) of 17 the generator used that are not on it | `docs/heldout-benchmark.md` |
| Scripted bursts from one IP, automation user agents | Yes | `ip_burst`, `ip_velocity`, `automation_agent`; ports and IPv6 /64 handled | `datacenter_bot` 90.2% flagged |
| Reused device id | Partly | `device_reuse` alone reaches review, never block | `device_reuse` 90.2% flagged; honest families cost below |
| Reused card or verified phone | Yes, if you pass them as `links` | `link_reuse` blocks; values stored hashed (HMAC with `link_secret`) | `card_or_phone_reuse` 83.3%; a default `Guard()` without links catches none |
| Farms rotating addresses inside one network | Partly | `network_burst` and `network_day` (IPv4 /24, IPv6 /48), only next to another risk signal | tests in section 2, phase 0 |
| Rotating residential proxies | Weakly | Caught only when something else links the accounts; `wide_spread` and `regular_timing` are soft and add only next to another signal | `rotating_proxy` 46.2% flagged (night-1 set); `proxy_rotate` 34.1% (blind v2 set) |
| Device hopping across networks | Yes | `device_farm`: 6+ accounts on one device in 7 days across 3+ /24 networks. One network (school lab, family) stays soft | `tests/attacks/test_honest_v2.py` (includes the case where a big family can look like a farm) |
| Patient farmer: fresh inbox, IP and device per account, spread over time | No | Nothing links the accounts. `Guard.for_ai_apps()` halves the grant; only a reused card or phone stops them | `patient_residential` 0.5% flagged; 20% of extra grants still received at default settings |
| Farmer who buys a new card or phone per account | No | Links only help while the identifier is expensive to change | `tests/attacks/test_attack_known_limits.py` |
| The first account of every farmer | No | No signup check can know it is the first of many | README, held-out section |
| Honest users wrongly flagged | Tracked, not solved | Families on one device (60% flagged, mostly partial credit), work plus personal account on one device (50%), carrier NAT (11%). Clean distinct identities behind one IP get partial credit, never a block, up to 60 an hour and 200 a day per IP; blind v2: honest blocked 12.5% to 1.6%, honest flagged unchanged 20.4% | `docs/heldout-benchmark.md`, `benchmarks/FROZEN-v2.md` |
| Attacker crashes or stalls `check` with crafted input | Yes | Lone surrogates, huge ids, raising custom signals are handled | `tests/attacks/test_attack_hostile_input.py` |
| Spoofed `X-Forwarded-For` | Yes | Ignored unless `trusted_proxies` is set; then the entry the trusted proxy appended is used | `tests/attacks/test_attack_ip_evasion.py` |
| Farmer triggers the surge guard to deny honest new users | No (opt-in rule) | Documented; verified phone or card links are exempt | README "Honest limits" |
| One hosted customer poisons the shared network | Yes (hosted) | A block needs 2 to 3 independent reporters; daily report caps; 90-day expiry; disputes; no way to probe | section 2, phase 1 |
| Cross-customer data access, session theft, CSRF, XSS on the dashboard | Held in drills (hosted) | Server-side hashed sessions, CSRF tokens, strict CSP, per-customer queries | section 2, phase 2 |
| Payment tricks: replayed or forged webhooks, double spend, refund abuse | Held after fixes (hosted) | Append-only ledger, once-only references, signature check, refund clawback | section 2, phase 3 |
| Prompt injection against the AI watcher | Held (hosted) | It cannot act, model text never reaches push, secrets are not in its reach | section 2, phase 4 |
| Database outage on the hosted API | Fail open | Answers from a per-instance engine with `"degraded": true`, nothing stored or charged. Your signup page keeps working and the shared network is skipped | `hosted/API.md`, `hosted/README.md` |
| Farmer or outage abuse against a copy of the Supabase or Vercel recipe | Held in drills (recipes) | Account first, then one check per new account; a 25% grant when Tenkyu cannot score; 4xx fails closed; same `{ok: true}` for new, existing and blocked; per-IP and per-device rate limits (per instance only) | section 2, phase 7; the Supabase client-IP header is unverified |
| Shadow mode leaks or can be escaped | Held after fixes (hosted) | Live keys only, set per account, visible on the dashboard pages, ends on its date or when the owner switches to live in Settings | section 2, phase 8 |
| Secrets printed by the deploy preflight | Held after fixes | Config errors name the variable, never its value; URLs with a login are dropped; output is redacted | section 2, phase 8 |
| Guessing an email from a decision id; label races that corrupt network reports; label uploads that lock out other customers | Held after fixes (library and hosted) | Random decision ids; labels use the same reporter rules as outcomes; one lock order; upload limits | section 2, phase 10 |
| Attack on a deployment that does not exist yet | Unknown | The hosted service was never deployed or load-tested against the internet | section 3 |

## 2. Attack drills run on 2026-11-26 and 2026-11-27

Test names are `file::test`, under `tests/` unless the file starts with `test_api`, `test_billing`, `test_dashboard`, `test_audit_page` or `test_watcher` (those are under `hosted/tests/`). A drill that "held" found nothing to fix, and its test is the regression guard.

### Phase 0: library, 18 tactics, local library only (done about 23:40)

Result of the phase: real bugs found and fixed, tests 410 to 616. Known limits unchanged (section 3). A fresh code review of the same work found 7 issues; all fixed.

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| New alias families: `x@user.fastmail.com`, `me.com`/`mac.com`, `protonmail.com`/`pm.me` | Bypass: each counted as a new inbox | Merged into one identity per family | `test_v022_aliases.py::test_fastmail_subdomain_collapses`, `::test_e2e_proton_alias_swap_not_full_credit`, `::test_e2e_fastmail_and_icloud_alias_not_full_credit` |
| Honest campus: 200 users on one /24 over 8 hours | False positives: 110 to 120 sent to review | `network_*` signals count only next to another real risk signal; burst needs 15+ alone | `test_v022_redteam.py::test_campus_of_200_honest_users_in_8_hours_stays_under_5_percent_review` |
| Family on one device, busy carrier network | False positive: block | Shared device plus network volume alone is at most review | `test_v022_redteam.py::test_family_on_a_busy_network_is_reviewed_not_blocked` |
| Regression check on the two rows above: a real farm on one network that reuses devices or aliases | Still caught | None needed | `test_v022_redteam.py::test_farm_on_one_network_with_reused_devices_is_still_caught` |
| Alias first, then the owner signs up with the plain address | Two full grants for one inbox | Owner gets review with 25% credit, never blocked | `test_v022_redteam.py::test_owner_after_aliases_gets_review_not_full_grant_and_not_block` |
| Unicode tricks: fullwidth letters, soft hyphen, combining joiner, bidi marks | Bypass of alias and disposable checks | NFKC, then format characters removed | `test_v022_redteam.py::test_unicode_variants_normalize_to_the_plain_address`, `::test_unicode_trick_disposable_domain_is_flagged` |
| Cyrillic look-alike domain | Flagged as `lookalike_domain` since night 2; still not merged with the real address | The address stays different on purpose (normalization is unchanged) | `test_v022_redteam.py::test_cyrillic_lookalike_domain_is_left_alone`, `tests/test_lookalike.py` |
| Same phone or card written in different formats | Bypass: new format, new link | Values normalized before hashing; phone `+`, `00` and `(0)` handled | `test_v022_redteam.py::test_phone_variants_share_one_key`, `::test_card_variants_share_one_key_but_other_ids_stay_apart`, `::test_guard_matches_link_variants` |
| Low-entropy links reversible from a plain hash | Weakness: phone numbers can be guessed from SHA-256 | Optional `link_secret` (HMAC); a one-time notice without it | `test_v022_aliases.py::test_link_key_with_secret_is_hmac` |
| Rotating source ports | Bypass: each port a new IP | Port dropped before keying | `test_v022_redteam.py::test_rotating_ports_do_not_dodge_ip_burst` |
| Rotating IPv6 /64s inside one /48 | Bypass of network rules | Network rules group IPv6 per /48 | `test_v022_redteam.py::test_rotating_ipv6_64s_inside_one_48_feed_the_network_rules` |
| Malformed email versus missing email | Malformed got review; phone-only users must not be blocked | Malformed blocks; missing email gives no signal | `test_v022_redteam.py::test_invalid_email_blocks_with_a_clear_reason`, `test_v022_review.py::test_missing_email_is_not_blocked` |
| CUBOT phones | False positive: "bot" substring | Whole-token match | `test_v022_redteam.py::test_cubot_phones_are_not_automation` |
| Custom signal named like a built-in soft signal | Downgraded by name | Softness only for built-in signals | `test_v022_review.py::test_custom_signal_with_a_soft_name_is_not_soft` |
| Lone surrogate in a device id or IP | `UnicodeEncodeError` inside SQLite | Keyed by SHA-256 | `test_v022_review.py::test_surrogate_inputs_never_raise_with_sqlite` |
| Very large disposable list | Linear scan per signup | Suffix lookup, same results | `test_v022_review.py::test_disposable_matches_old_linear_scan_on_a_big_list` |

### Phase B: held-out benchmark (done 23:18)

An adversarial generator rather than an attack run: a separate AI agent wrote 8 farming tactics and hard honest groups without seeing the rules. The rules were frozen first (`benchmarks/FROZEN.md`).

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| Blind generator, 20 seeds, `Guard()` | 70.2% of farmer accounts flagged (58.7 to 78.6), 83.4% precision, 4.6% of honest users flagged, 1.4% blocked | None. Weak spots recorded, not tuned on these seeds | `test_heldout_bench.py` |
| Review of `tenkyu evaluate` and the docs | 6 findings: CSV parsing, row cap, `--grant` checks, README framing | All fixed | `test_evaluate.py` |

### Phase 1: hosted API (done 03:26), 2 red teams, localhost, 3 runs each

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| One customer reports an email as `farmer` to block it for everyone (network poisoning) | HIGH: worked | Email and device need 3 reporters to block (2 for review); phone and card need 2; reports capped per day; 90-day expiry | `test_api_redteam.py::test_one_reporter_cannot_block_an_email_for_other_customers`, `::test_email_and_device_need_three_reporters_to_block_and_two_make_a_review`, `::test_phone_and_card_one_reporter_is_invisible_two_are_a_block`, `::test_reports_per_reporter_per_day_are_capped`, `::test_network_rows_expire_after_90_days_and_can_be_pruned` |
| Reporting the same identity twice through one decision; victims cannot undo | Part of the same HIGH finding | Network-flagged decisions cannot be reported; disputes cancel reports | `test_api_redteam.py::test_a_network_flagged_decision_cannot_be_reported`, `::test_victims_saying_real_dispute_a_hash_and_two_disputes_stop_it_scoring` |
| Probing whether an identity was reported | Part of the same HIGH finding ("no probing") | One generic signal and reason; a single report is invisible; ineligible customers get no lookups | `test_api_review.py::test_a_single_report_of_a_phone_or_card_leaks_nothing_in_any_field`, `::test_an_ineligible_customer_cannot_look_the_network_up_at_all` |
| Flood of unknown API keys; reset a rate-limit bucket by flooding new ones | MEDIUM: worked | Unknown keys never get buckets; negative cache; exhausted buckets cannot be evicted by a flood | `test_api_redteam.py::test_pre_auth_ip_bucket_limits_and_never_creates_buckets_for_unknown_keys`, `::test_flooding_unknown_buckets_cannot_reset_an_exhausted_one`, `test_api_review.py::test_negative_cache_refuses_a_made_up_key_without_the_database` |
| Backdated `ts` to hide an alias burst | MEDIUM: worked | A `ts` more than 300 s from server time is ignored | `test_api_redteam.py::test_backdated_ts_cannot_hide_an_alias_burst`, `::test_client_ts_outside_300_seconds_of_server_time_is_ignored` |
| `request_id` reused with another body | MEDIUM: worked | 400 when the body differs | `test_api_redteam.py::test_request_id_reused_with_a_different_body_is_a_400` |
| Spoofed `X-Forwarded-For` against the IP rate limit | Review finding | Header used only with `TRUST_PROXY`; rightmost front-end entry | `test_api_redteam.py::test_x_forwarded_for_is_used_only_with_trust_proxy` |
| Review: lock contention answered "allow" from the empty fail-open engine | HIGH | Lock timeout is a 429, never a fail-open answer | `test_api_review.py::test_a_lock_timeout_never_fails_open_and_parallel_signups_are_429_not_degraded` |
| Review: pool starvation tripped a global circuit breaker | HIGH | Breaker opens only on lost connections | `test_api_review.py::test_pool_exhaustion_is_busy_not_down_and_never_opens_the_breaker` |
| Review: short bursts from one customer got 429 | Review finding | Bursts queue | `test_api_review.py::test_a_short_burst_for_one_customer_queues_instead_of_429` |

### Phase 2: dashboard and audit page (done 06:28), localhost, mocked GitHub, 3 runs

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| OAuth state forgery or reuse, session fixation, cross-site POST | Held | n/a | `test_dashboard.py::test_state_mismatch_is_rejected`, `::test_state_is_single_use_and_expires`, `::test_every_post_needs_the_csrf_token` |
| Customer A reads or acts on customer B's data (IDOR) | Held | n/a | `test_dashboard.py::test_customer_a_cannot_see_or_act_on_customer_b` |
| Script injection through decision reasons or names | Held | n/a | `test_dashboard.py::test_every_page_escapes_decision_reasons_and_names` |
| Replaying the "new key" response cookie to mint a second key | MEDIUM: worked | One-time nonce; a used nonce makes no key | `test_dashboard.py::test_reloading_or_replaying_the_key_form_creates_no_second_key`, `::test_a_used_nonce_makes_no_key_for_another_customer` |
| Inflated "kept from farmers" dollar figure | MEDIUM | Counts only likely-farmer decisions at the grant of the day | `test_dashboard.py::test_withheld_counts_blocks_and_unapproved_reviews_and_holds_are_a_separate_line` |
| Path that climbs out and back to reach the build script | Fixed finding, severity not recorded | Path normalization | `test_audit_page.py::test_build_script_is_not_reachable_by_a_path_that_climbs_out_and_back` |
| Spreadsheet formulas in CSV column names (header formula injection) | Fixed finding, severity not recorded | Neutralised before display and export | `test_audit_page.py::test_bridge_neutralises_spreadsheet_formulas`, `::test_bridge_neutralises_formulas_in_column_names_too` |
| Missing security headers on some routes | Fixed finding, severity not recorded | CSP, `nosniff` and HSTS on every route | `test_dashboard.py::test_csp_and_security_headers_on_every_page`, `::test_every_route_sends_nosniff_and_hsts_when_public_url_is_https` |
| OAuth redirect built from the Host header | Fixed finding, severity not recorded | `PUBLIC_URL` required; redirect never from the request | `test_dashboard.py::test_redirect_uri_never_comes_from_the_host_header`, `::test_github_sign_in_needs_public_url_and_other_config_is_checked` |
| Audit page makes a third-party request or renders CSV as HTML | Held (the log records no third-party requests) | n/a | `test_audit_page.py::test_page_and_scripts_name_no_other_origin`, `::test_audit_js_never_writes_html`, `::test_hostile_csv_is_rendered_as_text` |

An accessibility review of the same pages found 8 issues (button names, live region and focus, contrast, copy); all fixed. It is not an attack and has no row above.

### Phase 3: prepaid credits (done about 07:35), 2 instances on one database, mocked provider, 3 runs

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| Parallel checks and parallel first checks to double-spend or double-grant | Held | n/a | `test_billing.py::test_50_parallel_checks_on_a_balance_of_10_charge_exactly_10`, `::test_free_grant_is_written_once_even_with_20_concurrent_first_checks` |
| Replayed webhook; same order id for another customer | Held | n/a | `test_billing.py::test_replayed_webhook_credits_nothing_more`, `::test_the_same_order_id_for_another_customer_is_not_credited_twice` |
| Forged or unsigned webhook | Held | n/a | `test_billing.py::test_forged_or_unsigned_webhooks_are_400_and_credit_nothing` |
| Test-mode keys or orders reaching the live ledger | Held | n/a | `test_billing.py::test_test_mode_keys_never_spend_and_never_run_out`, `::test_test_mode_acceptance_refuses_to_start_on_an_https_public_url` |
| Month rollover tricks | Held | n/a | `test_billing.py::test_free_checks_restart_at_utc_midnight_and_do_not_roll_over` |
| Buy, spend, refund: keep the credits | MEDIUM: worked | Refunds mirrored from the provider (full and partial clawback) | `test_billing.py::test_buy_burn_refund_takes_the_checks_back_once`, `::test_partial_refunds_claw_back_proportionally_rounded_up_and_cumulatively` |
| Review: signed webhook answered 200 but could not be applied, so money was taken and nothing credited | MEDIUM | Stored as pending, 503 with retry, applied later | `test_billing.py::test_unknown_customer_is_stored_as_pending_answered_503_and_logged`, `::test_a_refund_before_its_order_is_stored_answered_503_and_applied_when_the_order_is_credited` |
| Review: test keys bypassed the storage cap | MEDIUM | Bounded decision rows per day | `test_billing.py::test_test_keys_store_a_bounded_number_of_decisions_a_day_and_are_still_answered` |

Five low-severity issues in the first run and three in the review were also fixed. The log does not list them one by one.

### Phase 4: AI watcher (done about 08:45), all HTTP mocked, 3 runs

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| Hostile customer text and a hostile fake Gemini trying to make it act or leak | Held: it never acted and never leaked | n/a | `test_watcher.py::test_support_drafts_are_stored_as_drafts_and_the_message_is_fenced_as_untrusted`, `::test_no_emails_or_ips_reach_ntfy_gemini_or_the_digest_whatever_the_data_holds` |
| Model text reaching ntfy, or posing as an escalation | Held | n/a | `test_watcher.py::test_model_text_can_never_contain_escalate_or_the_watchers_own_markers`, `::test_model_text_has_no_urls_or_bare_domains` |
| Held escalation lost when its cause disappeared | MEDIUM | Kept in state and sent later | `test_watcher.py::test_held_escalation_is_kept_even_after_its_cause_disappears_and_goes_out_merged_with_the_next` |
| Two runs at once send double pushes | MEDIUM | Run lease and workflow concurrency group | `test_watcher.py::test_two_simultaneous_runs_send_one_push_and_one_digest` |
| A failing money check going silent | MEDIUM | A failing critical check escalates | `test_watcher.py::test_a_failing_critical_check_escalates_as_security` |
| Invisible or control characters in output; excess database rights; unpinned Actions | Low | Sanitising; least-privilege role; all GitHub Actions pinned to SHAs | `test_watcher.py::test_every_delivery_path_strips_invisible_characters`, `::test_readme_role_cannot_read_secrets_and_sees_new_tables_and_runs_every_check`, `::test_workflow_secrets_only_reach_the_watcher_step_and_checkout_keeps_no_credentials` |

### Phase 7: Supabase and Vercel recipes (night 2, about 20:00 to 20:50), AI red team and honest-user round, local only

Result of the phase: two high findings in the first round, two more in the review, all fixed. The recipes have not run on a real Supabase or Vercel project. Tests: JS client 22, Next.js recipe 23, Supabase recipe 48, plus the Python suite (1474 at the end of the phase).

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| Slow or flood Tenkyu so the recipe fails open: 25 sign-ups at a 300-credit grant | HIGH: worked, a full grant on failure gave 7,500 credits | Fail open grants 25% (`FAIL_OPEN_CREDIT_FRACTION`, 1,875 credits for the same 25) | `handler_test.ts`: "25-account farm during an outage: 25 x 75 = 1875 (was 7500), and one IP is throttled anyway"; `clients/js/test/client.test.mjs`: "a flood that exhausts Tenkyu's rate limit does not turn into full grants" |
| Review: a 200 answer flagged `degraded: true` with `credit_fraction` 1 | HIGH: a full grant from an empty engine | Capped at the fail-open share; its decision id is stored as null | `client.test.mjs`: "a 200 with degraded:true and credit_fraction 1 is capped at failOpenCreditFraction"; `handler_test.ts`: "a 200 flagged degraded:true with credit_fraction 1 is capped, and stores no decision id" |
| Review: `request_id` made from email and day; a retry with another device or case got a 400, and a 400 counted as degraded; anyone could burn a victim's id by submitting the email first | HIGH | Account-first flow: the id is `sha256("tenkyu-signup:" + user_id)`, so it is unique per account; a Tenkyu 4xx fails closed | `handler_test.ts`: "griefing: submitting a victim's email first cannot burn the victim's check or hand anyone a free grant", "case change on a retry: the second submit is a duplicate, never a Tenkyu 400 turned into a grant" |
| Malformed, private or spoofed client IPs; wrong hop count putting everyone in one bucket | Found; fixed | Strict global-IP parser, `x-real-ip` only on Vercel, rightmost entry on Supabase, no IP bucket when no valid IP; IPv6 keyed by /64 | `handler_test.ts`: "clientIp: private, loopback, link-local, CGNAT, ULA and unspecified addresses are never a client IP"; `signup_test.ts`: "IP: x-real-ip only; strict IPv4/IPv6; ports, garbage, zone ids dropped; x-forwarded-for never used" |
| Request floods and large bodies | Found; fixed | Per-IP and per-device token buckets before the Tenkyu call; body read as a stream and cut at 8 KB | `handler_test.ts`: "per-IP limit: a flood from one IP stops reaching Tenkyu and gets 429", "body cap: a chunked stream with no Content-Length is cut off at the cap, not buffered" |
| The browser reads the decision id or credits of other rows in the `credits` table | Found; fixed | Private `credits` columns (the browser reads only `user_id` and `granted` of its own row) | `sql/credits.sql`; the recipe README describes it. No automated test for the RLS rules |
| Review: a 403 on block ("am I flagged?") and different answers for existing emails | Oracle; fixed | Same `{ok: true}` for new, existing and blocked sign-ups; no `user_id` in the answer | `signup_test.ts`: "block: account is created with 0 credits and the answer is the same {ok:true} (no 403 oracle)", "duplicate email answers 200 {ok:true} like a fresh signup (no enumeration)" |
| Honest round: WebViews without `crypto.randomUUID`, retries after a weak password, one overall client deadline | Honest groups hit by recipe bugs were blocked or crashed; fixed | Device id works in old WebViews; local rejection happens before any Tenkyu call; client has one deadline including the 429 wait | `browser/device-id_test.ts`; `handler_test.ts`: "retry after a weak password: rejected locally with no Tenkyu call, the corrected retry is checked once"; `client.test.mjs`: "one deadline: slow first attempt + Retry-After wait + retry never exceed timeoutMs" |
| Many honest users behind ONE shared IP (found here, fixed in phase 9) | CGNAT: 27 of 40 blocked; school: 58 of 60 blocked | Library crowd rule, see phase 9 | `tests/attacks/test_honest_v2.py` |

Not fixed, by design: the rate limiter is per instance and in memory, so it is not a defence against a distributed flood. The recipes say so and point to a shared store.

### Phase 8: launch readiness (built 20:43 to 21:03; attacked and fixed at the start of phase 9), attack round and fresh review

Targets: deploy preflight, dashboard onboarding checklist, shadow mode. The shadow escape, shadow leak and onboarding key-exposure attacks held. Three preflight secret leaks and several shadow visibility problems were found and fixed. The log does not record severities.

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| A password inside `PUBLIC_URL` printed in the checklist | Leak | Any URL with a login is dropped from every checklist line | `test_phase8_attacks.py::test_a_password_inside_public_url_is_not_printed_in_the_checklist`, `test_phase8_fixes.py::test_a_public_url_with_a_login_is_dropped_from_every_checklist_line` |
| A malformed `PUBLIC_URL` made `urlsplit` raise, and the traceback held the password | Leak | Parser errors are caught; config errors name the variable, not its value | `test_phase8_attacks.py::test_a_malformed_public_url_does_not_crash_with_its_password_in_the_traceback`, `test_phase8_fixes.py::test_config_errors_name_the_variable_and_not_its_value` |
| A secret printed in its escaped form (`repr`) escaped the redaction | Leak | The escaped form is registered for redaction too | `test_phase8_attacks.py::test_redaction_survives_repr_escaping_in_config_errors`, `test_phase8_fixes.py::test_protect_also_registers_the_repr_escaped_form` |
| Shadow escape by tampering, CSRF or reaching another customer's settings | Held | n/a | `test_shadow_mode.py` |
| Review: shadow decisions looked like real blocks on Decisions and Review, the review count included them, onboarding said nothing, test keys could be shadowed | Visibility findings | Rows are tagged "shadow: allowed anyway", banners show only while shadowed, the sidebar count excludes shadow rows, test keys are never shadowed | `test_phase8_fixes.py::test_decisions_and_review_tag_shadow_rows_and_show_a_banner_only_while_shadowed`, `::test_the_sidebar_review_count_ignores_shadow_decisions`, `::test_a_test_key_is_never_shadowed_but_a_live_key_is` |

The preflight tests for the database steps need Postgres (`hosted/tests/test_preflight.py`).

### Phase 9: detection v2 (night 2, about 21:05 to 22:15), two attack rounds, an honest-user round and a fresh review

The detector was frozen first (`benchmarks/FROZEN-v2.md`). The blind v2 result is in section 1 and the README.

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| Round 1 evasions against the new signals: 29 tests | Each was fixed or pinned as a documented limit | Real Yahoo domains only, registrable-domain keys, leetspeak and confusable folding for disposable words, more homoglyphs, a non-ASCII wildcard rule, `.com` typo rules, user-agent major-version fingerprints | `tests/attacks/test_attack_v2_evasion.py` (for example `::test_attacker_subdomain_named_yahoo_is_not_a_well_known_provider`, `::test_one_edit_tld_swap_of_a_provider_is_a_lookalike`, `::test_wide_spread_survives_a_user_agent_minor_version_rotation`) |
| A flood of clean-looking Gmail addresses from one IP, to farm the crowd rule | Gets partial credit instead of a block | Caps of 60 an hour and 200 a day per IP; worst case about 17 grants an hour. This is a trade, not a full fix | `test_attack_v2_evasion.py::test_crowd_rule_caps_credit_for_one_ip_flood_of_clean_looking_gmail` |
| Word-like catch-all domain spread over many networks | Allowed | Documented limit: `domain_burst` alone never costs credit | `test_attack_v2_evasion.py::test_documented_limit_word_like_catch_all_domain_spread_alone_is_allowed` |
| Honest round: 11 findings. Schools, universities, families, shared labs, VPN exits, carrier crowds, CJK keyboards, odd-looking real company domains | Honest groups were blocked or reviewed | Crowd rule no longer needs a device id and accepts national providers and normal own domains; a shared device on one network counts as crowd noise; domain signals are soft; pasted invisible characters weigh 0.3; `device_farm` needs 6+ accounts | `tests/attacks/test_honest_v2.py` (for example `::test_school_of_60_with_clean_identities_gets_partial_credit_never_a_block`, `::test_iphone_crowd_on_one_carrier_across_many_slash24s_is_allowed`) |
| Shared IP carry-over from phase 7: CGNAT, school | School of 60 in 20 minutes: 58 blocked | 6 blocked after the crowd rule | `tests/attacks/test_honest_v2.py` |
| Fresh review: 6 findings | Fixed | `yahoo.co` and `yahoo.tk` are no longer big providers; domain networks re-recorded every 10 minutes so reuse keeps counting; `owner_after_aliases` forces review only when built in | `tests/test_v022_review.py`, `tests/attacks/test_attack_v2_evasion.py` |

Honest cost, unchanged by the fixes: a school behind one IP is 97% reviewed (partial credit). Proxy rotation (34%), shared-VPN farmers (32%) and unlisted disposable domains (46%) were not improved. The full suite passed 3 times (2,409 tests at the end of the phase).

### Phase 10: labels and your accuracy (night 2, 22:14 to 23:17), two attack rounds and a review

| Attack | Result | Fix | Test that guards it |
|---|---|---|---|
| `decision_id` was a hash of the log line, so a guessed email could be confirmed against it (email oracle) | MEDIUM: worked | `DecisionLog` writes a random `decision_id`; older lines keep a derived id | `tests/test_shadow_report_attacks.py::test_template_decision_id_does_not_confirm_a_guessed_email` |
| A non-ASCII CSRF token on label or upload | A 500 error | Compared as bytes; answered 403 | `test_labels_attacks.py::test_a_non_ascii_csrf_token_is_a_403_not_a_500_on_label`, `::..._on_upload` |
| A log line with wrong-typed `reasons` or `signals` | `shadow-report` crashed | Such lines are skipped | `test_shadow_report_attacks.py::test_a_log_line_with_a_wrong_typed_field_is_skipped_not_a_traceback` |
| Spreadsheet formulas in template cells and export | Formula injection | Cells are formula-safe | `test_shadow_report_attacks.py::test_template_cells_from_the_log_are_formula_safe` |
| Many label uploads to starve the database pool that `/v1/check` shares | Pool starvation | One upload at a time per customer, and at most half the database pool at once per process (otherwise 429) | `test_labels_attacks.py::test_a_second_concurrent_label_upload_is_refused_not_queued` |
| Round 2: concurrent labels (real, not sure, farmer, outcome buttons) on one decision or one identity | MEDIUM: deadlocks, orphan or lost network reports | One lock order everywhere: the customer's advisory lock, then the decision row | `test_labels_attacks2.py::test_not_sure_and_real_clicked_together_do_not_deadlock`, `::test_two_real_labels_racing_do_not_leave_an_orphan_network_report`, `::test_real_racing_farmer_on_the_same_identity_does_not_lose_the_report`, `test_labels_fixes.py::test_every_outcome_path_takes_the_customer_lock_first` |
| Removing a label removed a dispute that another "real" decision still backed | Dispute lost | `_undispute` keeps a dispute while another real decision needs it | `test_labels_attacks2.py::test_not_sure_keeps_the_dispute_another_real_decision_still_needs`, `test_labels_fixes.py::test_clearing_one_of_two_real_labels_keeps_the_dispute_but_clearing_the_last_drops_it` |
| Two other customers uploading could lock a third out of upload slots | Slot lockout | Global slots sized to the pool (one connection kept free), six uploads per customer in ten minutes, rows applied in id order, partial failure reported per chunk | `test_labels_attacks2.py::test_two_other_customers_uploading_must_not_lock_this_customer_out`, `test_labels_fixes.py::test_one_customer_may_start_only_six_uploads_in_ten_minutes` |
| Export past the cap cut off the newest labels | Truncation | Newest first, with a truncation note outside the CSV; UTC times | `test_labels_attacks2.py::test_export_past_the_limit_keeps_the_newest_labels` |
| `1,5` read as a thousands separator; `-1` read as a farmer label | Misread input | A comma is accepted only as a thousands separator; `-1` is not a farmer label | `test_labels_attacks2.py::test_a_decimal_comma_is_not_read_as_a_thousands_comma`, `tests/test_shadow_report.py::test_minus_one_is_not_read_as_a_farmer_label` |
| Docs that did not match the code (shadow is per account, set in Settings) | Mismatch | `docs/first-week.md` corrected | `test_labels_fixes.py::test_first_week_guide_describes_shadow_mode_as_per_account_in_settings` |

Carry-overs, not fixed: the outcome retraction path counts test-mode farmer outcomes; the accuracy query has no statement timeout (fine to about 400,000 decisions); upload limits are per process, so with N instances the ceiling is N times higher. The full suite passed 3 times (2,532 tests).

## 3. Residual risks

These are not fixed, and some cannot be by Tenkyu.

1. **Patient farmers get through.** A new inbox, home IP and device for every account, spread over time, shares nothing Tenkyu can link. 0.5% were flagged in the held-out data. At default settings the first benchmark shows them keeping every grant. `Guard.for_ai_apps()` halves it, at the price of half a grant for every unverified honest user. Only a card or verified phone they must reuse closes it, and a farmer who pays for a new one each time is not stopped.
2. **The numbers are synthetic.** The night-1 held-out set (seeds 1-20) became a tuning set on night 2, so only the blind v2 result (seeds 101-120, run once after the rules were frozen) is an unseen-data figure. Both generators are separate AI agents' guesses at farmers, not real traffic. Farmers are about 25% of its accounts, which sets the precision. Treat every figure as a sanity check until real customers label real data (`tenkyu evaluate`).
3. **Throwaway domains not on the bundled list** mostly pass (46% flagged on the blind v2 set), and so does a **word-like catch-all domain** spread over many networks (`domain_burst` alone never costs credit). **Look-alike emails** are flagged now, but one the confusables subset does not know can still pass. Tenkyu does not verify email ownership; you should.
4. **Honest users pay.** Families on one device (60% flagged), people with a work and a personal account on one device (50%), and carrier NAT (11%) lose part of their credit. A school behind one IP gets partial credit for nearly everyone (97% on the blind v2 set). Partial credit, not a block, but real. The same crowd rule means a farmer with distinct clean-looking addresses behind one IP gets partial credit too (worst case about 17 grants an hour for one IP).
5. **The surge guard can be triggered by a farmer** (opt-in, and verified links are exempt) to hold credit back from honest new users for up to an hour.
6. **The AI red teams are not human attackers.** Eighteen tactics and the hosted drills came from model-written attacks. They found real bugs, but a clean run is not proof of safety.
7. **The hosted service has not been deployed, and the recipes have not run on a real Supabase or Vercel project.** The Supabase recipe's client-IP header is unverified (its README says how to check it). Nothing has been tested over the internet, behind Cloud Run's front end, or with real GitHub or Lemon Squeezy traffic. The Lemon Squeezy payload fields and signature scheme are from memory and must be checked against its docs and one real test-mode order (`hosted/BILLING.md`). The Docker image was never built here. Load tests (about 115 to 135 checks a second, p95 about 210 ms at 20 clients) ran on one 2-core sandbox with everything on one box.
8. **Shared network poisoning is raised, not removed.** Two or three colluding customers that meet the age and volume rules (7 days old, 50 checks) could still report an honest identity. The watcher flags reports that look like collusion; disputes and a 90-day expiry limit the damage. One honest decision can still be wrong.
9. **Fail open on database loss** means a database outage turns the hosted check into the customer's own local engine. That protects the signup page and weakens detection while it lasts.
10. **Secrets.** Losing `MASTER_SECRET` makes all stored history unreadable. `NETWORK_SECRET` is the other key. A leak of either, plus the database, lets someone test guessed addresses.
11. **The watcher cannot see everything** (5xx answers, bad signatures and auth failures live only in service logs; own-signup checks are heuristics). Its daily digest says so.
12. **Night-2 carry-overs.** Outcome retraction counts test-mode farmer outcomes. The accuracy query has no statement timeout. Label upload limits are per process. The recipes' rate limits are per instance. Shadow mode only helps if you label: with fewer than 50 labelled decisions the accuracy numbers carry a warning.
13. **One person maintains this.** Response times in `SECURITY.md` depend on that.

To report something not listed here, see [SECURITY.md](../SECURITY.md).
