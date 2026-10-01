from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class CmdResult:
    code: int
    out: str
    err: str


def run(cmd: List[str], timeout_s: int = 10) -> CmdResult:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    return CmdResult(p.returncode, p.stdout.strip(), p.stderr.strip())


def run_sudo_n(cmd: List[str], timeout_s: int = 10) -> CmdResult:
    """
    Non-interactive sudo. Requires 'sudo -v' beforehand.
    """
    return run(["sudo", "-n"] + cmd, timeout_s=timeout_s)


class OvsCtl:
    def list_bridges(self) -> List[str]:
        r = run_sudo_n(["ovs-vsctl", "list-br"])
        if r.code != 0:
            return []
        return [x for x in r.out.splitlines() if x.strip()]

    def get_fail_mode(self, bridge: str) -> Optional[str]:
        r = run_sudo_n(["ovs-vsctl", "get-fail-mode", bridge])
        if r.code != 0:
            return None
        v = r.out.strip().strip('"')
        if not v or v == "[]":
            return None
        return v

    def get_protocols(self, bridge: str) -> str:
        r = run_sudo_n(["ovs-vsctl", "get", "bridge", bridge, "protocols"])
        return r.out.strip()

    def get_controller(self, bridge: str) -> str:
        r = run_sudo_n(["ovs-vsctl", "get-controller", bridge])
        return r.out.strip()

    def show(self) -> str:
        r = run_sudo_n(["ovs-vsctl", "show"])
        return r.out

    def controller_is_connected(self, bridge: str, target: str) -> Optional[bool]:
        """
        Parses 'ovs-vsctl show' to find Controller "<target>" and 'is_connected: true' under the bridge.
        Returns None if not found.
        """
        text = self.show()
        if not text:
            return None

        lines = text.splitlines()
        in_bridge = False
        saw_target = False

        for line in lines:
            s = line.strip()
            if s.startswith("Bridge "):
                in_bridge = (f'"{bridge}"' in s) or (bridge in s)
                saw_target = False
                continue

            if not in_bridge:
                continue

            if s.startswith("Controller "):
                saw_target = (f'"{target}"' in s) or (target in s)
                continue

            if saw_target and s.startswith("is_connected:"):
                val = s.split(":", 1)[1].strip().lower()
                return val == "true"

        return None

    def dump_flows(self, bridge: str) -> str:
        r = run_sudo_n(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", bridge], timeout_s=15)
        return r.out

    @staticmethod
    def n_packets(flow_line: str) -> Optional[int]:
        m = re.search(r"n_packets=(\d+)", flow_line)
        return int(m.group(1)) if m else None
