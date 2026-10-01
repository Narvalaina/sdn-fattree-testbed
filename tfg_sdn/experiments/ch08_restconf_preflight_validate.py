#!/usr/bin/env python3
"""
FT8.2R — Preflight RESTCONF para Fat-Tree k=4.

Ruta esperada:
    tfg_sdn/experiments/ch08_restconf_preflight_validate.py

Función:
    1) Preparar un archivo de comandos para ejecutar dentro de la CLI de Mininet.
       Ese archivo configura ARP estático y ejecuta pings representativos T1/T2/T3/T4.
    2) Resumir posteriormente los artefactos generados por esos pings.

Uso típico, con Mininet y ODL ya activos y tras ejecutar FT8.R:

    cd $TFG_ROOT

    python3 tfg_sdn/experiments/ch08_restconf_preflight_validate.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action prepare

    # Dentro de mininet>:
    source $TFG_ROOT/runs/ch08_fattree/ch08_fattree_restconf_20260519_0957/ft8_2r_preflight_restconf/mininet_ft8_2r_commands.cli

    # De nuevo en terminal normal:
    python3 tfg_sdn/experiments/ch08_restconf_preflight_validate.py \
      --campaign-id ch08_fattree_restconf_20260519_0957 \
      --action summarize

Notas metodológicas:
    - Este script no instala forwarding. Asume que FT8.R ya instaló flows mediante ODL RESTCONF.
    - No valida conectividad universal pingall.
    - Valida conectividad en los pares experimentales previstos para T1/T2/T3/T4.
    - El uso de ARP estático es intencionado porque los flows base descartan ARP.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class HostSpec:
    name: str
    ip: str
    mac: str
    iface: str


@dataclass(frozen=True)
class PingCheck:
    check_id: str
    scenario: str
    src: str
    dst: str
    expected_path: str
    output_file: str


HOSTS: Dict[str, HostSpec] = {
    f"h{i}": HostSpec(
        name=f"h{i}",
        ip=f"10.0.0.{i}",
        mac=f"00:00:00:00:00:{i:02x}",
        iface=f"h{i}-eth1",
    )
    for i in range(1, 17)
}

PING_CHECKS: List[PingCheck] = [
    PingCheck(
        check_id="t1_h1_h2",
        scenario="T1 local",
        src="h1",
        dst="h2",
        expected_path="h1-e1-h2",
        output_file="t1_h1_h2_ping.txt",
    ),
    PingCheck(
        check_id="t2_h1_h3",
        scenario="T2 intra-pod",
        src="h1",
        dst="h3",
        expected_path="h1-e1-a1-e2-h3",
        output_file="t2_h1_h3_ping.txt",
    ),
    PingCheck(
        check_id="t3_h1_h16",
        scenario="T3 inter-pod",
        src="h1",
        dst="h16",
        expected_path="h1-e1-a1-c1-a7-e8-h16",
        output_file="t3_h1_h16_ping.txt",
    ),
    PingCheck(
        check_id="t4_h3_h14",
        scenario="T4 concurrent representative",
        src="h3",
        dst="h14",
        expected_path="h3-e2-a2-c4-a8-e7-h14",
        output_file="t4_h3_h14_ping.txt",
    ),
    PingCheck(
        check_id="t4_h5_h12",
        scenario="T4 concurrent representative",
        src="h5",
        dst="h12",
        expected_path="h5-e3-a3-c2-a5-e6-h12",
        output_file="t4_h5_h12_ping.txt",
    ),
    PingCheck(
        check_id="t4_h7_h10",
        scenario="T4 concurrent representative",
        src="h7",
        dst="h10",
        expected_path="h7-e4-a4-c3-a6-e5-h10",
        output_file="t4_h7_h10_ping.txt",
    ),
]

# Pares necesarios para los pings anteriores. Se instalan en ambos sentidos.
ARP_PAIRS: List[Tuple[str, str]] = [
    ("h1", "h2"),
    ("h1", "h3"),
    ("h1", "h16"),
    ("h3", "h14"),
    ("h5", "h12"),
    ("h7", "h10"),
]

ARP_DUMP_HOSTS: List[str] = ["h1", "h2", "h3", "h5", "h7", "h10", "h12", "h14", "h16"]

# Dumps OVS mínimos para dejar evidencia de que las rutas tienen reglas instaladas.
FLOW_DUMPS: Dict[str, List[str]] = {
    "e1": ["00:00:00:00:00:02", "00:00:00:00:00:03", "00:00:00:00:00:10"],
    "a1": ["00:00:00:00:00:01", "00:00:00:00:00:03", "00:00:00:00:00:10"],
    "c1": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "a7": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "e8": ["00:00:00:00:00:01", "00:00:00:00:00:10"],
    "e2": ["00:00:00:00:00:01", "00:00:00:00:00:03", "00:00:00:00:00:0e"],
    "e3": ["00:00:00:00:00:05", "00:00:00:00:00:0c"],
    "e4": ["00:00:00:00:00:07", "00:00:00:00:00:0a"],
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
    # Las rutas del usuario no tienen espacios, pero dejamos la conversión explícita.
    return str(path)


def build_mininet_commands(artifact_dir: Path, ping_count: int) -> str:
    connectivity_dir = artifact_dir / "connectivity_checks"
    flow_dir = artifact_dir / "flow_counter_checks"

    lines: List[str] = []
    lines.append("# FT8.2R - comandos generados para la CLI de Mininet")
    lines.append("# Ejecutar desde el prompt: mininet> source <este_archivo>")
    lines.append("")
    lines.append(f"sh mkdir -p {mininet_safe_path(connectivity_dir)}")
    lines.append(f"sh mkdir -p {mininet_safe_path(flow_dir)}")
    lines.append("")

    lines.append("# 1) Limpieza de entradas ARP previas en hosts participantes")
    for host in ARP_DUMP_HOSTS:
        lines.append(f"{host} ip neigh flush all")
    lines.append("")

    lines.append("# 2) Instalación de ARP estático bidireccional para pares experimentales")
    for a, b in ARP_PAIRS:
        lines.append(host_peer_arp_command(a, b))
        lines.append(host_peer_arp_command(b, a))
    lines.append("")

    lines.append("# 3) Evidencia de tablas ARP estáticas")
    for host in ARP_DUMP_HOSTS:
        lines.append(
            f"{host} ip neigh show | tee {mininet_safe_path(connectivity_dir / f'{host}_static_arp.txt')}"
        )
    lines.append("")

    lines.append("# 4) Pings representativos T1/T2/T3/T4")
    for check in PING_CHECKS:
        lines.append(
            f"{check.src} ping -c {ping_count} {check.dst} | tee "
            f"{mininet_safe_path(connectivity_dir / check.output_file)}"
        )
    lines.append("")

    lines.append("# 5) Evidencia de flows relevantes y contadores tras los pings")
    for bridge, macs in FLOW_DUMPS.items():
        for mac in macs:
            safe_mac = mac.replace(":", "")
            lines.append(
                f"sh ovs-ofctl -O OpenFlow13 dump-flows {bridge} | grep '{mac}' | tee "
                f"{mininet_safe_path(flow_dir / f'{bridge}_{safe_mac}.txt')}"
            )
    lines.append("")
    lines.append("# Fin FT8.2R")
    lines.append("")
    return "\n".join(lines)


def parse_ping_output(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "file": str(path),
            "exists": False,
            "ok": False,
            "reason": "missing file",
        }

    text = path.read_text(encoding="utf-8", errors="replace")
    stats = PING_STATS_RE.search(text)
    rtt = RTT_RE.search(text)

    result: Dict[str, Any] = {
        "file": str(path),
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


def summarize(artifact_dir: Path) -> Dict[str, Any]:
    connectivity_dir = artifact_dir / "connectivity_checks"
    flow_dir = artifact_dir / "flow_counter_checks"

    rows: List[Dict[str, Any]] = []
    for check in PING_CHECKS:
        parsed = parse_ping_output(connectivity_dir / check.output_file)
        row = {
            "check_id": check.check_id,
            "scenario": check.scenario,
            "src": check.src,
            "dst": check.dst,
            "expected_path": check.expected_path,
            "output_file": check.output_file,
            **parsed,
        }
        rows.append(row)

    arp_files = []
    for host in ARP_DUMP_HOSTS:
        path = connectivity_dir / f"{host}_static_arp.txt"
        arp_files.append({
            "host": host,
            "file": str(path),
            "exists": path.exists(),
            "non_empty": path.exists() and path.stat().st_size > 0,
        })

    flow_files = []
    for bridge, macs in FLOW_DUMPS.items():
        for mac in macs:
            safe_mac = mac.replace(":", "")
            path = flow_dir / f"{bridge}_{safe_mac}.txt"
            flow_files.append({
                "bridge": bridge,
                "mac": mac,
                "file": str(path),
                "exists": path.exists(),
                "non_empty": path.exists() and path.stat().st_size > 0,
            })

    n_ok = sum(1 for row in rows if row["ok"])
    n_total = len(rows)
    arp_ok = all(item["exists"] and item["non_empty"] for item in arp_files)
    flow_evidence_ok = all(item["exists"] and item["non_empty"] for item in flow_files)

    summary = {
        "status": "PASS" if n_ok == n_total and arp_ok else "FAIL",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_ping_checks": n_total,
        "n_ping_ok": n_ok,
        "n_ping_fail": n_total - n_ok,
        "arp_evidence_ok": arp_ok,
        "flow_counter_evidence_ok": flow_evidence_ok,
        "ping_checks": rows,
        "arp_files": arp_files,
        "flow_files": flow_files,
        "methodological_note": (
            "FT8.2R valida conectividad en los pares experimentales previstos tras "
            "instalar forwarding determinista vía ODL RESTCONF y ARP estático. "
            "No valida pingall universal ni ECMP."
        ),
    }

    write_json(artifact_dir / "ft8_2r_preflight_summary.json", summary)
    write_csv(
        artifact_dir / "ft8_2r_ping_summary.csv",
        rows,
        fieldnames=[
            "check_id", "scenario", "src", "dst", "expected_path", "output_file",
            "exists", "ok", "tx", "rx", "loss_pct", "rtt_min_ms", "rtt_avg_ms",
            "rtt_max_ms", "rtt_mdev_ms", "reason", "file",
        ],
    )

    report_lines: List[str] = []
    report_lines.append("FT8.2R — Preflight RESTCONF Fat-Tree k=4")
    report_lines.append("=" * 58)
    report_lines.append("")
    report_lines.append(f"status: {summary['status']}")
    report_lines.append(f"n_ping_checks: {n_total}")
    report_lines.append(f"n_ping_ok: {n_ok}")
    report_lines.append(f"n_ping_fail: {n_total - n_ok}")
    report_lines.append(f"arp_evidence_ok: {arp_ok}")
    report_lines.append(f"flow_counter_evidence_ok: {flow_evidence_ok}")
    report_lines.append("")
    report_lines.append("Ping checks")
    report_lines.append("-" * 58)
    for row in rows:
        report_lines.append(
            f"{row['check_id']:<12} {row['src']:>3}->{row['dst']:<3} "
            f"loss={row['loss_pct']}% avg={row['rtt_avg_ms']} ms status={'OK' if row['ok'] else 'FAIL'}"
        )
    report_lines.append("")
    report_lines.append("Lectura metodológica")
    report_lines.append("-" * 58)
    report_lines.append(summary["methodological_note"])
    report_lines.append("")

    write_text(artifact_dir / "ft8_2r_preflight_report.txt", "\n".join(report_lines))
    return summary


def prepare(args: argparse.Namespace, tfg_root: Path, campaign_id: str, artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    commands_path = artifact_dir / "mininet_ft8_2r_commands.cli"
    commands = build_mininet_commands(artifact_dir=artifact_dir, ping_count=args.ping_count)
    write_text(commands_path, commands)

    manifest = {
        "campaign_id": campaign_id,
        "block": "FT8.2R",
        "purpose": "RESTCONF preflight connectivity validation for Fat-Tree k=4",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "tfg_root": str(tfg_root),
        "artifact_dir": str(artifact_dir),
        "mininet_commands_file": str(commands_path),
        "ping_count": args.ping_count,
        "checks": [asdict(item) for item in PING_CHECKS],
        "arp_pairs": ARP_PAIRS,
        "methodological_note": (
            "El preflight FT8.2R comprueba los pares experimentales previstos. "
            "No equivale a pingall ni a conectividad universal."
        ),
    }
    write_json(artifact_dir / "ft8_2r_manifest.json", manifest)

    return manifest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FT8.2R - prepara y resume validación preflight RESTCONF en Mininet."
    )
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--campaign-id", default=None,
                        help="ID de campaña. Si se omite, se usa la última ch08_fattree_restconf_*.")
    parser.add_argument("--action", choices=["prepare", "summarize", "both"], default="prepare")
    parser.add_argument("--ping-count", type=int, default=10)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tfg_root = Path(args.tfg_root).expanduser().resolve()
    campaign_id = args.campaign_id or find_latest_campaign(tfg_root)
    campaign_root = tfg_root / "runs" / "ch08_fattree" / campaign_id
    artifact_dir = campaign_root / "ft8_2r_preflight_restconf"

    if not campaign_root.exists():
        print(f"[FAIL] No existe la campaña: {campaign_root}", file=sys.stderr)
        return 2

    if args.action in ("prepare", "both"):
        manifest = prepare(args, tfg_root, campaign_id, artifact_dir)
        print("FT8.2R prepare: OK")
        print(f"campaign_id: {campaign_id}")
        print(f"artifact_dir: {artifact_dir}")
        print("Ejecuta dentro de Mininet:")
        print(f"source {manifest['mininet_commands_file']}")

    if args.action in ("summarize", "both"):
        summary = summarize(artifact_dir)
        print("FT8.2R summarize:", summary["status"])
        print(f"n_ping_ok: {summary['n_ping_ok']}/{summary['n_ping_checks']}")
        print(f"arp_evidence_ok: {summary['arp_evidence_ok']}")
        print(f"flow_counter_evidence_ok: {summary['flow_counter_evidence_ok']}")
        print(f"report: {artifact_dir / 'ft8_2r_preflight_report.txt'}")
        return 0 if summary["status"] == "PASS" else 10

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

