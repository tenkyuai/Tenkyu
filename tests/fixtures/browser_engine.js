/* Tenkyu demo engine v3 (browser copy).
   v2 + indexed lookups, a starter budget for new unverified accounts, a new-account surge guard, and shadow mode. */
(function () {
  var BUDGET = 3, HOUR = 3600, DAY = 86400;
  var STARTER = 0.5;        // new, unverified accounts get half the free budget in their first day
  var SURGE_N = 8;          // this many new unverified accounts starting to spend within an hour switches the surge guard on
  var FRACTION = { allow: 1, challenge: 0.25, block: 0 };
  var DISP = ('mailinator.com 10minutemail.com guerrillamail.com guerrillamail.net sharklasers.com grr.la yopmail.com yopmail.fr ' +
    'temp-mail.org tempmail.com tempmail.net trashmail.com trashmail.de getnada.com maildrop.cc dispostable.com mailnesia.com ' +
    'mintemail.com throwawaymail.com fakeinbox.com emailondeck.com mohmal.com tempail.com moakt.com spamgourmet.com ' +
    'mytemp.email tempinbox.com burnermail.io inboxkitten.com mailcatch.com 33mail.com').split(' ');
  var DISP_SET = {}; DISP.forEach(function (d) { DISP_SET[d] = 1; });
  var DC = ['45.32.', '185.220.10'];
  var VERIFY_LINKS = { phone: 1, card: 1 };
  var ZW = /[​-‍⁠﻿]/g;

  var accounts = [], byUser = {}, members = {}, keys = {}, netTimes = {}, usage = [];
  var spendCluster = {}, spendUser = {}, firstSpends = [], seq = 0;
  var cfg = { shadow: false };
  var t = Date.now() / 1000 - 6 * HOUR;

  function clean(e) { return e.replace(ZW, '').trim().toLowerCase(); }
  function norm(e) {
    var p = clean(e).split('@'), u = p[0].split('+')[0], d = p[1];
    if (d === 'gmail.com' || d === 'googlemail.com') { u = u.replace(/\./g, ''); d = 'gmail.com'; }
    else if (/^yahoo\.|^ymail\.com$/.test(d)) { u = u.split('-')[0]; }
    return u + '@' + d;
  }
  function isDisp(d) { var p = d.split('.'); for (var i = 0; i < p.length - 1; i++) if (DISP_SET[p.slice(i).join('.')]) return true; return false; }
  function net(ip) { return ip.split('.').slice(0, 3).join('.'); }
  function size(c) { return (members[c] || []).length; }
  function newId() { return Math.random().toString(16).slice(2, 14); }
  function validEmail(e) { if (typeof e !== 'string' || e.length > 254) return false; var c = clean(e); return c.split('@').length === 2 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(c); }
  function hashish(s) { var h = 2166136261; for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(16); }
  function netCount(n, since) {
    var a = netTimes[n]; if (!a) return 0;
    while (a.length && a[0] < t - DAY) a.shift();
    var c = 0; for (var i = a.length - 1; i >= 0 && a[i] >= since; i--) c++; return c;
  }
  function add(a) {
    accounts.push(a); byUser[a.user_id] = a;
    (members[a.cluster_id] = members[a.cluster_id] || []).push(a);
    if (a.ip) { var n = net(a.ip); (netTimes[n] = netTimes[n] || []).push(a.created); }
  }
  function merge(into, from) {
    if (into === from || !members[from]) return;
    members[from].forEach(function (a) { a.cluster_id = into; members[into].push(a); });
    delete members[from];
    Object.keys(keys).forEach(function (k) { if (keys[k] === from) keys[k] = into; });
    spendCluster[into] = (spendCluster[into] || 0) + (spendCluster[from] || 0); delete spendCluster[from];
  }

  function check(uid, email, ip, dev, links) {
    ip = typeof ip === 'string' ? ip : ''; dev = typeof dev === 'string' ? dev : '';
    t += 40;
    if (byUser[uid]) return byUser[uid];
    // A missing email (null, '' or whitespace: phone-auth users) is not invalid; only a supplied, malformed one blocks.
    var miss = email == null || (typeof email === 'string' && !email.trim());
    if (miss) email = '';
    if (!miss && !validEmail(email)) {
      var bad = { user_id: uid, email: String(email).slice(0, 80), ip: ip, cluster_id: newId(), score: 99, action: 'block', reasons: ['email address is not valid'], trusted: false, verified: false, created: t };
      members[bad.cluster_id] = []; add(bad); return bad;
    }
    var local = miss ? '' : clean(email).split('@')[0], domain = miss ? '' : clean(email).split('@')[1];
    // An address is the owner's when it carries no alias tag; dots do not count (people write maya.cohen@gmail.com).
    var isPlain = function (e) { var p = clean(e).split('@'); return p[0].indexOf('+') === -1 && !(/^yahoo\.|^ymail\.com$|^rocketmail\.com$/.test(p[1]) && p[0].indexOf('-') !== -1); };
    var canon = miss ? '' : norm(email), plain = miss ? false : isPlain(email);
    var ks = (miss ? [] : ['e:' + canon]).concat(dev ? ['d:' + dev] : []), verified = false;
    if (links && typeof links === 'object' && !Array.isArray(links)) Object.keys(links).slice(0, 8).forEach(function (k) {
      if (links[k] == null || links[k] === '' || typeof links[k] === 'object') return;
      ks.push('x:' + k + ':' + hashish(String(links[k]))); if (VERIFY_LINKS[k]) verified = true;
    });
    var why = [], s = 5, squat = false;

    var eOwner = miss ? undefined : keys['e:' + canon];
    if (eOwner && plain && members[eOwner]) {
      var same = members[eOwner].filter(function (a) { return norm(a.email) === canon; });
      if (same.length && same.every(function (a) { return !isPlain(a.email); })) {
        squat = true;
        same.forEach(function (a) { if (a.trusted) return; a.score = Math.max(a.score, 75); a.action = 'block'; if (a.reasons.indexOf('used an alias of an address its owner registered later') < 0) a.reasons.push('used an alias of an address its owner registered later'); });
      }
    }
    var found = [];
    ks.forEach(function (k) { if (squat && k === 'e:' + canon) return; if (keys[k] && found.indexOf(keys[k]) === -1) found.push(keys[k]); });
    var c = found[0] || newId(); members[c] = members[c] || [];
    found.slice(1).forEach(function (o) { merge(c, o); });
    var strongLink = ks.some(function (k) { return k.indexOf('d:') !== 0 && keys[k] && found.indexOf(keys[k]) !== -1; });

    var n = size(c) + 1, dom = miss ? '' : canon.split('@')[1], nt = ip ? net(ip) : '';
    var burst = nt ? netCount(nt, t - 600) + 1 : 1, today = nt ? netCount(nt, t - DAY) + 1 : 1;
    if (isDisp(dom)) { s += 45; why.push('disposable inbox'); }
    if (DC.some(function (p) { return ip.indexOf(p) === 0; })) { s += 25; why.push('datacenter IP'); }
    if (local.indexOf('+') !== -1 || (dom === 'gmail.com' && local.split('.').length > 2)) { s += 10; why.push('inbox alias trick'); }
    // Same rule as the library (0.2.2): volume from one network counts at 5 in 10 min (or network_day) only next to a real
    // risk signal. A shared device is not one, and a burst of its own needs 15.
    var other = why.length > 0 || strongLink;
    if (burst >= (other ? 5 : 15)) { s += 40; why.push('signup burst (' + burst + ' from one network in 10 min)'); }
    else if (other && today >= 10) { s += 35; why.push(today + ' signups from one network today'); }
    else if (other && today >= 4) { s += 15; why.push(today + ' signups from one network today'); }
    if (n >= 5) { s += 45; why.push('linked to ' + (n - 1) + ' accounts'); }
    else if (n >= 3) { s += 30; why.push('linked to ' + (n - 1) + ' accounts'); }
    else if (n === 2) { s += 15; why.push('linked to 1 account'); }
    // Same rule as the Python library: reusing an inbox (or an alias of it) or a card/phone link is enough to block.
    if (strongLink && !squat) { s = Math.max(s, 90); why.unshift(ks.some(function (k) { return k.indexOf('x:') === 0 && keys[k] && found.indexOf(keys[k]) !== -1; }) && !(keys['e:' + canon] && found.indexOf(keys['e:' + canon]) !== -1) ? 'shares a card or phone with another account' : 'this email (or an alias of it) already signed up'); }
    s = Math.min(s, 99);
    var action = s >= 70 ? 'block' : s >= 40 ? 'challenge' : 'allow';
    if (action === 'block' && !strongLink && !isDisp(dom) && !squat) action = 'challenge';
    if (squat) { why.push('owner of an inbox someone else aliased: partial credit'); if (action === 'allow') action = 'challenge'; }
    if (verified) why.push('verified phone or card');
    var a = { user_id: uid, email: miss ? '' : clean(email), ip: ip, cluster_id: c, score: s, action: action, reasons: why, trusted: false, verified: verified, created: t };
    add(a);
    ks.forEach(function (k) { keys[k] = c; });
    return a;
  }

  function surgeActive() { while (firstSpends.length && firstSpends[0] < t - HOUR) firstSpends.shift(); return firstSpends.length >= SURGE_N; }
  function capFor(a) {
    var cap = BUDGET * FRACTION[a.action], limit = '';
    if (!a.verified && t - a.created < DAY) {
      if (BUDGET * STARTER < cap) { cap = BUDGET * STARTER; limit = 'starter budget'; }
      if (!spendUser[a.user_id] && surgeActive()) { cap = 0; limit = 'surge guard: verify to unlock'; }
    }
    return { cap: cap, limit: limit };
  }
  function guard(uid, est) {
    var a = byUser[uid]; if (!a || a.trusted) return true;
    var lim = capFor(a);
    var ok = (spendCluster[a.cluster_id] || 0) + est <= BUDGET + 1e-9 && (spendUser[uid] || 0) + est <= lim.cap + 1e-9;
    if (!ok) { usage.push({ k: cfg.shadow ? 'would' : 'refused', usd: est, u: uid, c: a.cluster_id, why: lim.limit, at: t }); if (lim.limit) a.limit = lim.limit; }
    return ok || cfg.shadow;
  }
  function record(uid, usd) {
    var a = byUser[uid]; if (!a) return;
    if (!spendUser[uid] && !a.verified && !a.trusted && t - a.created < DAY) firstSpends.push(t);
    spendUser[uid] = (spendUser[uid] || 0) + usd; spendCluster[a.cluster_id] = (spendCluster[a.cluster_id] || 0) + usd;
    usage.push({ k: 'spend', usd: usd, u: uid, c: a.cluster_id, at: t });
  }
  function release(uid) { /* holds live in the real engine; the demo records spend directly */ }
  function allow(uid) { var a = byUser[uid]; if (a) { a.trusted = true; a.action = 'allow'; a.limit = ''; } return !!a; }
  function verify(uid) { var a = byUser[uid]; if (a) { a.verified = true; a.limit = ''; if (a.reasons.indexOf('verified phone or card') < 0) a.reasons.push('verified phone or card'); } return !!a; }
  function setShadow(on) { cfg.shadow = !!on; }

  function stats() {
    var first = {}; Object.keys(members).forEach(function (c) { if (members[c][0]) first[c] = members[c][0].user_id; });
    var counts = function (a) { return a && !a.trusted && (a.action === 'block' || first[a.cluster_id] !== a.user_id); };
    var per = {}, wouldPer = {}, refused = 0, held = 0;
    usage.forEach(function (x) {
      if (x.k === 'refused') { refused++; if (x.why) held += x.usd; }
      if ((x.k === 'refused' || x.k === 'would') && counts(byUser[x.u])) { var m = x.k === 'refused' ? per : wouldPer; m[x.u] = m[x.u] || { usd: 0, n: 0 }; m[x.u].usd += x.usd; m[x.u].n++; }
    });
    var tot = function (m) { var u = 0, n = 0; Object.keys(m).forEach(function (k) { u += Math.min(m[k].usd, BUDGET); n += m[k].n; }); return [u, n]; };
    var sv = tot(per), wv = tot(wouldPer), spend = 0, withheld = 0, flagged = 0;
    Object.keys(spendUser).forEach(function (k) { spend += spendUser[k]; });
    accounts.forEach(function (a) { if (!a.trusted && a.action !== 'allow') flagged++; if (counts(a)) withheld += Math.max(0, BUDGET - (spendUser[a.user_id] || 0)); });
    return { accounts: accounts.length, clusters: Object.keys(members).filter(function (c) { return members[c].length; }).length, flagged: flagged,
      spend_usd: spend, saved_usd: sv[0], saved_calls: sv[1], refused_calls: refused, would_save_usd: wv[0], would_save_calls: wv[1],
      new_account_limits_usd: held, free_credits_protected_usd: cfg.shadow ? 0 : withheld, free_budget_usd: BUDGET,
      shadow: cfg.shadow, surge_active: surgeActive(), starter_fraction: STARTER };
  }
  function signups(limit) { return accounts.slice(-(limit || 80)).reverse().map(function (a) { return Object.assign({}, a, { cluster_size: size(a.cluster_id) }); }); }
  var api = { check: check, guard: guard, record: record, release: release, allow: allow, verify: verify, setShadow: setShadow, acct: function (u) { return byUser[u]; },
    stats: stats, signups: signups, accounts: accounts, usage: usage, norm: norm, clock: function (v) { if (v !== undefined) t = v; return t; } };
  if (typeof window !== 'undefined') window.__t = api;
})();
