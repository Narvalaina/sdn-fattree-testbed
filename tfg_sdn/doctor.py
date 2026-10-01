from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _run(cmd: List[str]) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError as exc:
        # Doctor is best-effort: missing system tools are reported instead of aborting the snapshot.
        return 127, "", str(exc)


def _which(bin_name: str) -> Optional[str]:
    return shutil.which(bin_name)


def _read_first(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _utc_iso() -> str:
    # local time with tz offset is fine; we store ISO with offset
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _repo_root() -> Path:
    # tfg_sdn/doctor.py -> repo_root is 2 levels up
    return Path(__file__).resolve().parents[1]


def _safe_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except Exception:
        return None


def _get_cpuinfo() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    code, lscpu, _ = _run(["lscpu"])
    if code == 0 and lscpu:
        # parse some common keys
        def grab(key: str) -> Optional[str]:
            m = re.search(rf"^{re.escape(key)}:\s*(.+)$", lscpu, flags=re.MULTILINE)
            return m.group(1).strip() if m else None

        out["model_name"] = grab("Model name")
        out["architecture"] = grab("Architecture")
        out["cpu_sockets"] = _safe_int(grab("Socket(s)") or "")
        out["cores_per_socket"] = _safe_int(grab("Core(s) per socket") or "")
        out["threads_per_core"] = _safe_int(grab("Thread(s) per core") or "")
        out["cpu_mhz"] = grab("CPU MHz")
    return out


def _get_meminfo() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    code, txt, _ = _run(["free", "-h"])
    if code == 0:
        out["free_h"] = txt
    # also store MemTotal from /proc/meminfo
    meminfo = _read_first(Path("/proc/meminfo"))
    m = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, flags=re.MULTILINE)
    if m:
        out["mem_total_kb"] = int(m.group(1))
    return out


def _get_osinfo() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    out["platform"] = platform.platform()
    out["python"] = platform.python_version()
    out["kernel"] = platform.release()
    out["machine"] = platform.machine()
    out["uname"] = " ".join(platform.uname())
    # /etc/os-release
    osr = _read_first(Path("/etc/os-release"))
    if osr:
        out["os_release_raw"] = osr
        for key in ["PRETTY_NAME", "NAME", "VERSION", "VERSION_ID", "UBUNTU_CODENAME"]:
            m = re.search(rf'^{key}=(.*)$', osr, flags=re.MULTILINE)
            if m:
                out[key.lower()] = m.group(1).strip().strip('"')
    return out


def _get_cmd_version(cmd: List[str]) -> Optional[str]:
    code, out, err = _run(cmd)
    if code == 0 and out:
        return out.splitlines()[0].strip()
    if err:
        return err.splitlines()[0].strip()
    return None


def _get_java() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    java = _which("java")
    out["java_path"] = java
    out["JAVA_HOME"] = os.getenv("JAVA_HOME")
    v = _get_cmd_version(["java", "-version"])
    # java -version writes to stderr in many builds -> handled by _get_cmd_version
    out["java_version"] = v
    return out


def _get_odl() -> Dict[str, Any]:
    """
    Best-effort: we cannot introspect Karaf reliably without path.
    We store environment hints if present.
    """
    out: Dict[str, Any] = {}
    out["ODL_HOST"] = os.getenv("ODL_HOST", "127.0.0.1")
    out["ODL_REST_PORT"] = os.getenv("ODL_REST_PORT", "8181")
    out["ODL_OF_PORT"] = os.getenv("ODL_OF_PORT", "6653")
    out["ODL_USER"] = os.getenv("ODL_USER", "admin")
    # don't store password
    karaf_hint = os.getenv("ODL_KARAF_DIR") or ""
    out["ODL_KARAF_DIR_hint"] = karaf_hint
    return out


def _get_ovs_mininet() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    out["ovs_vsctl"] = _which("ovs-vsctl")
    out["ovs_ofctl"] = _which("ovs-ofctl")
    out["mn"] = _which("mn")

    out["ovs_vsctl_version"] = _get_cmd_version(["ovs-vsctl", "--version"])
    out["ovs_ofctl_version"] = _get_cmd_version(["ovs-ofctl", "--version"])
    out["mininet_version"] = _get_cmd_version(["mn", "--version"])
    return out


def _get_python_lib_versions() -> Dict[str, Any]:
    libs = ["matplotlib", "numpy", "pandas", "requests"]
    out: Dict[str, Any] = {}
    for lib in libs:
        try:
            mod = __import__(lib)
            out[lib] = getattr(mod, "__version__", "unknown")
        except Exception:
            out[lib] = None
    return out


def _get_git() -> Dict[str, Any]:
    out: Dict[str, Any] = {"git": _which("git")}
    if not out["git"]:
        return out
    root = _repo_root()
    if (root / ".git").exists():
        out["repo_root"] = str(root)
        out["is_git_repo"] = True
        out["git_status"] = _run(["git", "-C", str(root), "status", "--porcelain"])[1]
        out["git_head"] = _run(["git", "-C", str(root), "rev-parse", "HEAD"])[1]
        out["git_branch"] = _run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"])[1]
    else:
        out["repo_root"] = str(root)
        out["is_git_repo"] = False
    return out


def _get_network() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    out["ip_addr"] = _run(["ip", "-brief", "addr"])[1]
    out["ip_route"] = _run(["ip", "route"])[1]
    return out


def _get_odl_listening() -> Dict[str, Any]:
    """
    Best-effort check if ports are listening. Requires ss.
    """
    out: Dict[str, Any] = {}
    code, txt, _ = _run(["ss", "-lntp"])
    if code != 0:
        return out
    out["ss_listen"] = txt
    return out


@dataclass
class DoctorReport:
    ts: str
    repo_root: str
    user: str
    cwd: str
    os: Dict[str, Any]
    cpu: Dict[str, Any]
    mem: Dict[str, Any]
    java: Dict[str, Any]
    odl: Dict[str, Any]
    ovs_mininet: Dict[str, Any]
    python_libs: Dict[str, Any]
    git: Dict[str, Any]
    net: Dict[str, Any]
    listen: Dict[str, Any]


def generate_doctor_report() -> DoctorReport:
    root = _repo_root()
    return DoctorReport(
        ts=_utc_iso(),
        repo_root=str(root),
        user=os.getenv("USER", ""),
        cwd=str(Path.cwd()),
        os=_get_osinfo(),
        cpu=_get_cpuinfo(),
        mem=_get_meminfo(),
        java=_get_java(),
        odl=_get_odl(),
        ovs_mininet=_get_ovs_mininet(),
        python_libs=_get_python_lib_versions(),
        git=_get_git(),
        net=_get_network(),
        listen=_get_odl_listening(),
    )


def write_doctor_snapshot(out_dir: Path) -> Path:
    """
    Writes doctor.json and doctor.txt into out_dir, returns path to doctor.json.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = generate_doctor_report()
    jpath = out_dir / "doctor.json"
    tpath = out_dir / "doctor.txt"

    jpath.write_text(json.dumps(asdict(rep), indent=2, sort_keys=True), encoding="utf-8")

    # human-readable txt summary
    lines: List[str] = []
    lines.append(f"ts: {rep.ts}")
    lines.append(f"repo_root: {rep.repo_root}")
    lines.append(f"user: {rep.user}")
    lines.append("")
    lines.append("[OS]")
    lines.append(rep.os.get("pretty_name", rep.os.get("platform", "")) or "")
    lines.append(f"kernel: {rep.os.get('kernel', '')}")
    lines.append(f"python: {rep.os.get('python', '')}")
    lines.append("")
    lines.append("[CPU]")
    for k, v in rep.cpu.items():
        lines.append(f"{k}: {v}")
    lines.append("")
    lines.append("[MEM]")
    lines.append(rep.mem.get("free_h", ""))
    lines.append("")
    lines.append("[OVS/MININET]")
    for k in ["ovs_vsctl_version", "ovs_ofctl_version", "mininet_version"]:
        lines.append(f"{k}: {rep.ovs_mininet.get(k)}")
    lines.append("")
    lines.append("[JAVA/ODL]")
    lines.append(f"JAVA_HOME: {rep.java.get('JAVA_HOME')}")
    lines.append(f"java_path: {rep.java.get('java_path')}")
    lines.append(f"java_version: {rep.java.get('java_version')}")
    lines.append(f"ODL_HOST: {rep.odl.get('ODL_HOST')}")
    lines.append(f"ODL_REST_PORT: {rep.odl.get('ODL_REST_PORT')}")
    lines.append(f"ODL_OF_PORT: {rep.odl.get('ODL_OF_PORT')}")
    lines.append("")
    lines.append("[PYTHON LIBS]")
    for k, v in rep.python_libs.items():
        lines.append(f"{k}: {v}")
    lines.append("")
    lines.append("[GIT]")
    for k in ["is_git_repo", "git_branch", "git_head"]:
        lines.append(f"{k}: {rep.git.get(k)}")
    if rep.git.get("git_status") is not None:
        lines.append("git_status (porcelain):")
        lines.append(rep.git.get("git_status") or "")
    lines.append("")
    lines.append("[NETWORK]")
    lines.append(rep.net.get("ip_addr", ""))
    lines.append("")
    lines.append("ip route:")
    lines.append(rep.net.get("ip_route", ""))

    tpath.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return jpath
