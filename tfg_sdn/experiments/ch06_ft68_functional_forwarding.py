#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

from tfg_sdn.mininet.topos.fattree import FatTreeTopo


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "ch06_fat_tree"

CORE = [f"c{i}" for i in range(1, 5)]
AGG = [f"a{i}" for i in range(1, 9)]
EDGE = [f"e{i}" for i in range(1, 9)]
ALL_SWITCHES = CORE + AGG + EDGE

RELEVANT_SWITCHES = ["e1", "e2", "a1", "c1", "a7", "e8"]


def run(cmd: str, *, check: bool = True) -> str:
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed with code {result.returncode}:\n{cmd}\n\n{result.stdout}"
        )
    return result.stdout


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def clean_previous_outputs() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for path in OUT.glob("ch06_ft68_*"):
        path.unlink()


def configure_ovs_bridges() -> None:
    bridges = run("ovs-vsctl list-br", check=True).splitlines()
    bridges = [br.strip() for br in bridges if re.match(r"^(c|a|e)\d+$", br.strip())]

    write(
        OUT / "ch06_ft68_ovs_bridges.txt",
        "\n".join(sorted(bridges)) + "\n",
    )

    for br in bridges:
        run(f"ovs-vsctl set-controller {br} tcp:127.0.0.1:6653")
        run(f"ovs-vsctl set bridge {br} protocols=OpenFlow13")
        run(f"ovs-vsctl set-fail-mode {br} secure")

    state_lines: list[str] = []
    for br in sorted(bridges):
        controller = run(f"ovs-vsctl get-controller {br}").strip()
        fail_mode = run(f"ovs-vsctl get-fail-mode {br}").strip()
        protocols = run(f"ovs-vsctl get Bridge {br} protocols").strip()
        state_lines.append(f"=== {br} ===")
        state_lines.append(f"controller: {controller}")
        state_lines.append(f"fail_mode: {fail_mode}")
        state_lines.append(f"protocols: {protocols}")

    write(
        OUT / "ch06_ft68_ovs_controller_protocols.txt",
        "\n".join(state_lines) + "\n",
    )


def clear_and_install_flows() -> None:
    for br in ALL_SWITCHES:
        run(f"ovs-ofctl -O OpenFlow13 del-flows {br}", check=False)
        run(
            f"ovs-ofctl -O OpenFlow13 add-flow {br} "
            f"'table=0,priority=0,actions=drop'"
        )

    flows = [
        # e1: h1, h2, uplink hacia a1
        ("e1", "table=0,priority=200,ip,nw_dst=10.0.0.1,actions=output:1"),
        ("e1", "table=0,priority=200,ip,nw_dst=10.0.0.2,actions=output:2"),
        ("e1", "table=0,priority=200,ip,nw_dst=10.0.0.3,actions=output:3"),
        ("e1", "table=0,priority=200,ip,nw_dst=10.0.0.16,actions=output:3"),

        # e2: h3 y retorno hacia a1
        ("e2", "table=0,priority=200,ip,nw_dst=10.0.0.1,actions=output:3"),
        ("e2", "table=0,priority=200,ip,nw_dst=10.0.0.3,actions=output:1"),

        # a1: pod 0, camino hacia e1/e2/core c1
        ("a1", "table=0,priority=200,ip,nw_dst=10.0.0.1,actions=output:1"),
        ("a1", "table=0,priority=200,ip,nw_dst=10.0.0.3,actions=output:2"),
        ("a1", "table=0,priority=200,ip,nw_dst=10.0.0.16,actions=output:3"),

        # c1: core usado para el camino inter-pod
        ("c1", "table=0,priority=200,ip,nw_dst=10.0.0.1,actions=output:1"),
        ("c1", "table=0,priority=200,ip,nw_dst=10.0.0.16,actions=output:4"),

        # a7: pod 3, camino entre c1 y e8
        ("a7", "table=0,priority=200,ip,nw_dst=10.0.0.1,actions=output:3"),
        ("a7", "table=0,priority=200,ip,nw_dst=10.0.0.16,actions=output:2"),

        # e8: h16 y retorno hacia a7
        ("e8", "table=0,priority=200,ip,nw_dst=10.0.0.1,actions=output:3"),
        ("e8", "table=0,priority=200,ip,nw_dst=10.0.0.16,actions=output:2"),
    ]

    flow_log: list[str] = []
    for sw, flow in flows:
        run(f"ovs-ofctl -O OpenFlow13 add-flow {sw} '{flow}'")
        flow_log.append(f"{sw}: {flow}")

    write(
        OUT / "ch06_ft68_installed_flows.txt",
        "\n".join(flow_log) + "\n",
    )


def set_static_arp(net: Mininet, host_name: str, ip: str, mac: str) -> None:
    host = net.get(host_name)
    intf = host.defaultIntf().name
    host.cmd(f"ip neigh replace {ip} lladdr {mac} dev {intf} nud permanent")


def configure_static_arp(net: Mininet) -> None:
    # h1 <-> h2
    set_static_arp(net, "h1", "10.0.0.2", "00:00:00:00:00:02")
    set_static_arp(net, "h2", "10.0.0.1", "00:00:00:00:00:01")

    # h1 <-> h3
    set_static_arp(net, "h1", "10.0.0.3", "00:00:00:00:00:03")
    set_static_arp(net, "h3", "10.0.0.1", "00:00:00:00:00:01")

    # h1 <-> h16
    set_static_arp(net, "h1", "10.0.0.16", "00:00:00:00:00:10")
    set_static_arp(net, "h16", "10.0.0.1", "00:00:00:00:00:01")


def run_pings(net: Mininet) -> dict[str, str]:
    cases = {
        "h1_h2_same_edge": ("h1", "10.0.0.2"),
        "h1_h3_same_pod": ("h1", "10.0.0.3"),
        "h1_h16_cross_pod": ("h1", "10.0.0.16"),
    }

    outputs: dict[str, str] = {}

    for case, (src_host, dst_ip) in cases.items():
        host = net.get(src_host)
        out = host.cmd(f"ping -c 5 -W 1 {dst_ip}")
        outputs[case] = out
        write(OUT / f"ch06_ft68_ping_{case}.txt", out)

    return outputs


def dump_relevant_flows() -> None:
    for sw in RELEVANT_SWITCHES:
        out = run(f"ovs-ofctl -O OpenFlow13 dump-flows {sw}", check=False)
        write(OUT / f"ch06_ft68_flows_{sw}.txt", out)


def capture_restconf_after() -> str:
    inv_path = OUT / "ch06_ft68_inventory_nodes_after.json"
    topo_path = OUT / "ch06_ft68_network_topology_after.json"

    inv_status = run(
        'curl -s -u "${ODL_USER:-admin}:${ODL_PASS:?Set ODL_PASS}" '
        "-H 'Accept: application/json' "
        f"-o {inv_path} "
        "-w 'inventory_http=%{http_code}\\n' "
        "'http://127.0.0.1:8181/rests/data/opendaylight-inventory:nodes?content=nonconfig'",
        check=False,
    )

    topo_status = run(
        'curl -s -u "${ODL_USER:-admin}:${ODL_PASS:?Set ODL_PASS}" '
        "-H 'Accept: application/json' "
        f"-o {topo_path} "
        "-w 'topology_http=%{http_code}\\n' "
        "'http://127.0.0.1:8181/rests/data/network-topology:network-topology?content=nonconfig'",
        check=False,
    )

    status = inv_status + topo_status
    write(OUT / "ch06_ft68_restconf_status.txt", status)
    return status


def parse_ping_loss(text: str) -> str:
    m = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+received,\s+([0-9.]+)% packet loss", text)
    if not m:
        return "unknown"
    return f"{m.group(3)}%"


def count_openflow_nodes(path: Path) -> tuple[int, list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    nodes = sorted(
        set(re.findall(r"openflow:\d+", text)),
        key=lambda x: int(x.split(":")[1]),
    )
    return len(nodes), nodes


def write_summary(ping_outputs: dict[str, str], rest_status: str) -> None:
    inv_count, inv_nodes = count_openflow_nodes(OUT / "ch06_ft68_inventory_nodes_after.json")
    topo_count, topo_nodes = count_openflow_nodes(OUT / "ch06_ft68_network_topology_after.json")

    expected = {f"openflow:{i}" for i in range(1, 21)}
    inv_missing = sorted(expected - set(inv_nodes), key=lambda x: int(x.split(":")[1]))
    topo_missing = sorted(expected - set(topo_nodes), key=lambda x: int(x.split(":")[1]))

    lines = [
        "FT6.8 - Validación funcional mínima de forwarding Fat-Tree k=4",
        "",
        "Casos de conectividad:",
    ]

    for case, out in ping_outputs.items():
        lines.append(f"- {case}: packet_loss={parse_ping_loss(out)}")

    lines += [
        "",
        "RESTCONF:",
        rest_status.strip(),
        "",
        f"inventory_openflow_nodes={inv_count}",
        f"inventory_missing={', '.join(inv_missing) if inv_missing else 'none'}",
        f"topology_openflow_nodes={topo_count}",
        f"topology_missing={', '.join(topo_missing) if topo_missing else 'none'}",
        "",
        "Switches con dumps de flows:",
        " ".join(RELEVANT_SWITCHES),
    ]

    write(
        OUT / "ch06_ft68_functional_forwarding_summary.txt",
        "\n".join(lines) + "\n",
    )


def main() -> int:
    if not hasattr(sys, "real_prefix") and hasattr(sys, "base_prefix"):
        pass

    clean_previous_outputs()
    setLogLevel("info")

    topo = FatTreeTopo(k=4, bw=0, delay="0ms")

    net = Mininet(
        topo=topo,
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=False,
    )

    net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653,
    )

    try:
        print("[FT6.8] Starting Mininet Fat-Tree k=4...")
        net.start()

        print("[FT6.8] Waiting for OVS/ODL convergence...")
        time.sleep(20)

        print("[FT6.8] Configuring OVS bridges...")
        configure_ovs_bridges()

        print("[FT6.8] Installing deterministic OpenFlow rules...")
        clear_and_install_flows()

        print("[FT6.8] Configuring static ARP entries...")
        configure_static_arp(net)

        print("[FT6.8] Running representative pings...")
        ping_outputs = run_pings(net)

        print("[FT6.8] Dumping relevant OpenFlow tables...")
        dump_relevant_flows()

        print("[FT6.8] Capturing ODL RESTCONF state...")
        rest_status = capture_restconf_after()

        print("[FT6.8] Writing summary...")
        write_summary(ping_outputs, rest_status)

        print("[FT6.8] DONE")
        print(OUT / "ch06_ft68_functional_forwarding_summary.txt")

    finally:
        print("[FT6.8] Stopping Mininet...")
        net.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
