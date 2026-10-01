from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from tfg_sdn.odl.client import OdlAuth, OdlClient
from tfg_sdn.odl.flows import flow_arp_normal, flow_table_miss_controller, flow_ipv4_dst_to_port
from tfg_sdn.experiments.common import (
    repo_root, iso_now, run_id, ensure_dir, write_text, write_json, append_csv, try_get_env_snapshot
)


@dataclass(frozen=True)
class ExpAConfig:
    topo: str                  # "basic" | "intermediate"
    odl_host: str = "127.0.0.1"
    odl_of_port: int = 6653
    odl_rest_port: int = 8181
    odl_user: str = "admin"
    odl_pass: str = ""

    dur_s: int = 10
    ping_count: int = 20
    proto: str = "both"        # tcp | udp | both
    udp_rate_mbps: int = 200   # only for udp/both
    link_bw_mbps: int = 0      # 0 = no shaping; >0 = shape with HTB


def _safe_json_loads(s: str) -> Dict[str, Any]:
    s = s.strip()
    if not s:
        return {}
    if s[0] == "{":
        return json.loads(s)
    i = s.find("{")
    j = s.rfind("}")
    if i >= 0 and j > i:
        return json.loads(s[i:j+1])
    return {}


def _parse_ping_summary(ping_text: str) -> Dict[str, Any]:
    m = re.search(r"rtt .* = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", ping_text)
    if not m:
        return {}
    return {
        "rtt_min_ms": float(m.group(1)),
        "rtt_avg_ms": float(m.group(2)),
        "rtt_max_ms": float(m.group(3)),
        "rtt_mdev_ms": float(m.group(4)),
    }


def _parse_iperf_json(j: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    end = j.get("end", {}) if isinstance(j.get("end"), dict) else {}

    sum_recv = end.get("sum_received") if isinstance(end.get("sum_received"), dict) else None
    sum_sent = end.get("sum_sent") if isinstance(end.get("sum_sent"), dict) else None
    sum_any = end.get("sum") if isinstance(end.get("sum"), dict) else None

    if sum_recv and "bits_per_second" in sum_recv:
        bps = float(sum_recv.get("bits_per_second", 0.0))
        out["throughput_mbps"] = bps / 1e6
        if sum_sent and "retransmits" in sum_sent:
            out["retransmits"] = int(sum_sent.get("retransmits", 0))
        out["proto_detected"] = "tcp"
        return out

    if sum_any and "bits_per_second" in sum_any:
        bps = float(sum_any.get("bits_per_second", 0.0))
        out["throughput_mbps"] = bps / 1e6
        if "jitter_ms" in sum_any:
            out["jitter_ms"] = float(sum_any.get("jitter_ms", 0.0))
        if "lost_percent" in sum_any:
            out["lost_percent"] = float(sum_any.get("lost_percent", 0.0))
        out["proto_detected"] = "udp"
        return out

    return out


def _util_pct(throughput_mbps: Any, bw_mbps: int) -> Any:
    if not bw_mbps or bw_mbps <= 0:
        return None
    if throughput_mbps is None:
        return None
    try:
        tp = float(throughput_mbps)
    except Exception:
        return None
    # Si sale muy por encima del bw, asumimos que el shaping no fue efectivo → no reportar utilización.
    if tp > 1.2 * bw_mbps:
        return None
    return 100.0 * tp / float(bw_mbps)


def _push_flows(cfg: ExpAConfig, odl: OdlClient) -> None:
    if cfg.topo == "basic":
        if not odl.wait_for_nodes(["openflow:1"], timeout_s=25, interval_s=1.0):
            raise RuntimeError("ODL inventory missing openflow:1")
        for fid in ["arp_normal", "table_miss", "to_h1_ip", "to_h2_ip"]:
            odl.delete_flow("openflow:1", 0, fid)
        flows = [
            flow_arp_normal(),
            flow_table_miss_controller(),
            flow_ipv4_dst_to_port("to_h1_ip", "10.0.0.1/32", 1),
            flow_ipv4_dst_to_port("to_h2_ip", "10.0.0.2/32", 2),
        ]
        for f in flows:
            code, body = odl.put_flow("openflow:1", 0, f)
            if not (200 <= code < 300):
                raise RuntimeError(f"PUT flow failed openflow:1 {f['id']} HTTP={code} body={body[:200]}")
        return

    if cfg.topo == "intermediate":
        if not odl.wait_for_nodes(["openflow:1", "openflow:2"], timeout_s=25, interval_s=1.0):
            raise RuntimeError("ODL inventory missing openflow:1/openflow:2")
        for node in ["openflow:1", "openflow:2"]:
            for fid in ["arp_normal", "table_miss", "to_h1_ip", "to_h2_ip", "to_h3_ip", "to_h4_ip"]:
                odl.delete_flow(node, 0, fid)

        flows_1 = [
            flow_arp_normal(),
            flow_table_miss_controller(),
            flow_ipv4_dst_to_port("to_h1_ip", "10.0.0.1/32", 1),
            flow_ipv4_dst_to_port("to_h3_ip", "10.0.0.3/32", 2),
            flow_ipv4_dst_to_port("to_h2_ip", "10.0.0.2/32", 3),
            flow_ipv4_dst_to_port("to_h4_ip", "10.0.0.4/32", 3),
        ]
        flows_2 = [
            flow_arp_normal(),
            flow_table_miss_controller(),
            flow_ipv4_dst_to_port("to_h2_ip", "10.0.0.2/32", 1),
            flow_ipv4_dst_to_port("to_h4_ip", "10.0.0.4/32", 2),
            flow_ipv4_dst_to_port("to_h1_ip", "10.0.0.1/32", 3),
            flow_ipv4_dst_to_port("to_h3_ip", "10.0.0.3/32", 3),
        ]
        for f in flows_1:
            code, body = odl.put_flow("openflow:1", 0, f)
            if not (200 <= code < 300):
                raise RuntimeError(f"PUT flow failed openflow:1 {f['id']} HTTP={code} body={body[:200]}")
        for f in flows_2:
            code, body = odl.put_flow("openflow:2", 0, f)
            if not (200 <= code < 300):
                raise RuntimeError(f"PUT flow failed openflow:2 {f['id']} HTTP={code} body={body[:200]}")
        return

    raise ValueError(f"Unsupported topo: {cfg.topo}")


def _apply_shaping(net, bw_mbps: int, raw_dir: Path, manifest: Dict[str, Any]) -> None:
    """
    Apply HTB shaping on ALL Mininet link interfaces (both directions).
    Stores tc qdisc snapshots for evidence.
    """
    if not bw_mbps or bw_mbps <= 0:
        manifest["results"]["shaping"] = {"enabled": False}
        return

    enabled = True
    errors: list[str] = []

    # Apply shaping to all link interfaces
    for link in net.links:
        for intf in (link.intf1, link.intf2):
            try:
                # remove any existing qdisc (best-effort)
                intf.node.cmd(f"tc qdisc del dev {intf.name} root 2>/dev/null || true")
                # Apply shaping, silencing HTB warnings printed to stderr by tc calls
                intf.node.cmd(f"bash -lc 'TC_HTB_R2Q=10 true' >/dev/null 2>&1")
                intf.config(bw=bw_mbps, use_htb=True)
            except Exception as e:
                enabled = False
                errors.append(f"{intf.name}: {e}")

    # Snapshot qdisc on host-facing interfaces for evidence
    tc_paths = {}
    for hname in ["h1", "h2"]:
        try:
            h = net.get(hname)
            intf = h.defaultIntf()
            out = h.cmd(f"tc qdisc show dev {intf.name}")
            p = raw_dir / f"tc_{hname}_{intf.name}.txt"
            write_text(p, out)
            tc_paths[hname] = str(p)
        except Exception as e:
            enabled = False
            errors.append(f"tc_snapshot_{hname}: {e}")

    manifest["results"]["shaping"] = {
        "enabled": enabled,
        "bw_mbps": bw_mbps,
        "tc_qdisc_paths": tc_paths,
        "errors": errors,
    }


def run_expA(cfg: ExpAConfig) -> Path:
    from mininet.log import setLogLevel
    from mininet.net import Mininet
    from mininet.node import OVSKernelSwitch, RemoteController
    from mininet.link import TCLink  # we always use TCLink; shaping can be enabled/disabled dynamically

    setLogLevel("warning")

    root = repo_root()
    exp = "expA"
    tag = f"{cfg.proto}_dur{cfg.dur_s}_bw{cfg.link_bw_mbps}_udp{cfg.udp_rate_mbps}"
    rid = run_id(None, cfg.topo, exp, tag)

    raw_dir = root / "data" / "raw" / cfg.topo / exp / rid
    proc_dir = root / "data" / "processed" / cfg.topo / exp
    ensure_dir(raw_dir)
    ensure_dir(proc_dir)

    if cfg.topo == "basic":
        from tfg_sdn.mininet.topos.basic import BasicTopo
        topo = BasicTopo()
    elif cfg.topo == "intermediate":
        from tfg_sdn.mininet.topos.intermediate import IntermediateTopo
        topo = IntermediateTopo()
    else:
        raise ValueError(f"Unsupported topo: {cfg.topo}")

    net = Mininet(
        topo=topo,
        controller=None,
        switch=OVSKernelSwitch,
        autoSetMacs=False,
        autoStaticArp=False,
        build=True,
        link=TCLink,
    )
    net.addController("c0", controller=RemoteController, ip=cfg.odl_host, port=int(cfg.odl_of_port))

    odl = OdlClient(
        host=cfg.odl_host,
        port=cfg.odl_rest_port,
        auth=OdlAuth(cfg.odl_user, cfg.odl_pass),
        timeout_s=6,
    )

    manifest_path = root / "runs" / rid / "manifest.json"
    ensure_dir(manifest_path.parent)

    manifest: Dict[str, Any] = {
        "run_id": rid,
        "ts": iso_now(),
        "topo": cfg.topo,
        "exp": exp,
        "params": {
            "dur_s": cfg.dur_s,
            "ping_count": cfg.ping_count,
            "proto": cfg.proto,
            "udp_rate_mbps": cfg.udp_rate_mbps,
            "link_bw_mbps": cfg.link_bw_mbps,
        },
        "paths": {},
        "results": {},
        "env": try_get_env_snapshot(),
    }

    try:
        net.start()

        # enforce fail-mode secure
        for sw in net.switches:
            sw.cmd(f"ovs-vsctl set-fail-mode {sw.name} secure")

        # apply shaping (and evidence)
        _apply_shaping(net, cfg.link_bw_mbps, raw_dir, manifest)

        # flush neigh
        for h in net.hosts:
            h.cmd("ip neigh flush all")

        # push flows before traffic
        _push_flows(cfg, odl)

        # traffic to warm up counters/ARP
        net.pingAll()

        h1 = net.get("h1")
        h2 = net.get("h2")
        server_ip = "10.0.0.2"
        port = 5201

        # start server on h2
        h2.cmd("pkill -x iperf3 || true")
        h2.cmd(f"iperf3 -s -p {port} -D")
        time.sleep(0.3)

        # ping
        ping_txt = h1.cmd(f"ping -c {cfg.ping_count} {server_ip}")
        ping_path = raw_dir / "ping.txt"
        write_text(ping_path, ping_txt)
        manifest["paths"]["ping_txt"] = str(ping_path)
        ping_metrics = _parse_ping_summary(ping_txt)
        if ping_metrics:
            manifest["results"]["ping"] = ping_metrics

        # TCP
        if cfg.proto in ("tcp", "both"):
            tcp_out = h1.cmd(f"iperf3 -c {server_ip} -p {port} -t {cfg.dur_s} -J")
            tcp_path = raw_dir / "iperf_tcp.json"
            write_text(tcp_path, tcp_out)
            manifest["paths"]["iperf_tcp_json"] = str(tcp_path)
            try:
                tcp_j = _safe_json_loads(tcp_out)
                manifest["results"]["tcp"] = _parse_iperf_json(tcp_j)
            except Exception:
                manifest["results"]["tcp"] = {"parse_error": True}

        # UDP
        if cfg.proto in ("udp", "both"):
            udp_out = h1.cmd(f"iperf3 -c {server_ip} -p {port} -u -b {cfg.udp_rate_mbps}M -t {cfg.dur_s} -J")
            udp_path = raw_dir / "iperf_udp.json"
            write_text(udp_path, udp_out)
            manifest["paths"]["iperf_udp_json"] = str(udp_path)
            try:
                udp_j = _safe_json_loads(udp_out)
                manifest["results"]["udp"] = _parse_iperf_json(udp_j)
            except Exception:
                manifest["results"]["udp"] = {"parse_error": True}

        # stop server
        h2.cmd("pkill -x iperf3 || true")

        # CSV (W2)
        csv_path = proc_dir / "w2_results.csv"
        base_row: Dict[str, Any] = {
            "run_id": rid,
            "ts": manifest["ts"],
            "topo": cfg.topo,
            "exp": exp,
            "dur_s": cfg.dur_s,
            "ping_count": cfg.ping_count,
            "link_bw_mbps": cfg.link_bw_mbps,
            "udp_rate_mbps": cfg.udp_rate_mbps,
            "rtt_avg_ms": ping_metrics.get("rtt_avg_ms") if ping_metrics else None,
            "rtt_mdev_ms": ping_metrics.get("rtt_mdev_ms") if ping_metrics else None,
        }

        if "tcp" in manifest["results"]:
            m = manifest["results"]["tcp"]
            append_csv(csv_path, {
                **base_row,
                "proto": "tcp",
                "throughput_mbps": m.get("throughput_mbps"),
                "retransmits": m.get("retransmits"),
                "jitter_ms": None,
                "lost_percent": None,
                "utilization_pct": _util_pct(m.get("throughput_mbps"), cfg.link_bw_mbps),
            })

        if "udp" in manifest["results"]:
            m = manifest["results"]["udp"]
            append_csv(csv_path, {
                **base_row,
                "proto": "udp",
                "throughput_mbps": m.get("throughput_mbps"),
                "retransmits": None,
                "jitter_ms": m.get("jitter_ms"),
                "lost_percent": m.get("lost_percent"),
                "utilization_pct": _util_pct(m.get("throughput_mbps"), cfg.link_bw_mbps),
            })

        manifest["paths"]["w2_csv"] = str(csv_path)
        write_json(manifest_path, manifest)
        return manifest_path

    finally:
        try:
            net.stop()
        except Exception:
            pass
