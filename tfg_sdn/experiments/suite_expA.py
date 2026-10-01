from __future__ import annotations

import csv
import math
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from tfg_sdn.experiments.expA import ExpAConfig, run_expA
from tfg_sdn.experiments.common import ensure_dir, repo_root, write_json


def _ts_compact() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _parse_int_list(csv_list: str) -> List[int]:
    items = []
    for part in csv_list.split(","):
        part = part.strip()
        if not part:
            continue
        items.append(int(part))
    return items


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if p <= 0:
        return sorted_vals[0]
    if p >= 100:
        return sorted_vals[-1]
    k = (p / 100.0) * (len(sorted_vals) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


def _mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else float("nan")


def _stdev(vals: List[float]) -> float:
    if len(vals) < 2:
        return float("nan")
    m = _mean(vals)
    var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
    return math.sqrt(var)


def _to_float(x: Any) -> float:
    if x is None:
        return float("nan")
    if x == "":
        return float("nan")
    try:
        return float(x)
    except Exception:
        return float("nan")


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def compute_w2_summary(
    w2_results_csv: Path,
    out_summary_csv: Path,
    only_run_ids: List[str] | None = None,
) -> Path:
    """
    Aggregates w2_results.csv into w2_summary.csv.
    Groups:
      topo, proto, link_bw_mbps, udp_rate_key
    Notes:
      - For proto=tcp, udp_rate_key is forced to 0 (so TCP is not duplicated per udp_rate).
      - Computes count, mean, stdev, median, p95 for key metrics.
    """
    rows = _read_csv_rows(w2_results_csv)
    if only_run_ids:
        wanted = set(only_run_ids)
        rows = [r for r in rows if r.get("run_id") in wanted]

    # Normalize grouping key
    groups: Dict[Tuple[str, str, str, str], List[Dict[str, str]]] = {}
    for r in rows:
        topo = r.get("topo", "")
        proto = r.get("proto", "")
        bw = r.get("link_bw_mbps", "")
        udp_rate = r.get("udp_rate_mbps", "")
        udp_key = "0" if proto == "tcp" else (udp_rate or "0")
        key = (topo, proto, bw, udp_key)
        groups.setdefault(key, []).append(r)

    summary_rows: List[Dict[str, Any]] = []
    for (topo, proto, bw, udp_key), items in sorted(groups.items()):
        # Collect metrics lists
        rtt = [_to_float(x.get("rtt_avg_ms")) for x in items]
        thr = [_to_float(x.get("throughput_mbps")) for x in items]
        jit = [_to_float(x.get("jitter_ms")) for x in items]
        loss = [_to_float(x.get("lost_percent")) for x in items]
        util = [_to_float(x.get("utilization_pct")) for x in items]
        retx = [_to_float(x.get("retransmits")) for x in items]

        def finite(vals: List[float]) -> List[float]:
            return [v for v in vals if not math.isnan(v)]

        rtt_f = sorted(finite(rtt))
        thr_f = sorted(finite(thr))
        jit_f = sorted(finite(jit))
        loss_f = sorted(finite(loss))
        util_f = sorted(finite(util))
        retx_f = sorted(finite(retx))

        summary_rows.append({
            "topo": topo,
            "proto": proto,
            "link_bw_mbps": bw,
            "udp_rate_key": udp_key,
            "n": len(items),

            "rtt_avg_mean_ms": _mean(rtt_f) if rtt_f else "",
            "rtt_avg_stdev_ms": _stdev(rtt_f) if rtt_f else "",
            "rtt_avg_median_ms": _percentile(rtt_f, 50) if rtt_f else "",
            "rtt_avg_p95_ms": _percentile(rtt_f, 95) if rtt_f else "",

            "throughput_mean_mbps": _mean(thr_f) if thr_f else "",
            "throughput_stdev_mbps": _stdev(thr_f) if thr_f else "",
            "throughput_median_mbps": _percentile(thr_f, 50) if thr_f else "",
            "throughput_p95_mbps": _percentile(thr_f, 95) if thr_f else "",

            "util_mean_pct": _mean(util_f) if util_f else "",
            "util_median_pct": _percentile(util_f, 50) if util_f else "",

            "jitter_mean_ms": _mean(jit_f) if jit_f else "",
            "jitter_p95_ms": _percentile(jit_f, 95) if jit_f else "",

            "loss_mean_pct": _mean(loss_f) if loss_f else "",
            "loss_p95_pct": _percentile(loss_f, 95) if loss_f else "",

            "retrans_mean": _mean(retx_f) if retx_f else "",
        })

    fieldnames = [
        "topo","proto","link_bw_mbps","udp_rate_key","n",
        "rtt_avg_mean_ms","rtt_avg_stdev_ms","rtt_avg_median_ms","rtt_avg_p95_ms",
        "throughput_mean_mbps","throughput_stdev_mbps","throughput_median_mbps","throughput_p95_mbps",
        "util_mean_pct","util_median_pct",
        "jitter_mean_ms","jitter_p95_ms",
        "loss_mean_pct","loss_p95_pct",
        "retrans_mean",
    ]
    _write_csv(out_summary_csv, summary_rows, fieldnames)
    return out_summary_csv


@dataclass(frozen=True)
class SuiteExpAConfig:
    topo: str                         # basic | intermediate
    reps: int = 5
    proto: str = "both"               # tcp|udp|both
    dur_s: int = 10
    ping_count: int = 20
    link_bw_mbps: int = 1000
    udp_rates_mbps: List[int] = None  # list of ints
    reset_csv: bool = False

    odl_host: str = "127.0.0.1"
    odl_of_port: int = 6653
    odl_rest_port: int = 8181
    odl_user: str = "admin"
    odl_pass: str = ""


def run_suite_expA(cfg: SuiteExpAConfig) -> Dict[str, Any]:
    """
    Runs a matrix of expA experiments:
      for rate in udp_rates:
        repeat reps times:
          run expA with given topo/proto/bw/dur/ping-count and udp_rate
    Writes:
      - suite manifest in runs/<suite_id>/suite_manifest.json
      - w2_summary.csv in data/processed/<topo>/expA/w2_summary.csv (filtered to this suite runs)
    """
    root = repo_root()
    suite_id = f"{_ts_compact()}_{cfg.topo}_suite_expA_bw{cfg.link_bw_mbps}_rep{cfg.reps}"
    suite_dir = root / "runs" / suite_id
    ensure_dir(suite_dir)

    # Optionally reset w2_results.csv (archive)
    proc_dir = root / "data" / "processed" / cfg.topo / "expA"
    ensure_dir(proc_dir)
    w2_csv = proc_dir / "w2_results.csv"
    if cfg.reset_csv and w2_csv.exists():
        backup = proc_dir / f"w2_results_{_ts_compact()}.csv"
        shutil.copy2(w2_csv, backup)

    udp_rates = cfg.udp_rates_mbps or [200]

    run_ids: List[str] = []
    manifests: List[str] = []
    failures: List[Dict[str, Any]] = []

    for rate in udp_rates:
        for i in range(1, cfg.reps + 1):
            exp_cfg = ExpAConfig(
                topo=cfg.topo,
                odl_host=cfg.odl_host,
                odl_of_port=cfg.odl_of_port,
                odl_rest_port=cfg.odl_rest_port,
                odl_user=cfg.odl_user,
                odl_pass=cfg.odl_pass,
                dur_s=cfg.dur_s,
                ping_count=cfg.ping_count,
                proto=cfg.proto,
                udp_rate_mbps=int(rate),
                link_bw_mbps=int(cfg.link_bw_mbps),
            )
            try:
                manifest_path = run_expA(exp_cfg)
                rid = manifest_path.parent.name  # runs/<run_id>/manifest.json
                run_ids.append(rid)
                manifests.append(str(manifest_path))
                print(f"[suite expA] OK rate={rate} rep={i}/{cfg.reps} run_id={rid}")
            except Exception as e:
                failures.append({"rate": rate, "rep": i, "error": str(e)})
                print(f"[suite expA] FAIL rate={rate} rep={i}/{cfg.reps}: {e}")

    # Summary filtered to suite run_ids
    summary_csv = proc_dir / "w2_summary.csv"
    compute_w2_summary(w2_csv, summary_csv, only_run_ids=run_ids)

    suite_manifest = {
        "suite_id": suite_id,
        "ts": _ts_compact(),
        "topo": cfg.topo,
        "exp": "expA",
        "params": {
            "reps": cfg.reps,
            "proto": cfg.proto,
            "dur_s": cfg.dur_s,
            "ping_count": cfg.ping_count,
            "link_bw_mbps": cfg.link_bw_mbps,
            "udp_rates_mbps": udp_rates,
            "reset_csv": cfg.reset_csv,
        },
        "run_ids": run_ids,
        "manifests": manifests,
        "failures": failures,
        "outputs": {
            "w2_results_csv": str(w2_csv),
            "w2_summary_csv": str(summary_csv),
        }
    }
    write_json(suite_dir / "suite_manifest.json", suite_manifest)
    return suite_manifest
