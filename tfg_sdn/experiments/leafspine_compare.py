#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

from tfg_sdn.mininet.topos.leafspine import LeafSpineParams, LeafSpineTopo


# ============================================================
# Utilidades simples
# ============================================================

def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_compact() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: dict) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def run_cmd(cmd: List[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)


def append_csv(path: Path, row: dict, fieldnames: List[str]) -> None:
    ensure_dir(path.parent)
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def read_csv_rows(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ============================================================
# Parseadores
# ============================================================

def parse_ping_summary(ping_text: str) -> Dict[str, float | None]:
    out = {
        "rtt_avg_ms": None,
        "rtt_mdev_ms": None,
    }
    m = re.search(r"rtt .* = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", ping_text)
    if m:
        out["rtt_avg_ms"] = float(m.group(2))
        out["rtt_mdev_ms"] = float(m.group(4))
    return out


def safe_json_loads(text: str) -> dict:
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    i = text.find("{")
    j = text.rfind("}")
    if i >= 0 and j > i:
        return json.loads(text[i:j + 1])
    return {}


def parse_iperf_tcp_mbps_and_retx(text: str) -> Tuple[float | None, int | None]:
    try:
        data = safe_json_loads(text)
    except Exception:
        return None, None

    if isinstance(data, dict) and data.get("error"):
        return None, None

    end = data.get("end", {})
    sum_received = end.get("sum_received", {})
    sum_sent = end.get("sum_sent", {})

    mbps = None
    retx = None

    if isinstance(sum_received, dict) and "bits_per_second" in sum_received:
        mbps = float(sum_received["bits_per_second"]) / 1e6

    if isinstance(sum_sent, dict) and "retransmits" in sum_sent:
        retx = int(sum_sent["retransmits"])

    return mbps, retx


def parse_dump_ports_bytes(text: str) -> Dict[str, int]:
    out = {"tx_bytes": 0, "rx_bytes": 0}

    m_rx = re.search(r"rx pkts=\d+, bytes=(\d+)", text)
    m_tx = re.search(r"tx pkts=\d+, bytes=(\d+)", text)

    if m_rx:
        out["rx_bytes"] = int(m_rx.group(1))
    if m_tx:
        out["tx_bytes"] = int(m_tx.group(1))

    return out


def jain_fairness(values: List[float]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    s = sum(vals)
    s2 = sum(v * v for v in vals)
    n = len(vals)
    if s2 == 0:
        return None
    return (s * s) / (n * s2)


# ============================================================
# Escenario oficial Leaf-Spine
# ============================================================

def official_flows() -> List[dict]:
    """
    Seis flujos remotos concurrentes.
    La idea es sencilla:
    - h1 y h2 son los emisores
    - cada flujo va a un leaf remoto
    - así forzamos uso de los uplinks del leaf de entrada
    """
    return [
        {"name": "h1_h3", "src": "h1", "dst": "h3", "dst_ip": "10.0.0.3", "port": 5203},
        {"name": "h1_h5", "src": "h1", "dst": "h5", "dst_ip": "10.0.0.5", "port": 5205},
        {"name": "h1_h7", "src": "h1", "dst": "h7", "dst_ip": "10.0.0.7", "port": 5207},
        {"name": "h2_h4", "src": "h2", "dst": "h4", "dst_ip": "10.0.0.4", "port": 5204},
        {"name": "h2_h6", "src": "h2", "dst": "h6", "dst_ip": "10.0.0.6", "port": 5206},
        {"name": "h2_h8", "src": "h2", "dst": "h8", "dst_ip": "10.0.0.8", "port": 5208},
    ]


# ============================================================
# Captura de evidencias OVS
# ============================================================

def dump_ovs_state(raw_dir: Path, profile: str) -> None:
    switches = ["lf1", "sp1", "sp2"]

    for sw in switches:
        cp = run_cmd(["sudo", "ovs-ofctl", "-O", "OpenFlow13", "dump-flows", sw])
        write_text(raw_dir / f"dump_flows_{sw}.txt", cp.stdout + cp.stderr)

    if profile == "ecmp":
        cp = run_cmd(["sudo", "ovs-ofctl", "-O", "OpenFlow13", "dump-groups", "lf1"])
        write_text(raw_dir / "dump_groups_lf1.txt", cp.stdout + cp.stderr)

        cp = run_cmd(["sudo", "ovs-ofctl", "-O", "OpenFlow13", "dump-group-stats", "lf1"])
        write_text(raw_dir / "dump_group_stats_lf1.txt", cp.stdout + cp.stderr)


def get_lf1_uplink_stats() -> Dict[str, Dict[str, int]]:
    stats = {}

    for port in [1, 2]:
        cp = run_cmd(["sudo", "ovs-ofctl", "-O", "OpenFlow13", "dump-ports", "lf1", str(port)])
        stats[f"port{port}"] = parse_dump_ports_bytes(cp.stdout + cp.stderr)

    return stats


def save_lf1_uplink_stats(path: Path, stats: Dict[str, Dict[str, int]]) -> None:
    write_json(path, stats)


# ============================================================
# Despliegue y forwarding
# ============================================================

def build_leafspine_net(odl_host: str, odl_of_port: int, ls_bw_mbps: int) -> Mininet:
    params = LeafSpineParams(
        spines=2,
        leaves=4,
        hosts_per_leaf=2,
        leaf_spine_bw_mbps=ls_bw_mbps if ls_bw_mbps > 0 else None,
        leaf_spine_delay=None,
        host_leaf_bw_mbps=None,
        host_leaf_delay=None,
    )

    topo = LeafSpineTopo(params)

    net = Mininet(
        topo=topo,
        controller=None,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=False,
        build=True,
    )
    net.addController("c0", controller=RemoteController, ip=odl_host, port=int(odl_of_port))
    return net


def configure_net_basic(net: Mininet) -> None:
    net.start()

    for sw in net.switches:
        sw.cmd(f"ovs-vsctl set-fail-mode {sw.name} secure")

    for h in net.hosts:
        h.cmd("ip neigh flush all")

    net.staticArp()


def apply_profile(profile: str, root: Path, env: dict, raw_dir: Path) -> None:
    cmd = [
        str(root / "tfgctl"),
        "push-flows",
        "--topo", "leafspine",
        "--profile", profile,
        "--spines", "2",
        "--leaves", "4",
        "--hpl", "2",
    ]
    cp = run_cmd(cmd, cwd=root, env=env)
    write_text(raw_dir / "push_flows.txt", cp.stdout + cp.stderr)

    if cp.returncode != 0:
        raise RuntimeError(
            f"push-flows failed.\n"
            f"Revisa: {raw_dir / 'push_flows.txt'}"
        )


def validate_profile(
    profile: str,
    root: Path,
    env: dict,
    raw_dir: Path,
    tag: str,
    strict: bool,
) -> None:
    cmd = [
        str(root / "tfgctl"),
        "validate",
        "--topo", "leafspine",
        "--profile", profile,
        "--spines", "2",
        "--leaves", "4",
        "--hpl", "2",
    ]

    if strict:
        cmd.append("--strict")

    cp = run_cmd(cmd, cwd=root, env=env)
    write_text(raw_dir / f"validate_{tag}.txt", cp.stdout + cp.stderr)

    if cp.returncode != 0:
        raise RuntimeError(
            f"validate failed.\n"
            f"Revisa: {raw_dir / f'validate_{tag}.txt'}"
        )


# ============================================================
# Ejecución de tráfico
# ============================================================

def wait_port_listening(host, port: int, timeout_s: float = 3.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        out = host.cmd("ss -lnt")
        if f":{port} " in out or f":{port}\n" in out:
            return True
        time.sleep(0.1)
    return False


def start_iperf_servers(net: Mininet, flows: List[dict], raw_dir: Path) -> List[dict]:
    """
    Arranca un servidor por flujo.
    Usamos -1 para que cada servidor acepte una conexión y termine solo.
    """
    servers = []
    started = set()

    for flow in flows:
        dst = flow["dst"]
        port = flow["port"]

        if (dst, port) in started:
            continue

        host = net.get(dst)
        proc = host.popen(
            f"iperf3 -s -p {port} -1",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        ok = wait_port_listening(host, port, timeout_s=3.0)
        servers.append({
            "dst": dst,
            "port": port,
            "proc": proc,
            "listening_ok": ok,
            "raw_path": str(raw_dir / f"iperf_server_{dst}_{port}.txt"),
        })
        started.add((dst, port))

    failed = [s for s in servers if not s["listening_ok"]]
    if failed:
        details = ", ".join(f"{s['dst']}:{s['port']}" for s in failed)
        raise RuntimeError(f"Some iperf3 servers did not start correctly: {details}")

    return servers


def collect_iperf_server_outputs(servers: List[dict]) -> None:
    for s in servers:
        try:
            out, _ = s["proc"].communicate(timeout=5)
        except subprocess.TimeoutExpired:
            s["proc"].kill()
            out, _ = s["proc"].communicate()
        write_text(Path(s["raw_path"]), out)


def run_concurrent_flows(
    net: Mininet,
    flows: List[dict],
    dur_s: int,
    ping_count: int,
    raw_dir: Path,
) -> Tuple[List[dict], Dict[str, float | None]]:
    """
    Lanza los 6 flujos TCP de forma concurrente y, además, un ping bajo carga.
    """
    processes = []
    results = []

    ping_src = net.get("h1")
    ping_dst_ip = "10.0.0.7"
    ping_proc = ping_src.popen(
        f"ping -c {ping_count} {ping_dst_ip}",
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    for flow in flows:
        src = net.get(flow["src"])
        dst_ip = flow["dst_ip"]
        port = flow["port"]

        proc = src.popen(
            f"iperf3 -c {dst_ip} -p {port} -t {dur_s} -J",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append((flow, proc))

    for flow, proc in processes:
        out, _ = proc.communicate()
        out_path = raw_dir / f"iperf_{flow['name']}.json"
        write_text(out_path, out)

        mbps, retx = parse_iperf_tcp_mbps_and_retx(out)
        results.append({
            "flow_name": flow["name"],
            "src": flow["src"],
            "dst": flow["dst"],
            "dst_ip": flow["dst_ip"],
            "port": flow["port"],
            "throughput_mbps": mbps,
            "retransmits": retx,
            "json_path": str(out_path),
        })

    ping_out, _ = ping_proc.communicate()
    write_text(raw_dir / "ping.txt", ping_out)
    ping_metrics = parse_ping_summary(ping_out)

    return results, ping_metrics


# ============================================================
# Resumen por run
# ============================================================

def compute_run_metrics(
    flow_results: List[dict],
    ping_metrics: Dict[str, float | None],
    before_stats: Dict[str, Dict[str, int]],
    after_stats: Dict[str, Dict[str, int]],
    dur_s: int,
    ls_bw_mbps: int,
) -> Dict[str, float | None]:
    throughputs = [r["throughput_mbps"] for r in flow_results if r["throughput_mbps"] is not None]

    total_mbps = sum(throughputs) if throughputs else None
    mean_mbps = statistics.mean(throughputs) if throughputs else None
    fairness = jain_fairness(throughputs)

    successful_flows = len(throughputs)
    total_flows = len(flow_results)
    success_ratio_pct = (100.0 * successful_flows / total_flows) if total_flows > 0 else None

    tx1_before = before_stats["port1"]["tx_bytes"]
    tx2_before = before_stats["port2"]["tx_bytes"]
    tx1_after = after_stats["port1"]["tx_bytes"]
    tx2_after = after_stats["port2"]["tx_bytes"]

    delta1 = max(0, tx1_after - tx1_before)
    delta2 = max(0, tx2_after - tx2_before)

    util1 = None
    util2 = None
    if ls_bw_mbps > 0 and dur_s > 0:
        util1 = (delta1 * 8.0) / (dur_s * ls_bw_mbps * 1e6) * 100.0
        util2 = (delta2 * 8.0) / (dur_s * ls_bw_mbps * 1e6) * 100.0

    balance_ratio = None
    bigger = max(delta1, delta2)
    smaller = min(delta1, delta2)
    if bigger > 0:
        balance_ratio = smaller / bigger

    return {
        "successful_flows": successful_flows,
        "flow_success_ratio_pct": success_ratio_pct,
        "throughput_total_mbps": total_mbps,
        "throughput_mean_per_flow_mbps": mean_mbps,
        "fairness_jain": fairness,
        "rtt_avg_ms": ping_metrics.get("rtt_avg_ms"),
        "rtt_mdev_ms": ping_metrics.get("rtt_mdev_ms"),
        "lf1_uplink1_bytes": delta1,
        "lf1_uplink2_bytes": delta2,
        "lf1_uplink1_util_pct": util1,
        "lf1_uplink2_util_pct": util2,
        "lf1_uplink_balance_ratio": balance_ratio,
    }


# ============================================================
# Summary CSV
# ============================================================

def write_summary_csv(runs_csv: Path, summary_csv: Path) -> None:
    rows = read_csv_rows(runs_csv)
    if not rows:
        return

    groups = defaultdict(list)
    for row in rows:
        groups[row["profile"]].append(row)

    fieldnames = [
        "profile",
        "n",
        "successful_flows_mean",
        "flow_success_ratio_mean_pct",
        "throughput_total_mean_mbps",
        "throughput_total_stdev_mbps",
        "throughput_mean_per_flow_mean_mbps",
        "fairness_jain_mean",
        "rtt_avg_mean_ms",
        "lf1_uplink1_util_mean_pct",
        "lf1_uplink2_util_mean_pct",
        "lf1_uplink_balance_ratio_mean",
    ]

    ensure_dir(summary_csv.parent)
    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for profile, prof_rows in groups.items():
            def vals(key: str) -> List[float]:
                out = []
                for r in prof_rows:
                    v = r.get(key)
                    if v is None or v == "":
                        continue
                    out.append(float(v))
                return out

            success_n = vals("successful_flows")
            success_ratio = vals("flow_success_ratio_pct")
            tp_total = vals("throughput_total_mbps")
            tp_mean = vals("throughput_mean_per_flow_mbps")
            fairness = vals("fairness_jain")
            rtt = vals("rtt_avg_ms")
            u1 = vals("lf1_uplink1_util_pct")
            u2 = vals("lf1_uplink2_util_pct")
            balance = vals("lf1_uplink_balance_ratio")

            writer.writerow({
                "profile": profile,
                "n": len(prof_rows),
                "successful_flows_mean": statistics.mean(success_n) if success_n else None,
                "flow_success_ratio_mean_pct": statistics.mean(success_ratio) if success_ratio else None,
                "throughput_total_mean_mbps": statistics.mean(tp_total) if tp_total else None,
                "throughput_total_stdev_mbps": statistics.stdev(tp_total) if len(tp_total) > 1 else 0.0 if len(tp_total) == 1 else None,
                "throughput_mean_per_flow_mean_mbps": statistics.mean(tp_mean) if tp_mean else None,
                "fairness_jain_mean": statistics.mean(fairness) if fairness else None,
                "rtt_avg_mean_ms": statistics.mean(rtt) if rtt else None,
                "lf1_uplink1_util_mean_pct": statistics.mean(u1) if u1 else None,
                "lf1_uplink2_util_mean_pct": statistics.mean(u2) if u2 else None,
                "lf1_uplink_balance_ratio_mean": statistics.mean(balance) if balance else None,
            })


# ============================================================
# Ejecución de un run
# ============================================================

def run_one_experiment(
    root: Path,
    profile: str,
    rep: int,
    dur_s: int,
    ping_count: int,
    ls_bw_mbps: int,
    env: dict,
    runs_csv: Path,
) -> dict:
    run_id = f"{now_compact()}_leafspine_compare_{profile}_rep{rep}"
    raw_dir = root / "data" / "raw" / "leafspine" / "compare" / run_id
    ensure_dir(raw_dir)

    flows = official_flows()

    manifest = {
        "run_id": run_id,
        "ts": now_iso(),
        "topo": "leafspine",
        "profile": profile,
        "scenario": "Leaf-Spine concurrent remote TCP flows + ping under load",
        "params": {
            "spines": 2,
            "leaves": 4,
            "hosts_per_leaf": 2,
            "dur_s": dur_s,
            "ping_count": ping_count,
            "ls_bw_mbps": ls_bw_mbps,
            "tcp_flows": len(flows),
        },
        "flows": flows,
        "paths": {},
        "results": {},
    }

    net = None
    try:
        net = build_leafspine_net(
            odl_host=env.get("ODL_HOST", "127.0.0.1"),
            odl_of_port=int(env.get("ODL_OF_PORT", "6653")),
            ls_bw_mbps=ls_bw_mbps,
        )

        configure_net_basic(net)
        apply_profile(profile, root, env, raw_dir)

        warmup_loss = net.pingAll()
        manifest["results"]["warmup_pingall_loss_pct"] = warmup_loss

        validate_profile(profile, root, env, raw_dir, "after_pingall", strict=False)

        before_stats = get_lf1_uplink_stats()
        save_lf1_uplink_stats(raw_dir / "port_stats_lf1_before.json", before_stats)
        manifest["paths"]["port_stats_lf1_before"] = str(raw_dir / "port_stats_lf1_before.json")

        servers = start_iperf_servers(net, flows, raw_dir)

        flow_results, ping_metrics = run_concurrent_flows(
            net=net,
            flows=flows,
            dur_s=dur_s,
            ping_count=ping_count,
            raw_dir=raw_dir,
        )
        collect_iperf_server_outputs(servers)
        
        validate_profile(profile, root, env, raw_dir, "after_traffic", strict=True)

        after_stats = get_lf1_uplink_stats()
        save_lf1_uplink_stats(raw_dir / "port_stats_lf1_after.json", after_stats)
        manifest["paths"]["port_stats_lf1_after"] = str(raw_dir / "port_stats_lf1_after.json")

        dump_ovs_state(raw_dir, profile)

        metrics = compute_run_metrics(
            flow_results=flow_results,
            ping_metrics=ping_metrics,
            before_stats=before_stats,
            after_stats=after_stats,
            dur_s=dur_s,
            ls_bw_mbps=ls_bw_mbps,
        )

        manifest["results"]["flow_results"] = flow_results
        manifest["results"]["ping"] = ping_metrics
        manifest["results"]["summary"] = metrics
        manifest["paths"]["manifest"] = str(raw_dir / "manifest.json")

        write_json(raw_dir / "manifest.json", manifest)

        csv_fieldnames = [
            "run_id",
            "ts",
            "profile",
            "rep",
            "spines",
            "leaves",
            "hosts_per_leaf",
            "dur_s",
            "tcp_flows",
            "successful_flows",
            "flow_success_ratio_pct",
            "throughput_total_mbps",
            "throughput_mean_per_flow_mbps",
            "fairness_jain",
            "rtt_avg_ms",
            "rtt_mdev_ms",
            "lf1_uplink1_bytes",
            "lf1_uplink2_bytes",
            "lf1_uplink1_util_pct",
            "lf1_uplink2_util_pct",
            "lf1_uplink_balance_ratio",
        ]

        row = {
            "run_id": run_id,
            "ts": manifest["ts"],
            "profile": profile,
            "rep": rep,
            "spines": 2,
            "leaves": 4,
            "hosts_per_leaf": 2,
            "dur_s": dur_s,
            "tcp_flows": len(flows),
            "successful_flows": metrics["successful_flows"],
            "flow_success_ratio_pct": metrics["flow_success_ratio_pct"],
            "throughput_total_mbps": metrics["throughput_total_mbps"],
            "throughput_mean_per_flow_mbps": metrics["throughput_mean_per_flow_mbps"],
            "fairness_jain": metrics["fairness_jain"],
            "rtt_avg_ms": metrics["rtt_avg_ms"],
            "rtt_mdev_ms": metrics["rtt_mdev_ms"],
            "lf1_uplink1_bytes": metrics["lf1_uplink1_bytes"],
            "lf1_uplink2_bytes": metrics["lf1_uplink2_bytes"],
            "lf1_uplink1_util_pct": metrics["lf1_uplink1_util_pct"],
            "lf1_uplink2_util_pct": metrics["lf1_uplink2_util_pct"],
            "lf1_uplink_balance_ratio": metrics["lf1_uplink_balance_ratio"],
        }
        append_csv(runs_csv, row, csv_fieldnames)

        return manifest

    finally:
        if net is not None:
            try:
                net.stop()
            except Exception:
                pass

        run_cmd(["sudo", "mn", "-c"])


# ============================================================
# Main
# ============================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Leaf-Spine evidence package.\n"
            "Comparación principal: single_path vs ecmp.\n"
            "Comparación auxiliar: l3_static vs ecmp."
        )
    )
    p.add_argument(
        "--profiles",
        default="single_path,ecmp,l3_static",
        help="Comma-separated: single_path,l3_static,ecmp"
    )
    p.add_argument("--rep", type=int, default=1, help="Number of repetitions per profile")
    p.add_argument("--dur", type=int, default=15, help="iperf3 duration in seconds")
    p.add_argument("--ping-count", type=int, default=20, help="Ping packets during load")
    p.add_argument("--ls-bw-mbps", type=int, default=100, help="Leaf-Spine link shaping in Mbps")
    p.add_argument("--reset-csv", action="store_true", help="Delete previous runs/summary CSV before running")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    profiles = [x.strip() for x in args.profiles.split(",") if x.strip()]
    valid_profiles = {"single_path", "l3_static", "ecmp"}

    for profile in profiles:
        if profile not in valid_profiles:
            print(f"FAIL: invalid profile '{profile}'. Valid: single_path, l3_static, ecmp")
            return 2

    root = repo_root()
    os.chdir(root)

    runs_csv = root / "data" / "processed" / "leafspine" / "compare" / "leafspine_compare_runs.csv"
    summary_csv = root / "data" / "processed" / "leafspine" / "compare" / "leafspine_compare_summary.csv"

    if args.reset_csv:
        if runs_csv.exists():
            runs_csv.unlink()
        if summary_csv.exists():
            summary_csv.unlink()

    env = os.environ.copy()
    env.setdefault("ODL_HOST", "127.0.0.1")
    env.setdefault("ODL_USER", "admin")
    env.setdefault("ODL_PASS", "")
    env.setdefault("ODL_OF_PORT", "6653")
    env.setdefault("ODL_REST_PORT", "8181")

    setLogLevel("warning")

    all_manifests = []

    for profile in profiles:
        for rep in range(1, args.rep + 1):
            print(f"[leafspine_compare] Running profile={profile} rep={rep}/{args.rep}")
            manifest = run_one_experiment(
                root=root,
                profile=profile,
                rep=rep,
                dur_s=args.dur,
                ping_count=args.ping_count,
                ls_bw_mbps=args.ls_bw_mbps,
                env=env,
                runs_csv=runs_csv,
            )
            all_manifests.append(manifest)
            print(f"[leafspine_compare] OK profile={profile} rep={rep} run_id={manifest['run_id']}")

    write_summary_csv(runs_csv, summary_csv)

    print("\nDONE: Leaf-Spine compare finished.")
    print(f"Runs CSV: {runs_csv}")
    print(f"Summary CSV: {summary_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
