#!/usr/bin/env python3
"""
FT8.4R — Ensayo concurrente distribuido RESTCONF para Fat-Tree k=4.

Ruta esperada:
    tfg_sdn/experiments/ch08_restconf_concurrent_tests.py

Función:
    1) Preparar un archivo de comandos para ejecutar dentro de la CLI de Mininet.
       Ese archivo:
         - refuerza ARP estático para los pares concurrentes T4;
         - ejecuta pings de precheck;
         - lanza cuatro servidores iperf3;
         - lanza cuatro clientes iperf3 de forma simultánea;
         - captura JSON por flujo y repetición;
         - captura dumps de flows/contadores tras las pruebas.

    2) Resumir posteriormente los artefactos generados:
         - ping: pérdida y RTT;
         - iperf3 JSON: throughput por flujo, duración, bytes, retransmisiones si existen;
         - agregado concurrente por repetición;
         - evidencia de flows con contadores no nulos.

Uso típico, con Mininet y ODL ya activos, y tras FT8.R/FT8.2R/FT8.3R PASS:

    cd $TFG_ROOT

    python3 tfg_sdn/experiments/ch08_restconf_concurrent_tests.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action prepare

    # Dentro de mininet>:
    source $TFG_ROOT/runs/ch08_fattree/ch08_fattree_restconf_20260519_0957/ft8_4r_concurrent_restconf/mininet_ft8_4r_commands.cli

    # De nuevo en terminal normal:
    python3 tfg_sdn/experiments/ch08_restconf_concurrent_tests.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action summarize

Notas metodológicas:
    - Este script no instala forwarding. Asume que FT8.R ya instaló flows por ODL RESTCONF.
    - No implementa ECMP ni balanceo dinámico.
    - Evalúa cuatro flujos simultáneos sobre caminos deterministas explícitos.
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
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class HostSpec:
    name: str
    ip: str
    mac: str
    iface: str


@dataclass(frozen=True)
class ConcurrentFlow:
    flow_id: str
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

FLOWS: List[ConcurrentFlow] = [
    ConcurrentFlow(
        flow_id="t4_h1_h16",
        label="T4 flow 1",
        src="h1",
        dst="h16",
        port=5401,
        expected_path="h1-e1-a1-c1-a7-e8-h16",
        description="Flujo inter-pod desde el pod inicial hacia el último edge.",
    ),
    ConcurrentFlow(
        flow_id="t4_h3_h14",
        label="T4 flow 2",
        src="h3",
        dst="h14",
        port=5402,
        expected_path="h3-e2-a2-c4-a8-e7-h14",
        description="Flujo inter-pod alternativo atravesando c4.",
    ),
    ConcurrentFlow(
        flow_id="t4_h5_h12",
        label="T4 flow 3",
        src="h5",
        dst="h12",
        port=5403,
        expected_path="h5-e3-a3-c2-a5-e6-h12",
        description="Flujo inter-pod distribuido atravesando c2.",
    ),
    ConcurrentFlow(
        flow_id="t4_h7_h10",
        label="T4 flow 4",
        src="h7",
        dst="h10",
        port=5404,
        expected_path="h7-e4-a4-c3-a6-e5-h10",
        description="Flujo inter-pod distribuido atravesando c3.",
    ),
]

ARP_DUMP_HOSTS: List[str] = sorted({flow.src for flow in FLOWS} | {flow.dst for flow in FLOWS})

# Evidencia de todos los caminos deterministas T4. Se espera al menos una línea no vacía y contadores > 0.
FLOW_EVIDENCE: Dict[str, List[str]] = {
    "e1": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "a1": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "c1": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "a7": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "e8": ["00:00:00:00:00:01", "00:00:00:00:00:10"],

    "e2": ["00:00:00:00:00:03", "00:00:00:00:00:0e"],
    "a2": ["00:00:00:00:00:03", "00:00:00:00:00:0e"],
    "c4": ["00:00:00:00:00:03", "00:00:00:00:00:0e"],
    "a8": ["00:00:00:00:00:03", "00:00:00:00:00:0e"],
    "e7": ["00:00:00:00:00:03", "00:00:00:00:00:0e"],

    "e3": ["00:00:00:00:00:05", "00:00:00:00:00:0c"],
    "a3": ["00:00:00:00:00:05", "00:00:00:00:00:0c"],
    "c2": ["00:00:00:00:00:05", "00:00:00:00:00:0c"],
    "a5": ["00:00:00:00:00:05", "00:00:00:00:00:0c"],
    "e6": ["00:00:00:00:00:05", "00:00:00:00:00:0c"],

    "e4": ["00:00:00:00:00:07", "00:00:00:00:00:0a"],
    "a4": ["00:00:00:00:00:07", "00:00:00:00:00:0a"],
    "c3": ["00:00:00:00:00:07", "00:00:00:00:00:0a"],
    "a6": ["00:00:00:00:00:07", "00:00:00:00:00:0a"],
    "e5": ["00:00:00:00:00:07", "00:00:00:00:00:0a"],
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


def rep_dir(base: Path, rep: int) -> Path:
    return base / f"rep{rep:02d}"


def build_mininet_commands(artifact_dir: Path, ping_count: int, repetitions: int, iperf_seconds: int) -> str:
    lines: List[str] = []
    raw_dir = artifact_dir / "raw"
    flow_dir = artifact_dir / "flow_counter_checks"
    arp_dir = artifact_dir / "arp_checks"
    precheck_dir = artifact_dir / "connectivity_precheck"

    lines.append(f"sh mkdir -p {mininet_safe_path(raw_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(flow_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(arp_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(precheck_dir)}")

    for rep in range(1, repetitions + 1):
        lines.append(f"sh mkdir -p {mininet_safe_path(rep_dir(raw_dir, rep))}")

    # Limpieza de iperf3 previo en todos los hosts participantes.
    for host in ARP_DUMP_HOSTS:
        lines.append(f"{host} pkill -f iperf3 || true")

    # ARP estático bidireccional para todos los flujos T4.
    for flow in FLOWS:
        lines.append(f"{flow.src} ip neigh flush all")
        lines.append(f"{flow.dst} ip neigh flush all")
        lines.append(host_peer_arp_command(flow.src, flow.dst))
        lines.append(host_peer_arp_command(flow.dst, flow.src))

    for host in ARP_DUMP_HOSTS:
        lines.append(f"{host} ip neigh show | tee {mininet_safe_path(arp_dir / f'{host}_static_arp.txt')}")

    # Precheck de conectividad antes de los ensayos concurrentes.
    for flow in FLOWS:
        lines.append(
            f"{flow.src} ping -c {ping_count} {flow.dst} | tee "
            f"{mininet_safe_path(precheck_dir / f'{flow.flow_id}_ping.txt')}"
        )

    for rep in range(1, repetitions + 1):
        current_rep_dir = rep_dir(raw_dir, rep)
        rep_manifest = {
            "rep": rep,
            "flows": [asdict(flow) for flow in FLOWS],
            "iperf_seconds": iperf_seconds,
            "ping_count": ping_count,
            "methodological_note": "Ensayo concurrente T4 sobre forwarding determinista instalado mediante ODL RESTCONF.",
        }
        lines.append(f"sh printf '%s' '{json.dumps(rep_manifest, ensure_ascii=False)}' > {mininet_safe_path(current_rep_dir / 'rep_manifest.json')}")

        for flow in FLOWS:
            lines.append(f"{flow.dst} pkill -f 'iperf3.*-p {flow.port}' || true")

        # Arranque de servidores en segundo plano.
        for flow in FLOWS:
            server_out = current_rep_dir / f"{flow.flow_id}_iperf_server.txt"
            server_err = current_rep_dir / f"{flow.flow_id}_iperf_server.stderr.txt"
            lines.append(
                f"{flow.dst} iperf3 -s -1 -p {flow.port} > {mininet_safe_path(server_out)} "
                f"2> {mininet_safe_path(server_err)} &"
            )

        lines.append("sh sleep 1")

        # Evidencia ligera de puertos de servidor escuchando.
        for flow in FLOWS:
            ready_file = current_rep_dir / f"{flow.flow_id}_server_ready.txt"
            lines.append(
                f"{flow.dst} ss -lnt | grep ':{flow.port}' | tee {mininet_safe_path(ready_file)}"
            )

        # Lanzamiento simultáneo de clientes.
        for flow in FLOWS:
            dst_ip = HOSTS[flow.dst].ip
            client_json = current_rep_dir / f"{flow.flow_id}_iperf.json"
            client_err = current_rep_dir / f"{flow.flow_id}_iperf_client.stderr.txt"
            lines.append(
                f"{flow.src} iperf3 -c {dst_ip} -p {flow.port} -t {iperf_seconds} -J "
                f"> {mininet_safe_path(client_json)} 2> {mininet_safe_path(client_err)} &"
            )

        # Espera conservadora para que terminen los cuatro clientes y los servidores -1.
        lines.append(f"sh sleep {iperf_seconds + 4}")

        # Limpieza defensiva de iperf3 por si algún servidor queda vivo.
        for host in ARP_DUMP_HOSTS:
            lines.append(f"{host} pkill -f iperf3 || true")
        lines.append("sh sleep 1")

    # Dumps de contadores tras toda la serie concurrente.
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

    receiver_bps = safe_get(data, ["end", "sum_received", "bits_per_second"])
    if receiver_bps is None:
        receiver_bps = safe_get(data, ["end", "sum", "bits_per_second"])
    if receiver_bps is None:
        receiver_bps = safe_get(data, ["end", "streams", 0, "receiver", "bits_per_second"])

    sender_bps = safe_get(data, ["end", "sum_sent", "bits_per_second"])
    bytes_received = safe_get(data, ["end", "sum_received", "bytes"])
    retransmits = safe_get(data, ["end", "sum_sent", "retransmits"])
    seconds = safe_get(data, ["end", "sum_received", "seconds"])
    if seconds is None:
        seconds = safe_get(data, ["end", "sum", "seconds"])

    ok = receiver_bps is not None and float(receiver_bps) > 0
    return {
        "exists": True,
        "json_valid": True,
        "ok": ok,
        "receiver_bps": float(receiver_bps) if receiver_bps is not None else None,
        "receiver_mbps": float(receiver_bps) / 1e6 if receiver_bps is not None else None,
        "sender_bps": float(sender_bps) if sender_bps is not None else None,
        "sender_mbps": float(sender_bps) / 1e6 if sender_bps is not None else None,
        "bytes_received": int(bytes_received) if bytes_received is not None else None,
        "retransmits": int(retransmits) if retransmits is not None else None,
        "seconds": float(seconds) if seconds is not None else None,
        "reason": "valid throughput" if ok else "missing throughput",
    }


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
    commands_path = artifact_dir / "mininet_ft8_4r_commands.cli"
    commands = build_mininet_commands(
        artifact_dir=artifact_dir,
        ping_count=args.ping_count,
        repetitions=args.repetitions,
        iperf_seconds=args.iperf_seconds,
    )
    write_text(commands_path, commands)

    manifest = {
        "campaign_id": campaign_id,
        "block": "FT8.4R",
        "purpose": "RESTCONF concurrent distributed tests for Fat-Tree k=4",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "tfg_root": str(tfg_root),
        "artifact_dir": str(artifact_dir),
        "mininet_commands_file": str(commands_path),
        "ping_count": args.ping_count,
        "iperf_seconds": args.iperf_seconds,
        "repetitions": args.repetitions,
        "flows": [asdict(item) for item in FLOWS],
        "methodological_note": (
            "FT8.4R evalúa cuatro flujos simultáneos sobre caminos deterministas instalados "
            "mediante ODL RESTCONF. No implica ECMP, balanceo dinámico ni pingall universal."
        ),
    }
    write_json(artifact_dir / "ft8_4r_manifest.json", manifest)
    return manifest


def summarize(artifact_dir: Path) -> Dict[str, Any]:
    raw_dir = artifact_dir / "raw"
    flow_dir = artifact_dir / "flow_counter_checks"
    precheck_dir = artifact_dir / "connectivity_precheck"

    precheck_rows: List[Dict[str, Any]] = []
    for flow in FLOWS:
        path = precheck_dir / f"{flow.flow_id}_ping.txt"
        ping = parse_ping_output(path)
        precheck_rows.append({
            "flow_id": flow.flow_id,
            "src": flow.src,
            "dst": flow.dst,
            "expected_path": flow.expected_path,
            "ping_file": str(path),
            **ping,
        })

    rows: List[Dict[str, Any]] = []
    rep_dirs = sorted([p for p in raw_dir.glob("rep*") if p.is_dir()])
    for current_rep_dir in rep_dirs:
        rep_match = re.search(r"rep(\d+)$", current_rep_dir.name)
        rep = int(rep_match.group(1)) if rep_match else None
        for flow in FLOWS:
            iperf_path = current_rep_dir / f"{flow.flow_id}_iperf.json"
            server_out = current_rep_dir / f"{flow.flow_id}_iperf_server.txt"
            server_err = current_rep_dir / f"{flow.flow_id}_iperf_server.stderr.txt"
            client_err = current_rep_dir / f"{flow.flow_id}_iperf_client.stderr.txt"
            ready_file = current_rep_dir / f"{flow.flow_id}_server_ready.txt"
            iperf = parse_iperf_json(iperf_path)
            rows.append({
                "rep": rep,
                "flow_id": flow.flow_id,
                "label": flow.label,
                "src": flow.src,
                "dst": flow.dst,
                "port": flow.port,
                "expected_path": flow.expected_path,
                "iperf_ok": iperf.get("ok"),
                "iperf_json_valid": iperf.get("json_valid"),
                "receiver_mbps": iperf.get("receiver_mbps"),
                "sender_mbps": iperf.get("sender_mbps"),
                "bytes_received": iperf.get("bytes_received"),
                "retransmits": iperf.get("retransmits"),
                "seconds": iperf.get("seconds"),
                "reason": iperf.get("reason"),
                "iperf_file": str(iperf_path),
                "server_ready_exists": ready_file.exists(),
                "server_ready_non_empty": ready_file.exists() and ready_file.stat().st_size > 0,
                "server_out_exists": server_out.exists(),
                "server_err_exists": server_err.exists(),
                "client_err_exists": client_err.exists(),
                "ok": bool(iperf.get("ok")),
            })

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

    if not rows:
        summary = {
            "status": "FAIL",
            "reason": "no concurrent iperf results found",
            "artifact_dir": str(artifact_dir),
        }
        write_json(artifact_dir / "ft8_4r_summary.json", summary)
        return summary

    rep_summaries: List[Dict[str, Any]] = []
    for rep in sorted({row["rep"] for row in rows if row["rep"] is not None}):
        subset = [row for row in rows if row["rep"] == rep]
        mbps_values = [row["receiver_mbps"] for row in subset if row["receiver_mbps"] is not None]
        aggregate = sum(mbps_values)
        rep_summaries.append({
            "rep": rep,
            "n_flows": len(subset),
            "n_ok": sum(1 for row in subset if row["ok"]),
            "aggregate_receiver_mbps": aggregate,
            "mean_flow_receiver_mbps": statistics.mean(mbps_values) if mbps_values else None,
            "min_flow_receiver_mbps": min(mbps_values) if mbps_values else None,
            "max_flow_receiver_mbps": max(mbps_values) if mbps_values else None,
        })

    flow_summaries: List[Dict[str, Any]] = []
    for flow in FLOWS:
        subset = [row for row in rows if row["flow_id"] == flow.flow_id]
        mbps_values = [row["receiver_mbps"] for row in subset if row["receiver_mbps"] is not None]
        flow_summaries.append({
            "flow_id": flow.flow_id,
            "src": flow.src,
            "dst": flow.dst,
            "expected_path": flow.expected_path,
            "n_repetitions": len(subset),
            "n_ok": sum(1 for row in subset if row["ok"]),
            "throughput_mean_mbps": statistics.mean(mbps_values) if mbps_values else None,
            "throughput_median_mbps": statistics.median(mbps_values) if mbps_values else None,
            "throughput_min_mbps": min(mbps_values) if mbps_values else None,
            "throughput_max_mbps": max(mbps_values) if mbps_values else None,
        })

    n_ok = sum(1 for row in rows if row["ok"])
    precheck_ok = all(row["ok"] for row in precheck_rows)
    flow_counter_evidence_ok = all(row["ok"] for row in flow_rows)
    status = "PASS" if n_ok == len(rows) and precheck_ok and flow_counter_evidence_ok else "FAIL"

    summary = {
        "status": status,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_results": len(rows),
        "n_ok": n_ok,
        "n_fail": len(rows) - n_ok,
        "precheck_ok": precheck_ok,
        "flow_counter_evidence_ok": flow_counter_evidence_ok,
        "precheck_results": precheck_rows,
        "results": rows,
        "rep_summaries": rep_summaries,
        "flow_summaries": flow_summaries,
        "flow_evidence": flow_rows,
        "methodological_note": (
            "FT8.4R contiene un ensayo concurrente distribuido con cuatro flujos simultáneos "
            "sobre forwarding determinista instalado mediante ODL RESTCONF. No representa ECMP, "
            "balanceo automático ni reconfiguración dinámica."
        ),
    }

    write_json(artifact_dir / "ft8_4r_summary.json", summary)
    write_csv(
        artifact_dir / "ft8_4r_results.csv",
        rows,
        fieldnames=[
            "rep", "flow_id", "label", "src", "dst", "port", "expected_path",
            "iperf_ok", "iperf_json_valid", "receiver_mbps", "sender_mbps", "bytes_received",
            "retransmits", "seconds", "reason", "ok", "server_ready_exists", "server_ready_non_empty",
            "server_out_exists", "server_err_exists", "client_err_exists", "iperf_file",
        ],
    )
    write_csv(
        artifact_dir / "ft8_4r_rep_summary.csv",
        rep_summaries,
        fieldnames=[
            "rep", "n_flows", "n_ok", "aggregate_receiver_mbps", "mean_flow_receiver_mbps",
            "min_flow_receiver_mbps", "max_flow_receiver_mbps",
        ],
    )
    write_csv(
        artifact_dir / "ft8_4r_flow_summary.csv",
        flow_summaries,
        fieldnames=[
            "flow_id", "src", "dst", "expected_path", "n_repetitions", "n_ok",
            "throughput_mean_mbps", "throughput_median_mbps", "throughput_min_mbps", "throughput_max_mbps",
        ],
    )
    write_csv(
        artifact_dir / "ft8_4r_precheck_summary.csv",
        precheck_rows,
        fieldnames=[
            "flow_id", "src", "dst", "expected_path", "exists", "ok", "tx", "rx", "loss_pct",
            "rtt_min_ms", "rtt_avg_ms", "rtt_max_ms", "rtt_mdev_ms", "reason", "ping_file",
        ],
    )
    write_csv(
        artifact_dir / "ft8_4r_flow_evidence.csv",
        flow_rows,
        fieldnames=["bridge", "mac", "file", "exists", "non_empty", "packets", "bytes", "ok"],
    )

    report_lines: List[str] = []
    report_lines.append("FT8.4R — Ensayo concurrente distribuido RESTCONF")
    report_lines.append("=" * 68)
    report_lines.append("")
    report_lines.append(f"status: {status}")
    report_lines.append(f"n_results: {len(rows)}")
    report_lines.append(f"n_ok: {n_ok}")
    report_lines.append(f"n_fail: {len(rows) - n_ok}")
    report_lines.append(f"precheck_ok: {precheck_ok}")
    report_lines.append(f"flow_counter_evidence_ok: {flow_counter_evidence_ok}")
    report_lines.append("")
    report_lines.append("Resumen por repetición")
    report_lines.append("-" * 68)
    for item in rep_summaries:
        report_lines.append(
            f"rep{int(item['rep']):02d} ok={item['n_ok']}/{item['n_flows']} "
            f"aggregate_receiver_mbps={item['aggregate_receiver_mbps']} "
            f"mean_flow_receiver_mbps={item['mean_flow_receiver_mbps']}"
        )
    report_lines.append("")
    report_lines.append("Resumen por flujo")
    report_lines.append("-" * 68)
    for item in flow_summaries:
        report_lines.append(
            f"{item['flow_id']:<12} {item['src']:>3}->{item['dst']:<3} "
            f"ok={item['n_ok']}/{item['n_repetitions']} "
            f"thr_mean={item['throughput_mean_mbps']} Mbps"
        )
    report_lines.append("")
    report_lines.append("Lectura metodológica")
    report_lines.append("-" * 68)
    report_lines.append(summary["methodological_note"])
    report_lines.append("")
    write_text(artifact_dir / "ft8_4r_report.txt", "\n".join(report_lines))

    return summary


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FT8.4R - prepara y resume ensayo concurrente distribuido RESTCONF."
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
    artifact_dir = campaign_root / "ft8_4r_concurrent_restconf"

    if not campaign_root.exists():
        print(f"[FAIL] No existe la campaña: {campaign_root}", file=sys.stderr)
        return 2

    if args.action in ("prepare", "both"):
        manifest = prepare(args, tfg_root, campaign_id, artifact_dir)
        print("FT8.4R prepare: OK")
        print(f"campaign_id: {campaign_id}")
        print(f"artifact_dir: {artifact_dir}")
        print("Ejecuta dentro de Mininet:")
        print(f"source {manifest['mininet_commands_file']}")

    if args.action in ("summarize", "both"):
        summary = summarize(artifact_dir)
        print("FT8.4R summarize:", summary["status"])
        print(f"n_ok: {summary.get('n_ok')}/{summary.get('n_results')}")
        print(f"precheck_ok: {summary.get('precheck_ok')}")
        print(f"flow_counter_evidence_ok: {summary.get('flow_counter_evidence_ok')}")
        print(f"report: {artifact_dir / 'ft8_4r_report.txt'}")
        return 0 if summary["status"] == "PASS" else 10

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

