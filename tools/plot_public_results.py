#!/usr/bin/env python3
"""Generate public-facing result plots from the compact CSV evidence."""
from __future__ import annotations
import csv
from pathlib import Path
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figures"
OUT.mkdir(parents=True, exist_ok=True)

def rows(path: Path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))

def leafspine():
    data = rows(ROOT / "data/results/leafspine/leafspine_main_summary.csv")
    profiles=[r["profile"] for r in data]
    throughput=[float(r["throughput_total_mean_mbps"]) for r in data]
    fig,ax=plt.subplots(figsize=(7.2,4.4))
    ax.bar(profiles,throughput)
    ax.set_ylabel("Aggregate throughput (Mbit/s)")
    ax.set_title("Leaf-Spine: aggregate throughput")
    ax.grid(axis="y",alpha=.25)
    fig.tight_layout(); fig.savefig(OUT/"leafspine_throughput.png",dpi=180); plt.close(fig)

    u1=[float(r["lf1_uplink1_util_mean_pct"]) for r in data]
    u2=[float(r["lf1_uplink2_util_mean_pct"]) for r in data]
    x=range(len(profiles)); w=.36
    fig,ax=plt.subplots(figsize=(7.2,4.4))
    ax.bar([i-w/2 for i in x],u1,width=w,label="uplink 1")
    ax.bar([i+w/2 for i in x],u2,width=w,label="uplink 2")
    ax.set_xticks(list(x),profiles); ax.set_ylabel("Utilization (%)")
    ax.set_title("Leaf-Spine: lf1 uplink utilization")
    ax.legend(); ax.grid(axis="y",alpha=.25)
    fig.tight_layout(); fig.savefig(OUT/"leafspine_uplink_utilization.png",dpi=180); plt.close(fig)

def ecmp_limited():
    data=rows(ROOT/"data/results/ecmp_limited/ch08_ecmp_limited_branch_usage.csv")
    data=[r for r in data if r["mode"]=="select_group"]
    labels=[r["direction"] for r in data]
    a=[float(r["branch_A_share_pct"]) for r in data]
    b=[float(r["branch_B_share_pct"]) for r in data]
    x=range(len(labels)); w=.36
    fig,ax=plt.subplots(figsize=(7.2,4.4))
    ax.bar([i-w/2 for i in x],a,width=w,label="branch A")
    ax.bar([i+w/2 for i in x],b,width=w,label="branch B")
    ax.set_xticks(list(x),labels); ax.set_ylabel("Traffic share (%)")
    ax.set_title("Limited ECMP: select-group branch usage")
    ax.legend(); ax.grid(axis="y",alpha=.25)
    fig.tight_layout(); fig.savefig(OUT/"ecmp_limited_branch_usage.png",dpi=180); plt.close(fig)

def main():
    leafspine(); ecmp_limited(); print(f"Wrote figures to {OUT}")
if __name__ == "__main__":
    main()
