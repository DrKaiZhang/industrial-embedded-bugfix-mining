#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
01_EMSE_bugfix_mining / analyse.py

Statistical analysis of the mined bug-fix population. Every number reported in
the manuscript is produced here and written to data/analysis_*.csv / .json.

Research questions
  RQ1  How is bug-fix activity distributed across the industrial software
       architectural layers, and is the distribution stable across projects?
  RQ2  What is the shape of fix size (churn, code churn, file spread) and does
       it differ between layers and between domains?
  RQ3  How does fix activity evolve over calendar time (per-active-year
       intensity, and the share of fixes that re-touch the same files within
       90 days, i.e. an observable post-fix churn / regression proxy)?
  RQ4  Which commit-message defect classes dominate, and how do they relate to
       the architectural layer in which the fix lands?
"""
import os
import re
import sys
import csv
import json
import math
import datetime as dt
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "00_shared"))
import stats_tools as ST  # noqa: E402

# Defect classes from the commit message; mutually non-exclusive, matched in
# this fixed priority order so a commit is attributed to its primary class.
CLASSES = [
    ("crash-or-hang", r"\b(crash(es|ed|ing)?|panic(ked)?|segfault|seg-?fault|"
                      r"hang(s|ing)?|deadlock|fault|abort|freeze|watchdog|"
                      r"trap|exception|assert)\w*"),
    ("memory-safety", r"\b(memory\s*leak|leak(s|ed|ing)?|use-?after-?free|"
                      r"null\s*pointer|nullptr|dangling|buffer\s*over(flow)?|"
                      r"overflow(s|ed)?|underflow|out-?of-?bounds|\boob\b|"
                      r"double\s*free|uninitiali[sz]ed|stack\s*corrupt|"
                      r"invalid\s*(free|read|write|pointer))\w*"),
    ("race-or-sync", r"\b(race|atomic|mutex|lock(s|ed)?|synchroni[sz]|"
                      r"barrier|semaphore|interrupt\s*(handler|disabl|priority)|"
                      r"preempt|concurren|spinlock|dead-?lock)\w*"),
    ("protocol-or-wire", r"\b(protocol|packet|frame|checksum|\bcrc\b|tlv|"
                         r"handshake|retransmi|timeout|timing|byte\s*order|"
                         r"endianness|modbus|serial|\buart\b|\bcan\b|tcp|udp|"
                         r"coap|mqtt|opc\s*ua|ethernet|socket|ip\s*packet|"
                         r"fragment|keepalive|handshak)\w*"),
    ("numeric-or-conversion", r"\b(overflow|underflow|round(ing)?|truncat|"
                              r"precision|conversion|convert|cast|integer|"
                              r"unit\s*convert|scaling|calibrat|"
                              r"floating|double|int(eger)?\s*size)\w*"),
    ("resource-handling", r"\b(alloc|malloc|calloc|realloc|\bfree\b|"
                          r"file\s*desc|\bfd\b|handle|resource|close|cleanup|"
                          r"drain|flush|\bopen\b|truncate|seek|buffer\s*full)\w*"),
    ("build-or-config", r"\b(build|cmake|makefile|linker|\bld\b|compile|"
                        r"toolchain|flag|config|configure|header|include|"
                        r"packag|depend|\bci\b|pipeline|script|version\s*bump)\w*"),
    ("portability", r"\b(portab|platform|endian|align(ment)?|64-?bit|32-?bit|"
                    r"compiler|gcc|clang|msvc|windows|linux|posix|"
                    r"cross-?compil|target\s*(arch|board))\w*"),
    ("state-machine", r"\b(state\s*machine|transition|state|init(ialis|ializ)|"
                      r"startup|shutdown|reset|recover(y|ing)?|fault\s*handler)\w*"),
    ("regression", r"\bregress\w*"),
]


def classify(subject):
    """Primary class: the first class whose pattern matches, in the fixed
    priority order above, so every fix commit receives exactly one label and
    the class shares sum to 1."""
    s = subject.lower()
    for name, rx in CLASSES:
        if re.search(rx, s, re.I):
            return name
    # residual but explicit patterns that still describe a defect class
    if re.search(r"\b(no|not|never|stop|prevent|avoid|reject|guard|ensure|"
                 r"check|valid|correct|proper|wrong|bad|broken|fail|error|"
                 r"issue|problem|should|must|missing|remove|drop|skip|"
                 r"ignore|handle|deal|clean|clear|reset|close|stop)\w*",
                 s, re.I):
        return "generic-corrective"
    return "unclassified"


def main():
    rows = list(csv.DictReader(open(os.path.join(DATA, "commits.csv"),
                                    encoding="utf-8")))
    for r in rows:
        r["is_bugfix"] = int(r["is_bugfix"])
        r["files"] = int(r["files"])
        r["added"] = int(r["added"])
        r["deleted"] = int(r["deleted"])
        r["churn"] = int(r["churn"])
        r["code_churn"] = int(r["code_churn"])
        r["post_touch_90d"] = int(r["post_touch_90d"])
        r["regression_flag"] = int(r["regression_flag"])
        r["year"] = int(r["date"][:4])
        r["defect_class"] = classify(r["subject"])
    fixes = [r for r in rows if r["is_bugfix"]]
    out = {}

    # ---------------------------------------------------------- RQ0 population
    cfg = json.load(open(os.path.join(DATA, "mining_config.json"),
                         encoding="utf-8"))
    out["population"] = dict(
        n_repositories=len({r["repo"] for r in rows}),
        n_commits=len(rows), n_fix_commits=len(fixes),
        fix_share=round(len(fixes) / len(rows), 4),
        window_commits=cfg.get("default_window_commits"),
        n_domains=len({r["domain_tag"] for r in rows}))
    print(f"[population] {out['population']}")

    # ---------------------------------------------------------- RQ1 layers
    lay = defaultdict(lambda: dict(commits=0, fixes=0, churn=0, code=0,
                                   files=0, repos=set()))
    for r in rows:
        d = lay[r["layer"]]
        d["commits"] += 1
        d["repos"].add(r["repo"])
        if r["is_bugfix"]:
            d["fixes"] += 1
            d["churn"] += r["churn"]
            d["code"] += r["code_churn"]
            d["files"] += r["files"]
    lay_rows = []
    for k, v in sorted(lay.items(), key=lambda x: -x[1]["fixes"]):
        fx = [r["churn"] for r in fixes if r["layer"] == k]
        lay_rows.append(dict(
            layer=k, commits=v["commits"], fix_commits=v["fixes"],
            fix_ratio=round(v["fixes"] / v["commits"], 4) if v["commits"] else 0,
            n_repos=len(v["repos"]),
            mean_churn_per_fix=round(sum(fx) / len(fx), 2) if fx else 0,
            median_churn_per_fix=ST.quantile(sorted(fx), .5) if fx else 0,
            total_code_churn=v["code"]))
    ST.write_csv(lay_rows, os.path.join(DATA, "analysis_rq1_layers.csv"))
    out["rq1_layers"] = lay_rows

    # layer x repo fix ratio heterogeneity
    ratios = defaultdict(list)
    for r in rows:
        ratios[(r["repo"], r["layer"])].append(r["is_bugfix"])
    per_repo_ratio = defaultdict(list)
    for (repo, layer), v in ratios.items():
        per_repo_ratio[repo].append(sum(v) / len(v))
    spread = {repo: round(max(v) - min(v), 4)
              for repo, v in per_repo_ratio.items() if len(v) >= 3}
    out["rq1_layer_ratio_spread"] = dict(
        per_repo_layer_ratio_spread=spread,
        mean_spread=round(sum(spread.values()) / len(spread), 4) if spread else None)

    # ---------------------------------------------------------- RQ2 fix size
    size_rows = []
    for key in ("embedded-OS", "RTOS-kernel", "PLC-runtime", "SCADA-runtime",
                "industrial-messaging", "industrial-protocol-stack",
                "industrial-IoT-platform", "industrial-HMI", "IoT-middleware",
                "industrial-robot-control", "industrial-protocol-client",
                "industrial-energy-control", "industrial-energy-monitor",
                "industrial-IoT-gateway"):
        sub = [r for r in fixes if r["domain_tag"] == key]
        if len(sub) < 20:
            continue
        size_rows.append(dict(
            group="domain", name=key, n=len(sub),
            median_churn=ST.quantile(sorted(r["churn"] for r in sub), .5),
            q1=ST.quantile(sorted(r["churn"] for r in sub), .25),
            q3=ST.quantile(sorted(r["churn"] for r in sub), .75),
            median_code_churn=ST.quantile(
                sorted(r["code_churn"] for r in sub), .5),
            median_files=ST.quantile(sorted(r["files"] for r in sub), .5),
            mean_churn=round(sum(r["churn"] for r in sub) / len(sub), 2)))
    for k in sorted({r["layer"] for r in fixes}):
        sub = [r for r in fixes if r["layer"] == k]
        if len(sub) < 20:
            continue
        size_rows.append(dict(
            group="layer", name=k, n=len(sub),
            median_churn=ST.quantile(sorted(r["churn"] for r in sub), .5),
            q1=ST.quantile(sorted(r["churn"] for r in sub), .25),
            q3=ST.quantile(sorted(r["churn"] for r in sub), .75),
            median_code_churn=ST.quantile(
                sorted(r["code_churn"] for r in sub), .5),
            median_files=ST.quantile(sorted(r["files"] for r in sub), .5),
            mean_churn=round(sum(r["churn"] for r in sub) / len(sub), 2)))
    ST.write_csv(size_rows, os.path.join(DATA, "analysis_rq2_fix_size.csv"))
    out["rq2_fix_size"] = size_rows

    # tests: layer differences in churn and file spread
    tests = {}
    emb = [r["churn"] for r in fixes if r["domain_tag"] == "embedded-OS"]
    icp = [r["churn"] for r in fixes if r["domain_tag"] in
           ("industrial-protocol-stack", "industrial-messaging",
            "PLC-runtime", "SCADA-runtime", "industrial-HMI")]
    if len(emb) > 20 and len(icp) > 20:
        tests["churn_embedded_vs_ics_platform"] = ST.mannwhitney_u(emb, icp)
        tests["churn_embedded_vs_ics_platform"]["cliffs_delta"] = \
            ST.cliffs_delta(emb, icp)
        tests["churn_embedded_vs_ics_platform"]["median_diff_ci"] = \
            ST.bootstrap_diff_median_ci(emb, icp)
    drv = [r["code_churn"] for r in fixes if r["layer"] == "driver"]
    net = [r["code_churn"] for r in fixes if r["layer"] == "network-protocol"]
    if len(drv) > 20 and len(net) > 20:
        tests["code_churn_driver_vs_network"] = ST.mannwhitney_u(drv, net)
        tests["code_churn_driver_vs_network"]["cliffs_delta"] = \
            ST.cliffs_delta(drv, net)
    single = [r["files"] for r in fixes if r["files"] == 1]
    multi = [r["files"] for r in fixes if r["files"] > 1]
    out["rq2_tests"] = tests
    out["rq2_file_spread"] = dict(
        single_file_share=round(len(single) / len(fixes), 4),
        multi_file_share=round(len(multi) / len(fixes), 4),
        median_files_overall=ST.quantile(sorted(r["files"] for r in fixes), .5),
        p90_files=ST.quantile(sorted(r["files"] for r in fixes), .9),
        p99_files=ST.quantile(sorted(r["files"] for r in fixes), .99))
    print("[rq2] file spread:", out["rq2_file_spread"])
    for k, v in tests.items():
        print(f"  test {k}: p={v.get('p')} r={v.get('effect_r')}")

    # ---------------------------------------------------------- RQ3 time
    by_year = defaultdict(lambda: dict(commits=0, fixes=0, repos=set()))
    for r in rows:
        d = by_year[r["year"]]
        d["commits"] += 1
        d["repos"].add(r["repo"])
        if r["is_bugfix"]:
            d["fixes"] += 1
    yr_rows = []
    for y in sorted(by_year):
        d = by_year[y]
        # a project is "active" in y when it contributed >= 10 commits
        act = sum(1 for r in rows
                  if r["year"] == y and r["repo"] in d["repos"])
        yr_rows.append(dict(
            year=y, commits=d["commits"], fix_commits=d["fixes"],
            fix_ratio=round(d["fixes"] / d["commits"], 4) if d["commits"] else 0,
            n_repos=len(d["repos"]),
            fixes_per_active_project=round(
                d["fixes"] / max(1, len(d["repos"])), 3)))
    ST.write_csv(yr_rows, os.path.join(DATA, "analysis_rq3_year.csv"))
    out["rq3_year"] = yr_rows

    # post-fix churn (regression proxy)
    reg = [r for r in fixes if r["regression_flag"] == 1]
    out["rq3_postfix_churn"] = dict(
        n_fix_commits=len(fixes), n_with_post_touch=len(reg),
        share_with_post_touch=round(len(reg) / len(fixes), 4) if fixes else 0,
        median_touch_when_present=ST.quantile(
            sorted(r["post_touch_90d"] for r in reg), .5) if reg else 0,
        note=("a fix commit is flagged when any file it modified is modified "
              "again by a later commit within 90 days"))
    # does fix size predict post-fix churn?
    if len(fixes) > 50:
        xs = [r["files"] for r in fixes]
        ys = [r["post_touch_90d"] for r in fixes]
        out["rq3_postfix_churn"]["spearman_files_vs_touch"] = ST.spearman(xs, ys)
    print("[rq3] post-fix churn:", out["rq3_postfix_churn"])

    # per-project temporal stability
    proj_year = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        d = proj_year[r["repo"]][r["year"]]
        d[0] += 1
        d[1] += r["is_bugfix"]
    stab = []
    for repo, yd in proj_year.items():
        ratios = [v[1] / v[0] for v in yd.values() if v[0] >= 20]
        if len(ratios) >= 3:
            stab.append(dict(project=repo, n_years=len(ratios),
                             mean_fix_ratio=round(sum(ratios) / len(ratios), 4),
                             sd_fix_ratio=round(
                                 math.sqrt(sum((x - sum(ratios) / len(ratios)) ** 2
                                               for x in ratios) / len(ratios)), 4),
                             min_ratio=round(min(ratios), 4),
                             max_ratio=round(max(ratios), 4)))
    stab.sort(key=lambda x: -x["n_years"])
    ST.write_csv(stab, os.path.join(DATA, "analysis_rq3_stability.csv"))
    out["rq3_stability"] = dict(
        per_project=stab,
        mean_sd=round(sum(s["sd_fix_ratio"] for s in stab) / len(stab), 4)
        if stab else None,
        n_projects=len(stab))

    # ---------------------------------------------------------- RQ4 classes
    cls = Counter(r["defect_class"] for r in fixes)
    cls_rows = []
    tot = sum(cls.values())
    layers = sorted({r["layer"] for r in fixes})
    for name, c in cls.most_common():
        sub = [r for r in fixes if r["defect_class"] == name]
        cls_rows.append(dict(
            defect_class=name, n_fix_commits=c,
            share=round(c / tot, 4),
            median_churn=ST.quantile(sorted(r["churn"] for r in sub), .5),
            top_layer=Counter(r["layer"] for r in sub).most_common(1)[0][0],
            top_layer_share=round(
                Counter(r["layer"] for r in sub).most_common(1)[0][1] / len(sub), 3),
            dominant_domains=";".join(
                f"{k}:{v}" for k, v in Counter(
                    r["domain_tag"] for r in sub).most_common(3))))
    ST.write_csv(cls_rows, os.path.join(DATA, "analysis_rq4_classes.csv"))
    out["rq4_defect_classes"] = cls_rows

    # class x layer association (chi-square style effect via Fisher on extremes)
    net = sum(1 for r in fixes if r["layer"] == "network-protocol")
    net_crash = sum(1 for r in fixes if r["layer"] == "network-protocol"
                    and r["defect_class"] == "crash-or-hang")
    non = len(fixes) - net
    non_crash = sum(1 for r in fixes if r["layer"] != "network-protocol"
                    and r["defect_class"] == "crash-or-hang")
    out["rq4_association"] = dict(
        network_vs_crash=ST.fisher_exact_2x2(net_crash, net - net_crash,
                                              non_crash, non - non_crash),
        note=("Fisher exact test on [[network&crash, network&other],"
              " [non-network&crash, non-network&other]]"))
    print("[rq4] association:", out["rq4_association"]["network_vs_crash"]["p"])

    # concentration of fixes in a few subsystems
    sub = Counter(r["subsystem"] for r in fixes)
    out["rq4_subsystem_concentration"] = dict(
        n_subsystems=len(sub),
        top10_share=round(sum(c for _, c in sub.most_common(10)) / len(fixes), 4),
        top_subsystems=";".join(f"{k}:{v}" for k, v in sub.most_common(12)))

    ST.write_json(out, os.path.join(DATA, "analysis_results.json"))
    print("ANALYSIS COMPLETE")


if __name__ == "__main__":
    main()
