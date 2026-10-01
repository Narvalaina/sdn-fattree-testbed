#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

try:
    from mininet.topo import Topo
except ImportError:  # Allows metadata generation/tests without Mininet installed.
    Topo = object  # type: ignore[misc,assignment]


def _as_int(value: Any) -> int:
    return int(value)


def _as_float(value: Any) -> float:
    return float(value)


def _host_mac(host_id: int) -> str:
    return f"00:00:00:00:00:{host_id:02x}"


def _dpid_hex(dpid_decimal: int) -> str:
    """
    Devuelve un DPID OpenFlow de 64 bits en hexadecimal.
    OpenDaylight lo representará como openflow:<valor_decimal>.
    """
    return f"{dpid_decimal:016x}"


def build_fattree_spec(k: int = 4) -> dict[str, Any]:
    """
    Construye una especificación determinista de una topología Fat-Tree k-aria.

    Convenciones para k=4:
      - Core: c1..c4        -> openflow:1..openflow:4
      - Aggregation: a1..a8 -> openflow:5..openflow:12
      - Edge: e1..e8        -> openflow:13..openflow:20
      - Hosts: h1..h16
      - IPs: 10.0.0.1/24 .. 10.0.0.16/24

    Política de puertos:
      - Edge:
          puertos 1..k/2       -> hosts
          puertos k/2+1..k     -> aggregation
      - Aggregation:
          puertos 1..k/2       -> edge
          puertos k/2+1..k     -> core
      - Core:
          puertos 1..k         -> pods
    """
    if k < 4 or k % 2 != 0:
        raise ValueError("Fat-Tree requiere un valor par de k y k >= 4.")

    half = k // 2
    core_count = half * half
    aggregation_count = k * half
    edge_count = k * half

    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    hostmap: list[dict[str, Any]] = []

    # ---------- Core switches ----------
    # c1..cN -> DPID 1..core_count
    for group in range(half):
        for pos in range(half):
            idx = group * half + pos + 1
            dpid_decimal = idx

            nodes.append(
                {
                    "name": f"c{idx}",
                    "type": "switch",
                    "layer": "core",
                    "pod": "",
                    "index": idx,
                    "ip": "",
                    "mac": "",
                    "dpid_decimal": dpid_decimal,
                    "dpid_hex": _dpid_hex(dpid_decimal),
                    "odl_node": f"openflow:{dpid_decimal}",
                    "core_group": group,
                    "core_pos": pos,
                }
            )

    # ---------- Pods: aggregation, edge, hosts ----------
    host_id = 1

    for pod in range(k):
        # Aggregation switches
        # a1..aN -> DPID core_count+1 .. core_count+aggregation_count
        for agg_local in range(half):
            agg_id = pod * half + agg_local + 1
            dpid_decimal = core_count + agg_id

            nodes.append(
                {
                    "name": f"a{agg_id}",
                    "type": "switch",
                    "layer": "aggregation",
                    "pod": pod,
                    "index": agg_id,
                    "ip": "",
                    "mac": "",
                    "dpid_decimal": dpid_decimal,
                    "dpid_hex": _dpid_hex(dpid_decimal),
                    "odl_node": f"openflow:{dpid_decimal}",
                    "local_index": agg_local,
                }
            )

        # Edge switches
        # e1..eN -> DPID core_count+aggregation_count+1 ..
        for edge_local in range(half):
            edge_id = pod * half + edge_local + 1
            edge_name = f"e{edge_id}"
            dpid_decimal = core_count + aggregation_count + edge_id

            nodes.append(
                {
                    "name": edge_name,
                    "type": "switch",
                    "layer": "edge",
                    "pod": pod,
                    "index": edge_id,
                    "ip": "",
                    "mac": "",
                    "dpid_decimal": dpid_decimal,
                    "dpid_hex": _dpid_hex(dpid_decimal),
                    "odl_node": f"openflow:{dpid_decimal}",
                    "local_index": edge_local,
                }
            )

            # Hosts connected to each edge switch
            for host_local in range(half):
                host_name = f"h{host_id}"
                ip = f"10.0.0.{host_id}/24"
                mac = _host_mac(host_id)
                edge_port = host_local + 1

                nodes.append(
                    {
                        "name": host_name,
                        "type": "host",
                        "layer": "host",
                        "pod": pod,
                        "index": host_id,
                        "ip": ip,
                        "mac": mac,
                        "edge": edge_name,
                    }
                )

                links.append(
                    {
                        "src": edge_name,
                        "src_type": "switch",
                        "src_port": edge_port,
                        "dst": host_name,
                        "dst_type": "host",
                        "dst_port": 1,
                        "link_type": "host-edge",
                        "pod": pod,
                    }
                )

                hostmap.append(
                    {
                        "host": host_name,
                        "ip": ip,
                        "mac": mac,
                        "pod": pod,
                        "edge": edge_name,
                        "edge_port": edge_port,
                    }
                )

                host_id += 1

        # Edge-Aggregation links inside the pod
        for edge_local in range(half):
            edge_id = pod * half + edge_local + 1
            edge_name = f"e{edge_id}"

            for agg_local in range(half):
                agg_id = pod * half + agg_local + 1
                agg_name = f"a{agg_id}"

                links.append(
                    {
                        "src": edge_name,
                        "src_type": "switch",
                        "src_port": half + agg_local + 1,
                        "dst": agg_name,
                        "dst_type": "switch",
                        "dst_port": edge_local + 1,
                        "link_type": "edge-aggregation",
                        "pod": pod,
                    }
                )

        # Aggregation-Core links
        for agg_local in range(half):
            agg_id = pod * half + agg_local + 1
            agg_name = f"a{agg_id}"

            for core_pos in range(half):
                core_id = agg_local * half + core_pos + 1
                core_name = f"c{core_id}"

                links.append(
                    {
                        "src": agg_name,
                        "src_type": "switch",
                        "src_port": half + core_pos + 1,
                        "dst": core_name,
                        "dst_type": "switch",
                        "dst_port": pod + 1,
                        "link_type": "aggregation-core",
                        "pod": pod,
                    }
                )

    manifest = {
        "topology": "fattree",
        "k": k,
        "pods": k,
        "half_k": half,
        "core_switches": core_count,
        "aggregation_switches": aggregation_count,
        "edge_switches": edge_count,
        "hosts": k**3 // 4,
        "switches_total": core_count + aggregation_count + edge_count,
        "links_host_edge": k**3 // 4,
        "links_edge_aggregation": k**3 // 4,
        "links_aggregation_core": k**3 // 4,
        "links_total": 3 * (k**3 // 4),
        "n_nodes": len(nodes),
        "n_links": len(links),
        "n_hostmap": len(hostmap),
        "addressing": "10.0.0.X/24",
        "dpid_policy": {
            "core": f"1..{core_count}",
            "aggregation": f"{core_count + 1}..{core_count + aggregation_count}",
            "edge": f"{core_count + aggregation_count + 1}..{core_count + aggregation_count + edge_count}",
        },
        "switch_naming": {
            "core": "c1..cN",
            "aggregation": "a1..aN",
            "edge": "e1..eN",
            "hosts": "h1..hN",
        },
    }

    return {
        "manifest": manifest,
        "nodes": nodes,
        "links": links,
        "hostmap": hostmap,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """
    Escribe una lista de diccionarios en CSV aunque las filas no tengan
    exactamente las mismas claves. Esto es necesario porque los nodos core,
    aggregation, edge y host tienen algunos metadatos específicos.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        raise ValueError(f"No hay filas para escribir en {path}")

    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
            restval="",
        )
        w.writeheader()
        w.writerows(rows)


def build_ports_table(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for link in links:
        rows.append(
            {
                "node": link["src"],
                "port": link["src_port"],
                "peer": link["dst"],
                "peer_port": link["dst_port"],
                "link_type": link["link_type"],
                "pod": link["pod"],
            }
        )
        rows.append(
            {
                "node": link["dst"],
                "port": link["dst_port"],
                "peer": link["src"],
                "peer_port": link["src_port"],
                "link_type": link["link_type"],
                "pod": link["pod"],
            }
        )

    rows.sort(key=lambda r: (str(r["node"]), int(r["port"])))
    return rows


def export_spec(k: int, outdir: Path) -> None:
    spec = build_fattree_spec(k)
    outdir.mkdir(parents=True, exist_ok=True)

    manifest_path = outdir / f"ch06_fattree_k{k}_manifest.json"
    nodes_path = outdir / f"ch06_fattree_k{k}_nodes.csv"
    links_path = outdir / f"ch06_fattree_k{k}_links.csv"
    hostmap_path = outdir / f"ch06_fattree_k{k}_hostmap.csv"
    ports_path = outdir / f"ch06_fattree_k{k}_ports.csv"

    manifest_path.write_text(
        json.dumps(spec["manifest"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_csv(nodes_path, spec["nodes"])
    write_csv(links_path, spec["links"])
    write_csv(hostmap_path, spec["hostmap"])
    write_csv(ports_path, build_ports_table(spec["links"]))

    print("DONE: Fat-Tree metadata exported.")
    print(f"MANIFEST: {manifest_path}")
    print(f"NODES:    {nodes_path}")
    print(f"LINKS:    {links_path}")
    print(f"HOSTMAP:  {hostmap_path}")
    print(f"PORTS:    {ports_path}")


class FatTreeTopo(Topo):
    def __init__(
        self,
        k: int = 4,
        bw: float = 1000,
        delay: str = "0ms",
        loss: float = 0,
        **opts: Any,
    ) -> None:
        if Topo is object:
            raise RuntimeError("Mininet is required to instantiate FatTreeTopo; metadata export remains available without it.")
        super().__init__(**opts)

        k = _as_int(k)
        bw = _as_float(bw)
        loss = _as_float(loss)

        spec = build_fattree_spec(k)

        link_opts: dict[str, Any] = {}
        if bw > 0:
            link_opts["bw"] = bw
        if delay:
            link_opts["delay"] = str(delay)
        if loss > 0:
            link_opts["loss"] = loss

        # Add nodes
        for node in spec["nodes"]:
            name = node["name"]

            if node["type"] == "switch":
                self.addSwitch(
                    name,
                    protocols="OpenFlow13",
                    dpid=node["dpid_hex"],
                )
            else:
                self.addHost(name, ip=node["ip"], mac=node["mac"])

        # Add deterministic links
        for link in spec["links"]:
            self.addLink(
                link["src"],
                link["dst"],
                port1=int(link["src_port"]),
                port2=int(link["dst_port"]),
                **link_opts,
            )


topos = {
    "fattree": lambda **kwargs: FatTreeTopo(**kwargs),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generador/exportador de topología Fat-Tree k-aria para el TFG."
    )
    p.add_argument("--k", type=int, default=4, help="Valor k de la topología Fat-Tree.")
    p.add_argument(
        "--outdir",
        default="data/ch06_fat_tree",
        help="Directorio de salida para manifest y CSV.",
    )
    p.add_argument(
        "--export",
        action="store_true",
        help="Exporta metadatos JSON/CSV sin arrancar Mininet.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.export:
        export_spec(args.k, Path(args.outdir))
    else:
        spec = build_fattree_spec(args.k)
        print(json.dumps(spec["manifest"], indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
