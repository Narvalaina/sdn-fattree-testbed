from __future__ import annotations

from typing import Any, Dict


def _apply_output(output_connector: str, max_length: int = 0) -> Dict[str, Any]:
    return {
        "instruction": [
            {
                "order": 0,
                "apply-actions": {
                    "action": [
                        {
                            "order": 0,
                            "output-action": {
                                "output-node-connector": str(output_connector),
                                "max-length": int(max_length),
                            },
                        }
                    ]
                },
            }
        ]
    }


def flow_arp_normal(flow_id: str = "arp_normal", table_id: int = 0, priority: int = 300) -> Dict[str, Any]:
    return {
        "id": flow_id,
        "table_id": int(table_id),
        "priority": int(priority),
        "cookie": "0",
        "cookie_mask": "0",
        "idle-timeout": 0,
        "hard-timeout": 0,
        "match": {
            "ethernet-match": {
                "ethernet-type": {"type": 2054}  # 0x0806 ARP
            }
        },
        "instructions": _apply_output("NORMAL", max_length=0),
    }


def flow_table_miss_controller(flow_id: str = "table_miss", table_id: int = 0, priority: int = 0) -> Dict[str, Any]:
    # Table-miss: send to controller
    return {
        "id": flow_id,
        "table_id": int(table_id),
        "priority": int(priority),
        "cookie": "0",
        "cookie_mask": "0",
        "idle-timeout": 0,
        "hard-timeout": 0,
        "instructions": _apply_output("CONTROLLER", max_length=65535),
    }


def flow_ipv4_dst_to_port(flow_id: str, ipv4_dst_cidr: str, out_port: int, table_id: int = 0, priority: int = 200) -> Dict[str, Any]:
    return {
        "id": flow_id,
        "table_id": int(table_id),
        "priority": int(priority),
        "cookie": "0",
        "cookie_mask": "0",
        "idle-timeout": 0,
        "hard-timeout": 0,
        "match": {
            "ethernet-match": {
                "ethernet-type": {"type": 2048}  # 0x0800 IPv4
            },
            "ipv4-destination": str(ipv4_dst_cidr),
        },
        "instructions": _apply_output(str(out_port), max_length=0),
    }
