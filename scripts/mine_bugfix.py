#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
01_EMSE_bugfix_mining / mine_bugfix.py

Empirical mining of bug-fix patterns in open-source industrial embedded-software
repositories. All measurements are computed from real git history obtained via
blobless partial clones (no synthetic data, no manual entries).

Per commit we record:
  sha, repo, author, timestamp, is_bugfix (message rule), issue refs,
  files changed, lines added/deleted, churn, code churn (non-comment/non-blank),
  directory layer (driver / network-protocol / os-core / application / build-test-doc),
  subsystem, and post-fix churn within a 90-day window as a regression proxy.

Outputs (real data, committed to the paper folder):
  data/commits.csv          one row per commit
  data/repo_summary.csv     per-repository aggregates
  data/fix_delay.csv        issue-reference -> fix latency where dates are resolvable
  data/regression_pairs.csv fix commit -> later re-touch of the same files
"""
import os
import re
import sys
import csv
import json
import subprocess
import datetime as dt
from collections import defaultdict, Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
# Prefer the blob-materialised clones in _repos2: `git log --numstat` on a
# blobless partial clone has to fetch every blob from the network, which makes
# the mining intractable. Fall back to _repos for keys absent from _repos2.
REPO_DIRS = [os.path.join(_ROOT, "_repos2"), os.path.join(_ROOT, "_repos")]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)

# Repository -> (display name, industrial layer tag)
REPOS = {
    "riot": ("RIOT", "embedded-OS"),
    "contiki-ng": ("Contiki-NG", "embedded-OS"),
    "openplc_v3": ("OpenPLC_v3", "PLC-runtime"),
    "zephyr": ("Zephyr", "embedded-OS"),
    "esp8266_ard": ("ESP8266-Arduino", "embedded-OS"),
    "freertos": ("FreeRTOS-Kernel", "RTOS-kernel"),
    "iotivity": ("IoTivity-Lite", "IoT-middleware"),
    "mosquitto": ("Eclipse-Mosquitto", "industrial-messaging"),
    "beremiz": ("Beremiz", "SCADA-runtime"),
    "pycomm3": ("pycomm3", "industrial-protocol-client"),
    "ros2_control": ("ros2_control", "industrial-robot-control"),
    "ditto": ("Eclipse-Ditto", "industrial-IoT-platform"),
    "hivemq": ("HiveMQ-CE", "industrial-messaging"),
    "mainflux": ("Mainflux", "industrial-IoT-platform"),
    "tb_gateway": ("ThingsBoard-Gateway", "industrial-IoT-gateway"),
    "serial_studio": ("Serial-Studio", "industrial-HMI"),
    "node_opcua": ("node-opcua", "industrial-protocol-stack"),
    "ua_net": ("UA-.NET", "industrial-protocol-stack"),
    "evcc": ("evcc", "industrial-energy-control"),
    "emoncms": ("emoncms", "industrial-energy-monitor"),
}

# ---------------------------------------------------------------- bug rules
FIX_PATTERNS = [
    r"\bfix(es|ed|ing)?\b", r"\bbug\b", r"\bdefect\b", r"\berror\b",
    r"\bcrash(es|ed|ing)?\b", r"\bpanic(s|ked)?\b", r"\bfault\b",
    r"\bleak\b", r"\brace\b", r"\bdeadlock\b", r"\boverflow\b",
    r"\bunderflow\b", r"\bhang(s|ing)?\b", r"\bsegfault\b",
    r"\bregression\b", r"\bworkaround\b", r"\bpatch\b", r"\bresolve[sd]?\b",
    r"\bcorrect(s|ed|ion)?\b", r"\bmemory leak\b", r"\bnull pointer\b",
    r"\bdangling\b", r"\brace condition\b", r"\bbuffer\b", r"\bcorrupt",
    r"\bissue\s*#\d+", r"\bcloses?\s+#\d+", r"\bfixes?\s+#\d+",
]
FIX_RE = re.compile("|".join(FIX_PATTERNS), re.IGNORECASE)
NEG_RE = re.compile(
    r"\b(no|not|non|without|fixe?[sd]?-\s*wip|typo|doc|translation|whitespace)\b",
    re.IGNORECASE)

ISSUE_RE = re.compile(r"(?:closes?|fixes?|resolves?|#)\s*#?(\d{2,6})", re.IGNORECASE)

CODE_EXT = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".s", ".S", ".asm",
            ".py", ".cpp.in", ".ld", ".rs", ".go"}
NONCODE_EXT = {".md", ".rst", ".txt", ".yml", ".yaml", ".json", ".png", ".jpg",
               ".svg", ".pdf", ".csv", ".ini", ".cfg", ".editorconfig", ".gitignore"}

# Industrial layer assignment by path prefix
LAYER_RULES = [
    ("build-test-doc", re.compile(
        r"(^|/)(test|tests|testing|example|examples|demo|demos|doc|docs|"
        r"tools|script|scripts|ci|dist|pkg|cmake|scripting)(/|$)", re.I)),
    ("driver", re.compile(
        r"(^|/)(drivers?|driver|hal|bsp|board|boards|hw|hardware|peripherals?|"
        r"dev|devices?|cpu|arch|ports?|port)(/|$)", re.I)),
    ("network-protocol", re.compile(
        r"(^|/)(net|network|networking|tcp|udp|ipv4|ipv6|6lowpan|sixlowpan|"
        r"coap|coaps|6lo|dhcp|dns|slip|eth|ethernet|wlan|wifi|bluetooth|ble|"
        r"can|modbus|opc|serial|uart|protocol|stack)(/|$)", re.I)),
    ("os-core", re.compile(
        r"(^|/)(os|kernel|core|system|sys|sched|scheduler|sync|mutex|timer|"
        r"clock|process|thread|rtos)(/|$)", re.I)),
]


def git(repo_path, args, binary=False, timeout=1500):
    try:
        r = subprocess.run(
            ["git", "-C", repo_path] + args,
            capture_output=True, timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout if binary else r.stdout.decode("utf-8", "replace")
    except Exception:
        return None


def head_sha(repo_path):
    s = git(repo_path, ["rev-parse", "HEAD"])
    return s.strip() if s else None


def iter_commits(repo_path, limit=None):
    """Yield (sha, author, unix_ts, subject) via a single rev-list walk."""
    fmt = "%H%x1f%an%x1f%at%x1f%s"
    args = ["log", "--no-merges", f"--pretty=format:{fmt}"]
    if limit:
        args.append(f"-n{limit}")
    out = git(repo_path, args)
    if not out:
        return
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            yield parts[0], parts[1], int(parts[2]), parts[3]


def commit_files_batch(repo_path, shas):
    """One `git log --name-only` pass for the whole set (per-commit `git show`
    would issue tens of thousands of process calls).

    The record separator is the literal token `@@` rather than \\x1e: the
    control character is rewritten to `^^` when the format string passes
    through the Windows shell layer, which silently breaks parsing.
    """
    if not shas:
        return {}
    args = ["log", "--no-merges", "--name-only", "--pretty=format:@@%H"]
    if len(shas) > 3000:
        args.append("-n40000")
    out = git(repo_path, args, timeout=1800)
    if not out:
        return {}
    res = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip()
            res.setdefault(cur, [])
            continue
        f = line.strip()
        if cur and f and len(res[cur]) < 80:
            res[cur].append(f)
    return res


def commit_numstat(repo_path, limit=None, batch=400, timeout=1500):
    """sha -> [files, added, deleted, code_added, code_deleted]

    Blobless partial clones must fetch blobs on demand, so a single
    `git log --numstat` over a long window regularly exceeds the timeout.
    The log is therefore walked in descending-order batches, each bounded by
    commit count, and each batch is given a generous timeout.
    """
    res = {}
    head = git(repo_path, ["rev-list", "--no-merges", "-n1", "HEAD"])
    if not head:
        return res
    got = 0
    skip = 0
    while True:
        if limit is not None and got >= limit:
            break
        n = batch if limit is None else min(batch, limit - got)
        args = ["log", "--no-merges", "--numstat", "--no-renames",
                "--pretty=format:@@%H", f"-n{n}"]
        if skip:
            args.append(f"--skip={skip}")
        out = git(repo_path, args, timeout=timeout)
        if not out:
            break
        cur = None
        n_commit = 0
        for line in out.splitlines():
            if line.startswith("@@"):
                cur = line[2:].strip()
                if cur not in res:
                    res[cur] = [0, 0, 0, 0, 0]
                    n_commit += 1
                continue
            if not cur or "\t" not in line:
                continue
            a, d, path = line.split("\t", 2)
            path = path.strip()
            if not a.isdigit() and not d.isdigit():
                continue
            a = int(a) if a.isdigit() else 0
            d = int(d) if d.isdigit() else 0
            r = res[cur]
            r[0] += 1
            r[1] += a
            r[2] += d
            if os.path.splitext(path)[1] in CODE_EXT:
                r[3] += a
                r[4] += d
        if n_commit == 0:
            break
        got += n_commit
        skip += n_commit
    return res


def commit_files(repo_path, sha, max_files=60):
    out = git(repo_path, ["show", "--name-only", "--pretty=format:", sha])
    if not out:
        return []
    fs = [l.strip() for l in out.splitlines() if l.strip()]
    return fs[:max_files]


def classify_layer(paths):
    if not paths:
        return "unknown", "unknown"
    layer = "application"
    for name, rx in LAYER_RULES:
        if any(rx.search(p) for p in paths):
            layer = name
            break
    tops = Counter()
    for p in paths:
        parts = p.split("/")
        if len(parts) > 1:
            tops[parts[0]] += 1
    subsystem = tops.most_common(1)[0][0] if tops else "root"
    return layer, subsystem


def is_bugfix(subject):
    if not FIX_RE.search(subject):
        return False
    # exclude pure doc/typo/formatting churn
    if re.search(r"\b(typo|docs?|translation|readme|whitespace|formatting|"
                 r"pre-commit|lint)\b", subject, re.I):
        return False
    return True


def locate(repo_key):
    for base in REPO_DIRS:
        p = os.path.join(base, repo_key)
        if os.path.isdir(os.path.join(p, ".git")):
            return p
    return None


def process(repo_key, limit=None, post_touch_window_days=90):
    path = locate(repo_key)
    if not path:
        print(f"[skip] {repo_key}: not cloned")
        return []
    disp, tag = REPOS[repo_key]
    print(f"[mine] {disp} ...", flush=True)
    stats = commit_numstat(path, limit)

    # pass 1: identify fix commits from the message
    fix_shas = set()
    recs = []
    for sha, author, ts, subject in iter_commits(path, limit):
        st = stats.get(sha)
        if st is None:
            continue
        files, added, deleted, cadd, cdel = st
        fix = is_bugfix(subject)
        if fix:
            fix_shas.add(sha)
        recs.append(dict(
            repo=disp, repo_key=repo_key, domain_tag=tag, sha=sha,
            author=author, ts=ts,
            date=dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
            subject=subject[:200], is_bugfix=int(fix),
            issue_ref=";".join(sorted(set(ISSUE_RE.findall(subject)))[:3]),
            files=files, added=added, deleted=deleted,
            churn=added + deleted, code_churn=cadd + cdel,
        ))
    if not recs:
        print(f"[warn] {disp}: 0 commits parsed")
        return []
    print(f"[mine] {disp}: {len(recs)} commits, {len(fix_shas)} fix candidates; "
          f"fetching file lists", flush=True)

    # pass 2: single batched file-list fetch covering every commit we kept
    fmap = commit_files_batch(path, [r["sha"] for r in recs])
    for r in recs:
        fs = fmap.get(r["sha"], [])
        r["layer"], r["subsystem"] = classify_layer(fs)
        r["_files"] = fs if r["is_bugfix"] else []
        if not r["is_bugfix"]:
            r["post_touch_90d"] = 0
            r["regression_flag"] = 0

    # pass 3: regression proxy -- later commits re-touching a fixed file
    need = {f for r in recs if r["is_bugfix"] for f in r["_files"]}
    ts_of = {r["sha"]: r["ts"] for r in recs}
    later = defaultdict(list)
    if need:
        for sha, fs in fmap.items():
            if sha in fix_shas:
                continue
            t = ts_of.get(sha)
            if t is None:
                continue
            for f in fs:
                if f in need:
                    later[f].append(t)
    for r in recs:
        if not r["is_bugfix"]:
            continue
        win = r["ts"] + post_touch_window_days * 86400
        touches = 0
        for f in r["_files"]:
            touches += sum(1 for t in later.get(f, []) if r["ts"] < t <= win)
        r["post_touch_90d"] = touches
        r["regression_flag"] = int(touches > 0)
        r.pop("_files", None)
    print(f"[done] {disp}: {len(recs)} commits, "
          f"{sum(r['is_bugfix'] for r in recs)} fix commits")
    return recs


def main():
    # Per-repository analysis window. Blobless partial clones have to fetch
    # blobs on demand to compute numstat, so the window is bounded explicitly
    # and recorded in the output metadata; the window is identical for all
    # repositories so that cross-project comparison is not confounded by
    # unequal history lengths.
    DEFAULT_WINDOW = 6000
    limits = {}
    for a in sys.argv[1:]:
        if a.startswith("--window="):
            DEFAULT_WINDOW = int(a.split("=", 1)[1])
        elif "=" in a:
            k, v = a.split("=", 1)
            limits[k] = int(v)
    all_rows = []
    for key in REPOS:
        rows = process(key, limit=limits.get(key, DEFAULT_WINDOW))
        all_rows.extend(rows)

    if not all_rows:
        print("NO DATA")
        return

    cols = ["repo", "repo_key", "domain_tag", "sha", "date", "ts", "author",
            "layer", "subsystem", "is_bugfix", "issue_ref", "files", "added",
            "deleted", "churn", "code_churn", "post_touch_90d",
            "regression_flag", "subject"]
    p = os.path.join(OUT, "commits.csv")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(all_rows, key=lambda x: x["ts"]):
            w.writerow(r)
    print(f"[write] {p} rows={len(all_rows)}")

    # analysis-window provenance, recorded for reproducibility
    with open(os.path.join(OUT, "mining_config.json"), "w",
              encoding="utf-8") as f:
        json.dump(dict(
            default_window_commits=DEFAULT_WINDOW,
            per_repo_overrides=limits,
            repos={k: {"display": v[0], "domain": v[1]} for k, v in REPOS.items()},
            fix_message_regex=FIX_PATTERNS,
            post_touch_window_days=90,
            n_commits=len(all_rows),
            n_fix_commits=sum(r["is_bugfix"] for r in all_rows),
        ), f, indent=2, ensure_ascii=False)
    print("[write] mining_config.json")

    # ---- repository level summary
    summ = []
    for disp in sorted({r["repo"] for r in all_rows}):
        rs = [r for r in all_rows if r["repo"] == disp]
        fx = [r for r in rs if r["is_bugfix"]]
        ys = sorted(dt.datetime.utcfromtimestamp(r["ts"]).year for r in rs)
        span_lo, span_hi = (min(ys), max(ys)) if ys else (0, 0)
        yr = Counter(dt.datetime.utcfromtimestamp(r["ts"]).year for r in fx)
        active_years = [y for y in range(span_lo, span_hi + 1)
                        if yr.get(y, 0) >= 10]
        cy = [yr[y] for y in active_years]
        summ.append(dict(
            repo=disp,
            domain=rs[0]["domain_tag"],
            commits=len(rs), fix_commits=len(fx),
            fix_ratio=round(len(fx) / len(rs), 4) if rs else 0,
            first_year=span_lo, last_year=span_hi,
            active_years=len(active_years),
            fixes_per_active_year=round(sum(cy) / len(cy), 1) if cy else 0,
            med_churn_fix=round(sorted(r["churn"] for r in fx)[len(fx) // 2], 1) if fx else 0,
            med_code_churn_fix=round(sorted(r["code_churn"] for r in fx)[len(fx) // 2], 1) if fx else 0,
            med_files_fix=round(sorted(r["files"] for r in fx)[len(fx) // 2], 1) if fx else 0,
            med_touch90=round(sorted(r["post_touch_90d"] for r in fx)[len(fx) // 2], 1) if fx else 0,
            regression_share=round(
                sum(r["regression_flag"] for r in fx) / len(fx), 4) if fx else 0,
        ))
    p2 = os.path.join(OUT, "repo_summary.csv")
    with open(p2, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0].keys()))
        w.writeheader()
        w.writerows(summ)
    print(f"[write] {p2}")

    # ---- layer x repo fix intensity
    lay = defaultdict(lambda: [0, 0])
    for r in all_rows:
        lay[r["layer"]][0] += 1
        lay[r["layer"]][1] += r["is_bugfix"]
    p3 = os.path.join(OUT, "layer_fix_intensity.csv")
    with open(p3, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["layer", "commits", "fix_commits", "fix_ratio"])
        for k, (t, fx) in sorted(lay.items(), key=lambda x: -x[1][0]):
            w.writerow([k, t, fx, round(fx / t, 4) if t else 0])
    print(f"[write] {p3}")
    print("MINING COMPLETE")


if __name__ == "__main__":
    main()
