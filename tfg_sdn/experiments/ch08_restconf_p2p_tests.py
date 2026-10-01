#!/usr/bin/env python3
"""
FT8.3R — Ensayos punto a punto RESTCONF para Fat-Tree k=4.

Ruta esperada:
    tfg_sdn/experiments/ch08_restconf_p2p_tests.py

Función:
    1) Preparar un archivo de comandos para ejecutar dentro de la CLI de Mininet.
       Ese archivo:
         - refuerza ARP estático para T1/T2/T3;
         - ejecuta pings de control;
         - lanza servidores iperf3 en destino;
         - ejecuta clientes iperf3 desde origen;
         - captura dumps de flows/contadores tras las pruebas.

    2) Resumir posteriormente los artefactos generados:
         - ping: pérdida y RTT;
         - iperf3 JSON: throughput, retransmisiones si existen, duración;
         - evidencia de flows con contadores no nulos.

Uso típico, con Mininet y ODL ya activos, y tras FT8.R/FT8.2R PASS:

    cd $TFG_ROOT

    python3 tfg_sdn/experiments/ch08_restconf_p2p_tests.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action prepare

    # Dentro de mininet>:
    source $TFG_ROOT/runs/ch08_fattree/ch08_fattree_restconf_20260519_0957/ft8_3r_p2p_restconf/mininet_ft8_3r_commands.cli

    # De nuevo en terminal normal:
    python3 tfg_sdn/experiments/ch08_restconf_p2p_tests.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action summarize

Notas metodológicas:
    - Este script no instala forwarding. Asume que FT8.R ya instaló flows por ODL RESTCONF.
    - No implementa ECMP ni balanceo dinámico.
    - Evalúa los escenarios punto a punto T1, T2 y T3 sobre caminos deterministas.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class HostSpec:
    name: str
    ip: str
    mac: str
    iface: str


@dataclass(frozen=True)
class P2PScenario:
    scenario_id: str
    label: str
    src: str
    dst: str
    port: int
    expected_path: str
    description: str


HOSTS: Dict[str, HostSpec] = {
    f"h{i}": HostSpec(
        name=f"h{i}",
        ip=f"10.0.0.{i}",
        mac=f"00:00:00:00:00:{i:02x}",
        iface=f"h{i}-eth1",
    )
    for i in range(1, 17)
}

SCENARIOS: List[P2PScenario] = [
    P2PScenario(
        scenario_id="t1_local",
        label="T1 local",
        src="h1",
        dst="h2",
        port=5301,
        expected_path="h1-e1-h2",
        description="Comunicación local dentro del mismo edge switch.",
    ),
    P2PScenario(
        scenario_id="t2_intrapod",
        label="T2 intra-pod",
        src="h1",
        dst="h3",
        port=5302,
        expected_path="h1-e1-a1-e2-h3",
        description="Comunicación intra-pod atravesando edge-aggregation-edge.",
    ),
    P2PScenario(
        scenario_id="t3_interpod",
        label="T3 inter-pod",
        src="h1",
        dst="h16",
        port=5303,
        expected_path="h1-e1-a1-c1-a7-e8-h16",
        description="Comunicación inter-pod atravesando núcleo Fat-Tree.",
    ),
]

# Hosts cuya tabla ARP queda documentada para FT8.3R.
ARP_DUMP_HOSTS: List[str] = sorted({s.src for s in SCENARIOS} | {s.dst for s in SCENARIOS})

# Flows representativos que deben tener contadores tras T1/T2/T3.
FLOW_EVIDENCE: Dict[str, List[str]] = {
    "e1": ["00:00:00:00:00:01", "00:00:00:00:00:02", "00:00:00:00:00:03", "00:00:00:00:00:10"],
    "a1": ["00:00:00:00:00:01", "00:00:00:00:00:03", "00:00:00:00:00:10"],
    "e2": ["00:00:00:00:00:01", "00:00:00:00:00:03"],
    "c1": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "a7": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "e8": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
}

PING_STATS_RE = re.compile(
    r"(?P<tx>\d+)\s+packets transmitted,\s+"
    r"(?P<rx>\d+)\s+received,\s+"
    r"(?:(?:\+\d+\s+errors,\s+)?)"
    r"(?P<loss>[0-9.]+)%\s+packet loss",
    re.IGNORECASE,
)

RTT_RE = re.compile(
    r"rtt\s+min/avg/max/(?:mdev|stddev)\s+=\s+"
    r"(?P<min>[0-9.]+)/(?P<avg>[0-9.]+)/(?P<max>[0-9.]+)/(?P<mdev>[0-9.]+)\s+ms",
    re.IGNORECASE,
)

PACKET_COUNTER_RE = re.compile(r"n_packets=(?P<packets>\d+)")
BYTE_COUNTER_RE = re.compile(r"n_bytes=(?P<bytes>\d+)")


def default_tfg_root() -> Path:
    return Path(os.environ.get("TFG_ROOT", str(Path(__file__).resolve().parents[2]))).expanduser()


def find_latest_campaign(tfg_root: Path) -> str:
    base = tfg_root / "runs" / "ch08_fattree"
    if not base.exists():
        raise FileNotFoundError(f"No existe el directorio de campañas: {base}")
    candidates = sorted(
        [p.name for p in base.iterdir() if p.is_dir() and p.name.startswith("ch08_fattree_restconf_")],
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No se han encontrado campañas RESTCONF en {base}")
    return candidates[0]


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False), encoding="utf-8")


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def host_peer_arp_command(src: str, dst: str) -> str:
    src_host = HOSTS[src]
    dst_host = HOSTS[dst]
    return (
        f"{src_host.name} ip neigh replace {dst_host.ip} "
        f"lladdr {dst_host.mac} nud permanent dev {src_host.iface}"
    )


def mininet_safe_path(path: Path) -> str:
    return str(path)


def scenario_dir(base: Path, scenario: P2PScenario) -> Path:
    return base / scenario.scenario_id


def build_mininet_commands(artifact_dir: Path, ping_count: int, repetitions: int, iperf_seconds: int) -> str:
    lines: List[str] = []
    raw_dir = artifact_dir / "raw"
    flow_dir = artifact_dir / "flow_counter_checks"
    arp_dir = artifact_dir / "arp_checks"

    # Nota: no se incluyen comentarios con # porque Mininet CLI los interpreta como comandos desconocidos.
    lines.append(f"sh mkdir -p {mininet_safe_path(raw_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(flow_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(arp_dir)}")

    for scenario in SCENARIOS:
        lines.append(f"sh mkdir -p {mininet_safe_path(scenario_dir(raw_dir, scenario))}")

    # Limpieza de procesos iperf3 previos.
    for host in sorted({s.src for s in SCENARIOS} | {s.dst for s in SCENARIOS}):
        lines.append(f"{host} pkill -f iperf3 || true")

    # Refuerzo de ARP estático bidireccional.
    for scenario in SCENARIOS:
        lines.append(f"{scenario.src} ip neigh flush all")
        lines.append(f"{scenario.dst} ip neigh flush all")
        lines.append(host_peer_arp_command(scenario.src, scenario.dst))
        lines.append(host_peer_arp_command(scenario.dst, scenario.src))

    # Evidencia ARP.
    for host in ARP_DUMP_HOSTS:
        lines.append(f"{host} ip neigh show | tee {mininet_safe_path(arp_dir / f'{host}_static_arp.txt')}")

    # Pings e iperf3 por escenario/repetición.
    for scenario in SCENARIOS:
        sc_dir = scenario_dir(raw_dir, scenario)
        dst_ip = HOSTS[scenario.dst].ip
        for rep in range(1, repetitions + 1):
            rep_id = f"rep{rep:02d}"
            ping_file = sc_dir / f"{rep_id}_ping.txt"
            server_out = sc_dir / f"{rep_id}_iperf_server.txt"
            server_err = sc_dir / f"{rep_id}_iperf_server.stderr.txt"
            client_json = sc_dir / f"{rep_id}_iperf.json"
            client_err = sc_dir / f"{rep_id}_iperf_client.stderr.txt"
            manifest_file = sc_dir / f"{rep_id}_manifest.json"

            manifest = {
                "scenario_id": scenario.scenario_id,
                "label": scenario.label,
                "src": scenario.src,
                "dst": scenario.dst,
                "dst_ip": dst_ip,
                "port": scenario.port,
                "rep": rep,
                "expected_path": scenario.expected_path,
                "iperf_seconds": iperf_seconds,
                "ping_count": ping_count,
                "methodological_note": "Ensayo punto a punto sobre forwarding determinista instalado mediante ODL RESTCONF.",
            }
            lines.append(f"sh printf '%s' '{json.dumps(manifest, ensure_ascii=False)}' > {mininet_safe_path(manifest_file)}")

            lines.append(f"{scenario.dst} pkill -f 'iperf3.*-p {scenario.port}' || true")
            lines.append(f"{scenario.src} ping -c {ping_count} {scenario.dst} | tee {mininet_safe_path(ping_file)}")
            lines.append(
                f"{scenario.dst} iperf3 -s -1 -p {scenario.port} > {mininet_safe_path(server_out)} "
                f"2> {mininet_safe_path(server_err)} &"
            )
            lines.append("sh sleep 1")
            lines.append(
                f"{scenario.src} iperf3 -c {dst_ip} -p {scenario.port} -t {iperf_seconds} -J "
                f"> {mininet_safe_path(client_json)} 2> {mininet_safe_path(client_err)}"
            )
            lines.append("sh sleep 1")

    # Dumps de contadores tras toda la serie.
    for bridge, macs in FLOW_EVIDENCE.items():
        for mac in macs:
            safe_mac = mac.replace(":", "")
            lines.append(
                f"sh ovs-ofctl -O OpenFlow13 dump-flows {bridge} | grep '{mac}' | tee "
                f"{mininet_safe_path(flow_dir / f'{bridge}_{safe_mac}.txt')}"
            )

    lines.append("")
    return "\n".join(lines)


def parse_ping_output(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "ok": False, "reason": "missing file"}
    text = path.read_text(encoding="utf-8", errors="replace")
    stats = PING_STATS_RE.search(text)
    rtt = RTT_RE.search(text)
    result: Dict[str, Any] = {
        "exists": True,
        "ok": False,
        "tx": None,
        "rx": None,
        "loss_pct": None,
        "rtt_min_ms": None,
        "rtt_avg_ms": None,
        "rtt_max_ms": None,
        "rtt_mdev_ms": None,
        "reason": None,
    }
    if not stats:
        result["reason"] = "ping statistics not found"
        return result
    tx = int(stats.group("tx"))
    rx = int(stats.group("rx"))
    loss = float(stats.group("loss"))
    result.update({"tx": tx, "rx": rx, "loss_pct": loss})
    if rtt:
        result.update({
            "rtt_min_ms": float(rtt.group("min")),
            "rtt_avg_ms": float(rtt.group("avg")),
            "rtt_max_ms": float(rtt.group("max")),
            "rtt_mdev_ms": float(rtt.group("mdev")),
        })
    if tx > 0 and rx == tx and loss == 0.0:
        result["ok"] = True
        result["reason"] = "0% packet loss"
    else:
        result["reason"] = f"tx={tx}, rx={rx}, loss={loss}%"
    return result


def safe_get(data: Dict[str, Any], path: Sequence[Any], default: Any = None) -> Any:
    cur: Any = data
    for key in path:
        try:
            if isinstance(cur, dict):
                cur = cur[key]
            elif isinstance(cur, list) and isinstance(key, int):
                cur = cur[key]
            else:
                return default
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def parse_iperf_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "json_valid": False, "ok": False, "reason": "missing file"}
    if path.stat().st_size == 0:
        return {"exists": True, "json_valid": False, "ok": False, "reason": "empty file"}

    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return {"exists": True, "json_valid": False, "ok": False, "reason": f"json decode error: {exc}"}

    error = data.get("error")
    if error:
        return {"exists": True, "json_valid": True, "ok": False, "reason": f"iperf error: {error}"}

    # iperf3 TCP summary. La estructura varía ligeramente entre versiones.
    bits_per_second = safe_get(data, ["end", "sum_received", "bits_per_second"])
    if bits_per_second is None:
        bits_per_second = safe_get(data, ["end", "sum", "bits_per_second"])
    if bits_per_second is None:
        bits_per_second = safe_get(data, ["end", "streams", 0, "receiver", "bits_per_second"])

    sender_bps = safe_get(data, ["end", "sum_sent", "bits_per_second"])
    bytes_received = safe_get(data, ["end", "sum_received", "bytes"])
    retransmits = safe_get(data, ["end", "sum_sent", "retransmits"])
    seconds = safe_get(data, ["end", "sum_received", "seconds"])
    if seconds is None:
        seconds = safe_get(data, ["end", "sum", "seconds"])

    result = {
        "exists": True,
        "json_valid": True,
        "ok": bits_per_second is not None and float(bits_per_second) > 0,
        "receiver_bps": float(bits_per_second) if bits_per_second is not None else None,
        "receiver_mbps": float(bits_per_second) / 1e6 if bits_per_second is not None else None,
        "sender_bps": float(sender_bps) if sender_bps is not None else None,
        "sender_mbps": float(sender_bps) / 1e6 if sender_bps is not None else None,
        "bytes_received": int(bytes_received) if bytes_received is not None else None,
        "retransmits": int(retransmits) if retransmits is not None else None,
        "seconds": float(seconds) if seconds is not None else None,
        "reason": "valid throughput" if bits_per_second is not None and float(bits_per_second) > 0 else "missing throughput",
    }
    return result


def parse_flow_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "non_empty": False, "packets": None, "bytes": None, "ok": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    packets_match = PACKET_COUNTER_RE.search(text)
    bytes_match = BYTE_COUNTER_RE.search(text)
    packets = int(packets_match.group("packets")) if packets_match else None
    byte_count = int(bytes_match.group("bytes")) if bytes_match else None
    return {
        "exists": True,
        "non_empty": bool(text.strip()),
        "packets": packets,
        "bytes": byte_count,
        "ok": packets is not None and packets > 0,
    }


def prepare(args: argparse.Namespace, tfg_root: Path, campaign_id: str, artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    commands_path = artifact_dir / "mininet_ft8_3r_commands.cli"
    commands = build_mininet_commands(
        artifact_dir=artifact_dir,
        ping_count=args.ping_count,
        repetitions=args.repetitions,
        iperf_seconds=args.iperf_seconds,
    )
    write_text(commands_path, commands)

    manifest = {
        "campaign_id": campaign_id,
        "block": "FT8.3R",
        "purpose": "RESTCONF point-to-point tests for Fat-Tree k=4",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "tfg_root": str(tfg_root),
        "artifact_dir": str(artifact_dir),
        "mininet_commands_file": str(commands_path),
        "ping_count": args.ping_count,
        "iperf_seconds": args.iperf_seconds,
        "repetitions": args.repetitions,
        "scenarios": [asdict(item) for item in SCENARIOS],
        "methodological_note": (
            "FT8.3R evalúa ensayos punto a punto T1/T2/T3 sobre forwarding determinista "
            "previamente instalado mediante ODL RESTCONF. No implica ECMP ni pingall universal."
        ),
    }
    write_json(artifact_dir / "ft8_3r_manifest.json", manifest)
    return manifest


def summarize(artifact_dir: Path) -> Dict[str, Any]:
    raw_dir = artifact_dir / "raw"
    flow_dir = artifact_dir / "flow_counter_checks"

    rows: List[Dict[str, Any]] = []
    for scenario in SCENARIOS:
        sc_dir = scenario_dir(raw_dir, scenario)
        rep_files = sorted(sc_dir.glob("rep*_manifest.json"))
        for manifest_path in rep_files:
            rep_match = re.search(r"rep(\d+)_manifest\.json$", manifest_path.name)
            rep_num = int(rep_match.group(1)) if rep_match else None
            rep_id = f"rep{rep_num:02d}" if rep_num is not None else manifest_path.stem.replace("_manifest", "")

            ping_path = sc_dir / f"{rep_id}_ping.txt"
            iperf_path = sc_dir / f"{rep_id}_iperf.json"
            server_out = sc_dir / f"{rep_id}_iperf_server.txt"
            server_err = sc_dir / f"{rep_id}_iperf_server.stderr.txt"
            client_err = sc_dir / f"{rep_id}_iperf_client.stderr.txt"

            ping = parse_ping_output(ping_path)
            iperf = parse_iperf_json(iperf_path)

            row = {
                "scenario_id": scenario.scenario_id,
                "label": scenario.label,
                "src": scenario.src,
                "dst": scenario.dst,
                "port": scenario.port,
                "rep": rep_num,
                "expected_path": scenario.expected_path,
                "ping_ok": ping.get("ok"),
                "ping_tx": ping.get("tx"),
                "ping_rx": ping.get("rx"),
                "ping_loss_pct": ping.get("loss_pct"),
                "rtt_min_ms": ping.get("rtt_min_ms"),
                "rtt_avg_ms": ping.get("rtt_avg_ms"),
                "rtt_max_ms": ping.get("rtt_max_ms"),
                "rtt_mdev_ms": ping.get("rtt_mdev_ms"),
                "iperf_ok": iperf.get("ok"),
                "iperf_json_valid": iperf.get("json_valid"),
                "receiver_mbps": iperf.get("receiver_mbps"),
                "sender_mbps": iperf.get("sender_mbps"),
                "bytes_received": iperf.get("bytes_received"),
                "retransmits": iperf.get("retransmits"),
                "seconds": iperf.get("seconds"),
                "ping_file": str(ping_path),
                "iperf_file": str(iperf_path),
                "server_out_exists": server_out.exists(),
                "server_err_exists": server_err.exists(),
                "client_err_exists": client_err.exists(),
                "ok": bool(ping.get("ok")) and bool(iperf.get("ok")),
            }
            rows.append(row)

    if not rows:
        summary = {
            "status": "FAIL",
            "reason": "no repetition manifests found",
            "artifact_dir": str(artifact_dir),
        }
        write_json(artifact_dir / "ft8_3r_summary.json", summary)
        return summary

    # Evidencia de contadores.
    flow_rows: List[Dict[str, Any]] = []
    for bridge, macs in FLOW_EVIDENCE.items():
        for mac in macs:
            safe_mac = mac.replace(":", "")
            path = flow_dir / f"{bridge}_{safe_mac}.txt"
            parsed = parse_flow_file(path)
            flow_rows.append({
                "bridge": bridge,
                "mac": mac,
                "file": str(path),
                **parsed,
            })

    scenario_summaries: List[Dict[str, Any]] = []
    for scenario in SCENARIOS:
        subset = [row for row in rows if row["scenario_id"] == scenario.scenario_id]
        mbps_values = [row["receiver_mbps"] for row in subset if row["receiver_mbps"] is not None]
        rtt_values = [row["rtt_avg_ms"] for row in subset if row["rtt_avg_ms"] is not None]
        scenario_summaries.append({
            "scenario_id": scenario.scenario_id,
            "label": scenario.label,
            "src": scenario.src,
            "dst": scenario.dst,
            "expected_path": scenario.expected_path,
            "n_repetitions": len(subset),
            "n_ok": sum(1 for row in subset if row["ok"]),
            "throughput_mean_mbps": statistics.mean(mbps_values) if mbps_values else None,
            "throughput_median_mbps": statistics.median(mbps_values) if mbps_values else None,
            "throughput_min_mbps": min(mbps_values) if mbps_values else None,
            "throughput_max_mbps": max(mbps_values) if mbps_values else None,
            "rtt_avg_mean_ms": statistics.mean(rtt_values) if rtt_values else None,
            "rtt_avg_median_ms": statistics.median(rtt_values) if rtt_values else None,
        })

    n_ok = sum(1 for row in rows if row["ok"])
    flow_counter_evidence_ok = all(row["ok"] for row in flow_rows)
    status = "PASS" if n_ok == len(rows) and flow_counter_evidence_ok else "FAIL"

    summary = {
        "status": status,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_results": len(rows),
        "n_ok": n_ok,
        "n_fail": len(rows) - n_ok,
        "flow_counter_evidence_ok": flow_counter_evidence_ok,
        "scenario_summaries": scenario_summaries,
        "results": rows,
        "flow_evidence": flow_rows,
        "methodological_note": (
            "FT8.3R contiene ensayos punto a punto T1/T2/T3 sobre forwarding determinista "
            "instalado mediante ODL RESTCONF. No representa ECMP ni reconfiguración dinámica."
        ),
    }

    write_json(artifact_dir / "ft8_3r_summary.json", summary)
    write_csv(
        artifact_dir / "ft8_3r_results.csv",
        rows,
        fieldnames=[
            "scenario_id", "label", "src", "dst", "port", "rep", "expected_path",
            "ping_ok", "ping_tx", "ping_rx", "ping_loss_pct", "rtt_min_ms", "rtt_avg_ms",
            "rtt_max_ms", "rtt_mdev_ms", "iperf_ok", "iperf_json_valid", "receiver_mbps",
            "sender_mbps", "bytes_received", "retransmits", "seconds", "ok",
            "server_out_exists", "server_err_exists", "client_err_exists", "ping_file", "iperf_file",
        ],
    )
    write_csv(
        artifact_dir / "ft8_3r_scenario_summary.csv",
        scenario_summaries,
        fieldnames=[
            "scenario_id", "label", "src", "dst", "expected_path", "n_repetitions", "n_ok",
            "throughput_mean_mbps", "throughput_median_mbps", "throughput_min_mbps",
            "throughput_max_mbps", "rtt_avg_mean_ms", "rtt_avg_median_ms",
        ],
    )
    write_csv(
        artifact_dir / "ft8_3r_flow_evidence.csv",
        flow_rows,
        fieldnames=["bridge", "mac", "file", "exists", "non_empty", "packets", "bytes", "ok"],
    )

    report_lines: List[str] = []
    report_lines.append("FT8.3R — Ensayos punto a punto RESTCONF")
    report_lines.append("=" * 60)
    report_lines.append("")
    report_lines.append(f"status: {status}")
    report_lines.append(f"n_results: {len(rows)}")
    report_lines.append(f"n_ok: {n_ok}")
    report_lines.append(f"n_fail: {len(rows) - n_ok}")
    report_lines.append(f"flow_counter_evidence_ok: {flow_counter_evidence_ok}")
    report_lines.append("")
    report_lines.append("Resumen por escenario")
    report_lines.append("-" * 60)
    for item in scenario_summaries:
        report_lines.append(
            f"{item['scenario_id']:<12} {item['src']:>3}->{item['dst']:<3} "
            f"ok={item['n_ok']}/{item['n_repetitions']} "
            f"thr_mean={item['throughput_mean_mbps']} Mbps "
            f"rtt_mean={item['rtt_avg_mean_ms']} ms"
        )
    report_lines.append("")
    report_lines.append("Lectura metodológica")
    report_lines.append("-" * 60)
    report_lines.append(summary["methodological_note"])
    report_lines.append("")
    write_text(artifact_dir / "ft8_3r_report.txt", "\n".join(report_lines))

    return summary


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FT8.3R - prepara y resume ensayos punto a punto RESTCONF."
    )
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--campaign-id", default=None,
                        help="ID de campaña. Si se omite, se usa la última ch08_fattree_restconf_*.")
    parser.add_argument("--action", choices=["prepare", "summarize", "both"], default="prepare")
    parser.add_argument("--ping-count", type=int, default=10)
    parser.add_argument("--iperf-seconds", type=int, default=10)
    parser.add_argument("--repetitions", type=int, default=3)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tfg_root = Path(args.tfg_root).expanduser().resolve()
    campaign_id = args.campaign_id or find_latest_campaign(tfg_root)
    campaign_root = tfg_root / "runs" / "ch08_fattree" / campaign_id
    artifact_dir = campaign_root / "ft8_3r_p2p_restconf"

    if not campaign_root.exists():
        print(f"[FAIL] No existe la campaña: {campaign_root}", file=sys.stderr)
        return 2

    if args.action in ("prepare", "both"):
        manifest = prepare(args, tfg_root, campaign_id, artifact_dir)
        print("FT8.3R prepare: OK")
        print(f"campaign_id: {campaign_id}")
        print(f"artifact_dir: {artifact_dir}")
        print("Ejecuta dentro de Mininet:")
        print(f"source {manifest['mininet_commands_file']}")

    if args.action in ("summarize", "both"):
        summary = summarize(artifact_dir)
        print("FT8.3R summarize:", summary["status"])
        print(f"n_ok: {summary.get('n_ok')}/{summary.get('n_results')}")
        print(f"flow_counter_evidence_ok: {summary.get('flow_counter_evidence_ok')}")
        print(f"report: {artifact_dir / 'ft8_3r_report.txt'}")
        return 0 if summary["status"] == "PASS" else 10

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

