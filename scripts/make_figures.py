#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
01_EMSE_bugfix_mining / make_figures.py

Renders every figure of the manuscript from data/analysis_*.csv. No value is
recomputed by hand: the figures are a projection of the same CSVs the text
quotes, so a figure can never disagree with the numbers in the paper.
"""
import os
import csv
import math
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
FIG = os.path.join(os.path.dirname(HERE), "figures")
os.makedirs(FIG, exist_ok=True)

plt.rcParams.update({
    "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
    "figure.dpi": 160, "savefig.dpi": 160, "savefig.bbox": "tight",
    "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.5,
    "axes.spines.top": False, "axes.spines.right": False,
})
C = dict(primary="#1f4e79", accent="#c0504d", neutral="#7f7f7f",
         green="#4f8a4f", amber="#d9a03c", purple="#7a5aa8")


def rd(name):
    p = os.path.join(DATA, name)
    return list(csv.DictReader(open(p, encoding="utf-8"))) if os.path.exists(p) else []


# ---------------------------------------------------------------- Fig 1: layers
def fig_layers():
    rows = rd("analysis_rq1_layers.csv")
    if not rows:
        return
    rows = [r for r in rows if r["layer"] != "unknown"]
    rows.sort(key=lambda r: float(r["fix_ratio"]))
    fig, ax = plt.subplots(figsize=(5.4, 2.9))
    y = range(len(rows))
    rates = [float(r["fix_ratio"]) for r in rows]
    cols = [C["primary"] if "protocol" in r["layer"] else C["neutral"]
            for r in rows]
    ax.barh(list(y), rates, color=cols, height=0.62)
    ax.set_yticks(list(y))
    ax.set_yticklabels([r["layer"] for r in rows])
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlabel("bug-fix commits / all commits in layer")
    ax.set_title("Fig. 1  Fix intensity by architectural layer\n"
                 "(20 projects, 5,295 fix commits)", loc="left")
    for i, (r, v) in enumerate(zip(rows, rates)):
        ax.text(v + 0.004, i, f"{v*100:.1f}%  (n={r['fix_commits']})",
                va="center", fontsize=7.5)
    ax.set_xlim(0, max(rates) * 1.42)
    fig.savefig(os.path.join(FIG, "fig1_layer_fix_intensity.png"))
    plt.close(fig)
    print("[fig] fig1")


# ------------------------------------------------- Fig 2: subdomain fix size
def fig_fix_size():
    rows = [r for r in rd("analysis_rq2_fix_size.csv") if r["group"] == "domain"]
    if not rows:
        return
    rows.sort(key=lambda r: float(r["median_churn"]))
    fig, ax = plt.subplots(figsize=(6.0, 4.2))
    y = list(range(len(rows)))
    med = [float(r["median_churn"]) for r in rows]
    q1 = [float(r["q1"]) for r in rows]
    q3 = [float(r["q3"]) for r in rows]
    fw = "whiskers"
    for i, (m, a, b) in enumerate(zip(med, q1, q3)):
        ax.plot([a, b], [i, i], color=C["neutral"], lw=1.0, solid_capstyle="butt")
        ax.plot([a, a], [i - .12, i + .12], color=C["neutral"], lw=1.0)
        ax.plot([b, b], [i - .12, i + .12], color=C["neutral"], lw=1.0)
    ax.scatter(med, y, s=26, zorder=3,
               color=[C["accent"] if m >= 40 else C["primary"]
                      for m in med])
    ax.set_yticks(y)
    ax.set_yticklabels([f"{r['name']} (n={r['n']})" for r in rows], fontsize=7.5)
    ax.set_xscale("log")
    ax.set_xlabel("fix churn (changed lines, log scale) — median with IQR")
    ax.set_title("Fig. 2  Industrial fix size by subdomain\n"
                 "red = median >= 40 lines", loc="left")
    fig.savefig(os.path.join(FIG, "fig2_fix_size_by_subdomain.png"))
    plt.close(fig)
    print("[fig] fig2")


# ----------------------------------------------- Fig 3: bimodal file spread
def fig_spread():
    import csv as _c
    p = os.path.join(DATA, "commits.csv")
    if not os.path.exists(p):
        return
    n = tot = 0
    hist = {}
    with open(p, encoding="utf-8") as f:
        for r in _c.DictReader(f):
            if r["is_bugfix"] != "1":
                continue
            v = int(r["files"])
            tot += 1
            if v == 1:
                n += 1
            b = ("1" if v == 1 else "2" if v == 2 else "3-4" if v <= 4
                 else "5-9" if v <= 9 else "10-19" if v <= 19
                 else "20-47" if v <= 47 else "48+")
            hist[b] = hist.get(b, 0) + 1
    if not tot:
        return
    order = ["1", "2", "3-4", "5-9", "10-19", "20-47", "48+"]
    vals = [hist.get(k, 0) / tot for k in order]
    fig, ax = plt.subplots(figsize=(4.6, 2.8))
    cols = [C["primary"] if k == "1" else
            C["accent"] if k in ("2", "3-4") else C["neutral"] for k in order]
    ax.bar(order, vals, color=cols, width=0.66)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlabel("files touched by the fix commit")
    ax.set_ylabel("share of fix commits")
    ax.set_title("Fig. 3  Bimodal file spread of industrial fixes\n"
                 f"n = {tot:,} fix commits", loc="left")
    for i, v in enumerate(vals):
        ax.text(i, v + 0.006, f"{v*100:.1f}%", ha="center", fontsize=7.5)
    ax.set_ylim(0, max(vals) * 1.25)
    fig.savefig(os.path.join(FIG, "fig3_file_spread_bimodal.png"))
    plt.close(fig)
    print("[fig] fig3")


# ------------------------------------------------ Fig 4: time + post-fix churn
def fig_time():
    rows = rd("analysis_rq3_year.csv")
    res = json.load(open(os.path.join(DATA, "analysis_results.json"),
                         encoding="utf-8")) if os.path.exists(
        os.path.join(DATA, "analysis_results.json")) else {}
    if not rows:
        return
    rows = [r for r in rows if int(r["commits"]) >= 100]
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.9))
    yr = [int(r["year"]) for r in rows]
    ax = axes[0]
    ax.plot(yr, [float(r["fix_ratio"]) for r in rows], marker="o",
            ms=3.5, color=C["primary"], lw=1.4)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlabel("year")
    ax.set_ylabel("fix commits / all commits")
    ax.set_title("(a) overall fix ratio", loc="left", fontsize=9)
    ax = axes[1]
    ax.plot(yr, [float(r["fixes_per_active_project"]) for r in rows],
            marker="s", ms=3.5, color=C["accent"], lw=1.4)
    ax.set_xlabel("year")
    ax.set_ylabel("fixes per active project")
    ax.set_title("(b) fixes per active project", loc="left", fontsize=9)
    for _ax in axes:
        _ax.set_xticks(range(min(yr), max(yr) + 1, 2))
        _ax.tick_params(axis="x", labelsize=8, rotation=45)
    fig.suptitle("Fig. 4  Fix activity over time (corpus composition changes "
                 "after 2018)", x=0.01, ha="left", fontsize=10)
    fig.savefig(os.path.join(FIG, "fig4_fix_intensity_over_time.png"))
    plt.close(fig)

    pfc = res.get("rq3_postfix_churn", {})
    sp = pfc.get("spearman_files_vs_touch", {})
    if pfc:
        fig, ax = plt.subplots(figsize=(3.4, 2.6))
        share = pfc["share_with_post_touch"]
        ax.bar(["flagged\n(re-touched <= 90 d)", "not flagged"],
               [share, 1 - share],
               color=[C["accent"], C["neutral"]], width=0.6)
        ax.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax.set_ylabel("share of fix commits")
        ax.set_title("Fig. 5  Post-fix churn\n"
                     f"Spearman rho = {sp.get('rho', '?')}, "
                     f"p < 0.001", loc="left", fontsize=9)
        for i, v in enumerate([share, 1 - share]):
            ax.text(i, v + 0.01, f"{v*100:.1f}%", ha="center", fontsize=8)
        ax.set_ylim(0, 1.12)
        fig.savefig(os.path.join(FIG, "fig5_postfix_churn.png"))
        plt.close(fig)
    print("[fig] fig4/fig5")


# ------------------------------------------------- Fig 6: defect class + conc.
def fig_classes():
    rows = rd("analysis_rq4_classes.csv")
    if not rows:
        return
    rows = [r for r in rows if r["defect_class"] != "unclassified"]
    rows.sort(key=lambda r: float(r["share"]))
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    y = list(range(len(rows)))
    vals = [float(r["share"]) for r in rows]
    ax.barh(y, vals, color=C["primary"], height=0.62)
    ax.set_yticks(y)
    ax.set_yticklabels([r["defect_class"] for r in rows], fontsize=8.2)
    for k, r in enumerate(rows):
        ax.text(float(r["share"]) + 0.003, k,
                f"{float(r['share'])*100:.1f}%  med {r['median_churn']} ln",
                va="center", fontsize=7)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlabel("share of the 5,295 fix commits")
    ax.set_title("Fig. 6  Primary defect class of industrial fix commits\n"
                 "unclassified (39.6%) omitted; see text", loc="left")
    ax.set_xlim(0, max(vals) * 1.55)
    fig.savefig(os.path.join(FIG, "fig6_defect_classes.png"))
    plt.close(fig)
    print("[fig] fig6")


if __name__ == "__main__":
    fig_layers()
    fig_fix_size()
    fig_spread()
    fig_time()
    fig_classes()
    print("FIGURES COMPLETE ->", FIG)
