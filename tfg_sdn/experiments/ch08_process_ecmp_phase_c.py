#!/usr/bin/env python3
"""
Procesado final de la Fase C del Capítulo 8: demostrador ECMP limitado.

Ruta recomendada:
    tfg_sdn/experiments/ch08_process_ecmp_phase_c.py

Objetivo:
    Consolidar las evidencias de la Fase C:
      - C.1: single-path y ECMP limitado instalado directamente en OVS con ovs-ofctl.
      - C.2: ECMP limitado instalado mediante OpenDaylight RESTCONF.

    Genera CSV procesados, figuras homogéneas PNG/SVG/PDF, tablas LaTeX,
    listings LaTeX y un resumen Markdown para preparar la redacción final.

Alcance metodológico:
    - No demuestra ECMP completo en toda la Fat-Tree.
    - No demuestra balanceo dinámico global.
    - Documenta un demostrador limitado h1 <-> h16 con dos ramas equivalentes.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


DEFAULT_C1_ID = "ch08_fattree_ecmp_limited_20260520_0812"
DEFAULT_C2_ID = "ch08_fattree_ecmp_restconf_groups_20260520_1009"
DEFAULT_OUTPUT_ID = "ch08_fattree_ecmp_phase_c_20260520"

FIGURE_DPI = 180


# ---------------------------------------------------------------------------
# Rutas y utilidades
# ---------------------------------------------------------------------------


def default_tfg_root() -> Path:
    return Path(
        os.environ.get(
            "TFG_ROOT",
            str(Path(__file__).resolve().parents[2]),
        )
    ).expanduser()


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False), encoding="utf-8")


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: List[str] = []
        for row in rows:
            for key in row:
                if key not in keys:
                    keys.append(key)
        fieldnames = keys
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, "", "None", "nan", "NaN"):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def fmt_num(value: Any, decimals: int = 2, missing: str = "--") -> str:
    number = as_float(value)
    if number is None:
        return missing
    return f"{number:.{decimals}f}".replace(".", ",")


def tick_es(value: float, _pos=None) -> str:
    """
    Formateador de ejes para figuras del documento en castellano.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number))}"
    return f"{number:g}".replace(".", ",")


def apply_spanish_yaxis(ax: Any) -> None:
    from matplotlib.ticker import FuncFormatter
    ax.yaxis.set_major_formatter(FuncFormatter(tick_es))


def latex_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def safe_status(value: Any) -> str:
    return str(value or "CHECK_REQUIRED")


class Paths:
    def __init__(self, tfg_root: Path, c1_id: str, c2_id: str, output_id: str) -> None:
        self.tfg_root = tfg_root
        self.c1_id = c1_id
        self.c2_id = c2_id
        self.output_id = output_id

        self.c1_root = tfg_root / "runs" / "ch08_fattree" / c1_id
        self.c2_root = tfg_root / "runs" / "ch08_fattree" / c2_id

        self.processed = tfg_root / "data" / "processed" / "ch08_fattree" / output_id
        self.figures = tfg_root / "figures" / "ch08_resultados" / output_id
        self.tables = tfg_root / "tables" / "ch08" / output_id
        self.listings = tfg_root / "listings" / "ch08" / output_id
        self.docs = tfg_root / "docs" / "ch08_resultados" / output_id

    def ensure(self) -> None:
        for path in [self.processed, self.figures, self.tables, self.listings, self.docs]:
            path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Carga y normalización de resultados
# ---------------------------------------------------------------------------


def load_inputs(paths: Paths) -> Dict[str, Any]:
    c1_single = read_json(paths.c1_root / "c1_single_path" / "single_summary.json")
    c1_select = read_json(paths.c1_root / "c2_select_group" / "select_summary.json")

    c2_groups = read_json(paths.c2_root / "c2_install_groups_restconf" / "install_groups_restconf_summary.json")
    c2_verify = read_json(paths.c2_root / "c2_verify_groups" / "verify_groups_summary.json")
    c2_flows = read_json(paths.c2_root / "c2_2_install_flows_restconf" / "install_flows_restconf_summary.json")
    c2_traffic = read_json(paths.c2_root / "c2_2_traffic_restconf" / "c2_2_restconf_traffic_summary.json")

    return {
        "c1_single": c1_single,
        "c1_select": c1_select,
        "c2_groups": c2_groups,
        "c2_verify": c2_verify,
        "c2_flows": c2_flows,
        "c2_traffic": c2_traffic,
    }


def normalized_modes(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    c1_single = data.get("c1_single", {})
    c1_select = data.get("c1_select", {})
    c2_traffic = data.get("c2_traffic", {})

    return [
        {
            "mode_id": "single_path_ovs",
            "mode_label": "Single-path",
            "installation": "ovs-ofctl",
            "status": safe_status(c1_single.get("status")),
            "packet_loss_pct": c1_single.get("packet_loss_pct"),
            "rtt_avg_ms": c1_single.get("rtt_avg_ms"),
            "n_iperf_ok": c1_single.get("n_iperf_ok"),
            "n_iperf_total": c1_single.get("n_iperf_total"),
            "throughput_mean_gbps": c1_single.get("throughput_mean_gbps"),
            "throughput_min_gbps": c1_single.get("throughput_min_gbps"),
            "throughput_max_gbps": c1_single.get("throughput_max_gbps"),
            "branch_1_share_pct": c1_single.get("forward_A_share_pct"),
            "branch_2_share_pct": c1_single.get("forward_B_share_pct"),
            "evidence": "c1_single_path/single_summary.json",
            "interpretation": "baseline con una única rama activa",
        },
        {
            "mode_id": "ecmp_limited_ovs",
            "mode_label": "ECMP limitado",
            "installation": "ovs-ofctl",
            "status": safe_status(c1_select.get("status")),
            "packet_loss_pct": c1_select.get("packet_loss_pct"),
            "rtt_avg_ms": c1_select.get("rtt_avg_ms"),
            "n_iperf_ok": c1_select.get("n_iperf_ok"),
            "n_iperf_total": c1_select.get("n_iperf_total"),
            "throughput_mean_gbps": c1_select.get("throughput_mean_gbps"),
            "throughput_min_gbps": c1_select.get("throughput_min_gbps"),
            "throughput_max_gbps": c1_select.get("throughput_max_gbps"),
            "branch_1_share_pct": c1_select.get("forward_A_share_pct"),
            "branch_2_share_pct": c1_select.get("forward_B_share_pct"),
            "evidence": "c2_select_group/select_summary.json",
            "interpretation": "dos ramas activas con programación directa en OVS",
        },
        {
            "mode_id": "ecmp_limited_restconf",
            "mode_label": "ECMP limitado",
            "installation": "RESTCONF",
            "status": safe_status(c2_traffic.get("status")),
            "packet_loss_pct": c2_traffic.get("packet_loss_pct"),
            "rtt_avg_ms": c2_traffic.get("rtt_avg_ms"),
            "n_iperf_ok": c2_traffic.get("n_iperf_ok"),
            "n_iperf_total": c2_traffic.get("n_iperf_total"),
            "throughput_mean_gbps": c2_traffic.get("throughput_mean_gbps"),
            "throughput_min_gbps": None,
            "throughput_max_gbps": None,
            "branch_1_share_pct": c2_traffic.get("forward_1_share_pct"),
            "branch_2_share_pct": c2_traffic.get("forward_2_share_pct"),
            "reverse_branch_1_share_pct": c2_traffic.get("reverse_1_share_pct"),
            "reverse_branch_2_share_pct": c2_traffic.get("reverse_2_share_pct"),
            "evidence": "c2_2_traffic_restconf/c2_2_restconf_traffic_summary.json",
            "interpretation": "dos ramas activas con grupos y flows instalados mediante ODL RESTCONF",
        },
    ]


def build_status_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    c2_groups = data.get("c2_groups", {})
    c2_verify = data.get("c2_verify", {})
    c2_flows = data.get("c2_flows", {})
    c2_traffic = data.get("c2_traffic", {})

    return [
        {
            "block": "C.1",
            "description": "Baseline single-path h1↔h16",
            "installation": "ovs-ofctl",
            "status": safe_status(data.get("c1_single", {}).get("status")),
            "main_evidence": "runs/.../c1_single_path/single_summary.json",
            "note": "camino único de referencia",
        },
        {
            "block": "C.1",
            "description": "ECMP limitado h1↔h16",
            "installation": "ovs-ofctl",
            "status": safe_status(data.get("c1_select", {}).get("status")),
            "main_evidence": "runs/.../c2_select_group/select_summary.json",
            "note": "grupo select instalado directamente en OVS",
        },
        {
            "block": "C.2.1",
            "description": "Grupos OpenFlow select",
            "installation": "ODL RESTCONF",
            "status": safe_status(c2_groups.get("status")),
            "main_evidence": "runs/.../c2_install_groups_restconf/install_groups_restconf_summary.json",
            "note": "RPC json_input y datastore ns_group",
        },
        {
            "block": "C.2.1",
            "description": "Verificación de grupos",
            "installation": "ODL RESTCONF + OVS",
            "status": safe_status(c2_verify.get("status")),
            "main_evidence": "runs/.../c2_verify_groups/verify_groups_summary.json",
            "note": "visibles en OVS y en inventario operacional ODL",
        },
        {
            "block": "C.2.2",
            "description": "Flows asociados al ECMP limitado",
            "installation": "ODL RESTCONF datastore PUT",
            "status": safe_status(c2_flows.get("status")),
            "main_evidence": "runs/.../c2_2_install_flows_restconf/install_flows_restconf_summary.json",
            "note": f"{c2_flows.get('n_ok', '--')}/{c2_flows.get('n_flows', '--')} flows verificados",
        },
        {
            "block": "C.2.3",
            "description": "Tráfico h1↔h16",
            "installation": "ODL RESTCONF",
            "status": safe_status(c2_traffic.get("status")),
            "main_evidence": "runs/.../c2_2_traffic_restconf/c2_2_restconf_traffic_summary.json",
            "note": f"iperf3 {c2_traffic.get('n_iperf_ok', '--')}/{c2_traffic.get('n_iperf_total', '--')}",
        },
    ]


def build_restconf_installation_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    c2_groups = data.get("c2_groups", {})
    c2_flows = data.get("c2_flows", {})
    group_results = c2_groups.get("results", {}) if isinstance(c2_groups.get("results", {}), dict) else {}

    rows: List[Dict[str, Any]] = []
    for bridge in ["e1", "e8"]:
        result = group_results.get(bridge, {})
        rows.append(
            {
                "element": f"Grupo select {bridge}",
                "switches": bridge,
                "method": "sal-group:add-group + datastore",
                "status": safe_status(result.get("status")),
                "detail": f"RPC={result.get('rpc_variant', result.get('winning_variant', '--'))}; DS={result.get('datastore_variant', '--')}",
            }
        )

    rows.append(
        {
            "element": "Flows forwarding",
            "switches": "e1,e8,a1,a2,c1,c3,a7,a8",
            "method": "RESTCONF datastore PUT",
            "status": safe_status(c2_flows.get("status")),
            "detail": f"{c2_flows.get('n_ok', '--')}/{c2_flows.get('n_flows', '--')} flows",
        }
    )

    return rows


def build_branch_rows(modes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for mode in modes:
        rows.append(
            {
                "mode": mode["mode_label"],
                "installation": mode["installation"],
                "direction": "h1→h16",
                "branch_1_pct": mode.get("branch_1_share_pct"),
                "branch_2_pct": mode.get("branch_2_share_pct"),
                "criterion": "dos ramas activas"
                if (as_float(mode.get("branch_1_share_pct"), 0.0) or 0.0) > 0 and (as_float(mode.get("branch_2_share_pct"), 0.0) or 0.0) > 0
                else "una rama activa",
            }
        )
    restconf = next((m for m in modes if m["mode_id"] == "ecmp_limited_restconf"), None)
    if restconf:
        rows.append(
            {
                "mode": restconf["mode_label"],
                "installation": restconf["installation"],
                "direction": "h16→h1",
                "branch_1_pct": restconf.get("reverse_branch_1_share_pct"),
                "branch_2_pct": restconf.get("reverse_branch_2_share_pct"),
                "criterion": "dos ramas activas",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Figuras
# ---------------------------------------------------------------------------


def import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt
    except ImportError:
        return None


def save_fig(plt: Any, fig: Any, out_base: Path) -> List[str]:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    generated = []
    for suffix in [".png", ".svg", ".pdf"]:
        path = out_base.with_suffix(suffix)
        fig.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
        generated.append(str(path))
    plt.close(fig)
    return generated


def style_ax(ax: Any) -> None:
    ax.grid(True, axis="y", alpha=0.28)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", labelsize=11)


def figure_throughput(plt: Any, modes: List[Dict[str, Any]], out_base: Path) -> List[str]:
    labels = [
        "Single-path",
        "ECMP limitado\n(ovs-ofctl)",
        "ECMP limitado\n(RESTCONF)",
    ]
    values = [as_float(mode.get("throughput_mean_gbps"), 0.0) or 0.0 for mode in modes]

    fig = plt.figure(figsize=(10.6, 5.6))
    ax = fig.add_subplot(111)
    bars = ax.bar(labels, values)

    ax.set_title("Throughput medio del demostrador ECMP limitado", fontsize=17, fontweight="bold", pad=16)
    ax.set_ylabel("Throughput medio recibido (Gbps)", fontsize=13)
    apply_spanish_yaxis(ax)
    ax.set_xlabel("Modo de encaminamiento", fontsize=13)
    style_ax(ax)

    ymax = max(values) if values else 1.0
    ax.set_ylim(0, ymax * 1.18 if ymax > 0 else 1.0)

    for bar in bars:
        h = bar.get_height()
        ax.annotate(
            f"{fmt_num(h, 2)} Gbps",
            xy=(bar.get_x() + bar.get_width() / 2.0, h),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=10.5,
            fontweight="bold",
        )

    fig.tight_layout()
    return save_fig(plt, fig, out_base)


def figure_branch_share(plt: Any, modes: List[Dict[str, Any]], out_base: Path) -> List[str]:
    labels = [
        "Single-path",
        "ECMP limitado\n(ovs-ofctl)",
        "ECMP limitado\n(RESTCONF)",
    ]
    branch_1 = [as_float(mode.get("branch_1_share_pct"), 0.0) or 0.0 for mode in modes]
    branch_2 = [as_float(mode.get("branch_2_share_pct"), 0.0) or 0.0 for mode in modes]

    fig = plt.figure(figsize=(11.0, 5.8))
    ax = fig.add_subplot(111)

    x = list(range(len(labels)))
    width = 0.32

    bars_1 = ax.bar([i - width / 2 for i in x], branch_1, width, label="Rama 1")
    bars_2 = ax.bar([i + width / 2 for i in x], branch_2, width, label="Rama 2")

    ax.set_title("Reparto de tráfico entre ramas equivalentes", fontsize=17, fontweight="bold", pad=16)
    ax.set_ylabel("Porcentaje de bytes en sentido h1→h16 (%)", fontsize=13)
    apply_spanish_yaxis(ax)
    ax.set_xlabel("Modo de encaminamiento", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend(loc="upper center", ncol=2, frameon=True)
    style_ax(ax)
    ax.set_ylim(0, 110)

    for bars in [bars_1, bars_2]:
        for bar in bars:
            h = bar.get_height()
            ax.annotate(
                f"{fmt_num(h, 2)} %",
                xy=(bar.get_x() + bar.get_width() / 2.0, h),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=10.5,
                fontweight="bold",
            )

    fig.tight_layout()
    return save_fig(plt, fig, out_base)


# ---------------------------------------------------------------------------
# Tablas LaTeX
# ---------------------------------------------------------------------------


def latex_table(
    caption: str,
    label: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    colspec: str,
    small: bool = True,
) -> str:
    lines: List[str] = []
    lines.append(r"\begin{table}[htbp]")
    lines.append(r"\centering")
    if small:
        lines.append(r"\small")
    lines.append(rf"\caption{{{latex_escape(caption)}}}")
    lines.append(rf"\label{{{label}}}")
    lines.append(rf"\begin{{tabular}}{{{colspec}}}")
    lines.append(r"\hline")
    lines.append(" & ".join(latex_escape(h) for h in headers) + r" \\")
    lines.append(r"\hline")
    for row in rows:
        lines.append(" & ".join(latex_escape(cell) for cell in row) + r" \\")
    lines.append(r"\hline")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    lines.append("")
    return "\n".join(lines)


def generate_tables(paths: Paths, status_rows: List[Dict[str, Any]], modes: List[Dict[str, Any]], branch_rows: List[Dict[str, Any]], restconf_rows: List[Dict[str, Any]]) -> Dict[str, str]:
    generated: Dict[str, str] = {}

    status_tex = latex_table(
        caption="Estado de validación de la Fase C",
        label="tab:ch08_phase_c_status",
        headers=["Bloque", "Descripción", "Instalación", "Resultado", "Observación"],
        rows=[
            [
                row["block"],
                row["description"],
                row["installation"],
                row["status"],
                row["note"],
            ]
            for row in status_rows
        ],
        colspec="p{1.2cm}p{4.1cm}p{3.1cm}p{1.5cm}p{4.4cm}",
    )
    path = paths.tables / "ch08_phase_c_status.tex"
    write_text(path, status_tex)
    generated["status_table"] = str(path)

    metrics_tex = latex_table(
        caption="Métricas comparativas del demostrador ECMP limitado",
        label="tab:ch08_phase_c_metrics",
        headers=["Modo", "Instalación", "Pérdidas", "RTT medio", "iperf3", "Throughput medio"],
        rows=[
            [
                mode["mode_label"],
                mode["installation"],
                f"{fmt_num(mode.get('packet_loss_pct'), 1)} %",
                f"{fmt_num(mode.get('rtt_avg_ms'), 3)} ms",
                f"{mode.get('n_iperf_ok', '--')}/{mode.get('n_iperf_total', '--')}",
                f"{fmt_num(mode.get('throughput_mean_gbps'), 2)} Gbps",
            ]
            for mode in modes
        ],
        colspec="p{2.5cm}p{2.6cm}p{1.5cm}p{1.8cm}p{1.5cm}p{2.6cm}",
    )
    path = paths.tables / "ch08_phase_c_metrics.tex"
    write_text(path, metrics_tex)
    generated["metrics_table"] = str(path)

    branch_tex = latex_table(
        caption="Uso de ramas equivalentes en la Fase C",
        label="tab:ch08_phase_c_branch_usage",
        headers=["Modo", "Instalación", "Sentido", "Rama 1", "Rama 2", "Criterio"],
        rows=[
            [
                row["mode"],
                row["installation"],
                row["direction"],
                f"{fmt_num(row.get('branch_1_pct'), 2)} %",
                f"{fmt_num(row.get('branch_2_pct'), 2)} %",
                row["criterion"],
            ]
            for row in branch_rows
        ],
        colspec="p{2.5cm}p{2.4cm}p{1.8cm}p{1.6cm}p{1.6cm}p{3.2cm}",
    )
    path = paths.tables / "ch08_phase_c_branch_usage.tex"
    write_text(path, branch_tex)
    generated["branch_table"] = str(path)

    restconf_tex = latex_table(
        caption="Instalación RESTCONF de grupos y flujos en la Fase C",
        label="tab:ch08_phase_c_restconf_installation",
        headers=["Elemento", "Switches", "Método", "Resultado", "Detalle"],
        rows=[
            [
                row["element"],
                row["switches"],
                row["method"],
                row["status"],
                row["detail"],
            ]
            for row in restconf_rows
        ],
        colspec="p{2.8cm}p{3.0cm}p{3.5cm}p{1.5cm}p{3.2cm}",
    )
    path = paths.tables / "ch08_phase_c_restconf_installation.tex"
    write_text(path, restconf_tex)
    generated["restconf_table"] = str(path)

    inputs = "\n".join(
        [
            r"% Inserciones sugeridas para la Fase C del Capítulo 8",
            rf"\input{{tables/ch08/{paths.output_id}/ch08_phase_c_status.tex}}",
            rf"\input{{tables/ch08/{paths.output_id}/ch08_phase_c_metrics.tex}}",
            rf"\input{{tables/ch08/{paths.output_id}/ch08_phase_c_branch_usage.tex}}",
            rf"\input{{tables/ch08/{paths.output_id}/ch08_phase_c_restconf_installation.tex}}",
            "",
        ]
    )
    path = paths.tables / "ch08_phase_c_tables_inputs.tex"
    write_text(path, inputs)
    generated["tables_inputs"] = str(path)

    return generated


# ---------------------------------------------------------------------------
# Listings LaTeX
# ---------------------------------------------------------------------------


def lstlisting(caption: str, label: str, content: str, language: str = "bash") -> str:
    return (
        rf"\begin{{lstlisting}}[language={language},caption={{{latex_escape(caption)}}},label={{{label}}}]" + "\n"
        + content.rstrip()
        + "\n"
        + r"\end{lstlisting}"
        + "\n"
    )


def generate_listings(paths: Paths, data: Dict[str, Any]) -> Dict[str, str]:
    generated: Dict[str, str] = {}

    sequence = """python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \\
  --campaign-id "$CAMPAIGN_ID" \\
  --action cleanup-forwarding-ovs

python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \\
  --campaign-id "$CAMPAIGN_ID" \\
  --action install-groups-restconf

python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \\
  --campaign-id "$CAMPAIGN_ID" \\
  --action install-flows-restconf

python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \\
  --campaign-id "$CAMPAIGN_ID" \\
  --action summarize-traffic"""
    path = paths.listings / "ch08_phase_c_operational_sequence.tex"
    write_text(
        path,
        lstlisting(
            "Secuencia de automatización del demostrador ECMP limitado vía RESTCONF",
            "lst:ch08_phase_c_sequence",
            sequence,
            language="bash",
        ),
    )
    generated["operational_sequence"] = str(path)

    c2_groups = data.get("c2_groups", {})
    c2_flows = data.get("c2_flows", {})
    group_results = c2_groups.get("results", {}) if isinstance(c2_groups.get("results", {}), dict) else {}

    install_lines = ["install-groups-restconf: " + safe_status(c2_groups.get("status"))]
    for bridge in ["e1", "e8"]:
        result = group_results.get(bridge, {})
        install_lines.append(
            f"{bridge}: rpc_variant={result.get('rpc_variant', result.get('winning_variant', '--'))} "
            f"datastore_variant={result.get('datastore_variant', '--')} "
            f"ovs_group_present={result.get('ovs_group_present', '--')}"
        )
    install_lines.extend(
        [
            "",
            "install-flows-restconf: " + safe_status(c2_flows.get("status")),
            f"method: {c2_flows.get('method', 'RESTCONF datastore PUT')}",
            f"n_flows: {c2_flows.get('n_flows', '--')}",
            f"n_ok: {c2_flows.get('n_ok', '--')}/{c2_flows.get('n_flows', '--')}",
        ]
    )
    path = paths.listings / "ch08_phase_c_restconf_install_result.tex"
    write_text(
        path,
        lstlisting(
            "Evidencia resumida de instalación RESTCONF de grupos select y flujos",
            "lst:ch08_phase_c_restconf_install",
            "\n".join(install_lines),
            language="bash",
        ),
    )
    generated["restconf_install_result"] = str(path)

    c2_traffic = data.get("c2_traffic", {})
    traffic_lines = [
        f"ping_ok: {c2_traffic.get('ping_ok', '--')}",
        f"packet_loss_pct: {fmt_num(c2_traffic.get('packet_loss_pct'), 1)}",
        f"n_iperf_ok: {c2_traffic.get('n_iperf_ok', '--')}/{c2_traffic.get('n_iperf_total', '--')}",
        f"throughput_mean_gbps: {fmt_num(c2_traffic.get('throughput_mean_gbps'), 2)}",
        f"group_evidence_ok: {c2_traffic.get('group_evidence_ok', '--')}",
        f"branch_evidence_ok: {c2_traffic.get('branch_evidence_ok', '--')}",
        f"forward_1_share_pct: {fmt_num(c2_traffic.get('forward_1_share_pct'), 1)}",
        f"forward_2_share_pct: {fmt_num(c2_traffic.get('forward_2_share_pct'), 1)}",
        f"status: {safe_status(c2_traffic.get('status'))}",
    ]
    path = paths.listings / "ch08_phase_c_traffic_summary.tex"
    write_text(
        path,
        lstlisting(
            "Resumen final del tráfico h1↔h16 sobre ECMP limitado instalado por RESTCONF",
            "lst:ch08_phase_c_traffic_summary",
            "\n".join(traffic_lines),
            language="bash",
        ),
    )
    generated["traffic_summary"] = str(path)

    inputs = "\n".join(
        [
            r"% Inserciones sugeridas para listings de la Fase C del Capítulo 8",
            rf"\input{{listings/ch08/{paths.output_id}/ch08_phase_c_operational_sequence.tex}}",
            rf"\input{{listings/ch08/{paths.output_id}/ch08_phase_c_restconf_install_result.tex}}",
            rf"\input{{listings/ch08/{paths.output_id}/ch08_phase_c_traffic_summary.tex}}",
            "",
        ]
    )
    path = paths.listings / "ch08_phase_c_listings_inputs.tex"
    write_text(path, inputs)
    generated["listings_inputs"] = str(path)

    return generated


# ---------------------------------------------------------------------------
# Resumen Markdown
# ---------------------------------------------------------------------------


def generate_markdown_summary(paths: Paths, modes: List[Dict[str, Any]], status_rows: List[Dict[str, Any]], branch_rows: List[Dict[str, Any]], manifest: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# Procesado Fase C — ECMP limitado")
    lines.append("")
    lines.append(f"- **output_id:** `{paths.output_id}`")
    lines.append(f"- **C.1 campaign:** `{paths.c1_id}`")
    lines.append(f"- **C.2 campaign:** `{paths.c2_id}`")
    lines.append(f"- **status:** `{manifest.get('status')}`")
    lines.append("")
    lines.append("## Lectura metodológica")
    lines.append("")
    lines.append(
        "La Fase C constituye una extensión exploratoria del Capítulo 8. "
        "El objetivo no es demostrar ECMP completo en toda la Fat-Tree, sino validar "
        "un demostrador limitado h1↔h16 con dos ramas equivalentes."
    )
    lines.append("")
    lines.append("## Métricas comparativas")
    lines.append("")
    lines.append("| Modo | Instalación | RTT medio | iperf3 | Throughput medio | Rama 1 | Rama 2 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for mode in modes:
        lines.append(
            f"| {mode['mode_label']} | {mode['installation']} | "
            f"{fmt_num(mode.get('rtt_avg_ms'), 3)} ms | "
            f"{mode.get('n_iperf_ok', '--')}/{mode.get('n_iperf_total', '--')} | "
            f"{fmt_num(mode.get('throughput_mean_gbps'), 2)} Gbps | "
            f"{fmt_num(mode.get('branch_1_share_pct'), 2)} % | "
            f"{fmt_num(mode.get('branch_2_share_pct'), 2)} % |"
        )
    lines.append("")
    lines.append("## Artefactos generados")
    lines.append("")
    lines.append(f"- Figuras: `{paths.figures}`")
    lines.append(f"- Tablas: `{paths.tables}`")
    lines.append(f"- Listings: `{paths.listings}`")
    lines.append(f"- CSV procesados: `{paths.processed}`")
    lines.append("")
    lines.append("## Advertencia de alcance")
    lines.append("")
    lines.append(
        "La denominación ECMP limitado debe interpretarse como un demostrador controlado "
        "basado en grupos OpenFlow select y dos ramas equivalentes entre h1 y h16. "
        "No representa balanceo ECMP completo de todos los pares de la Fat-Tree ni "
        "reconfiguración dinámica."
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def process(args: argparse.Namespace) -> int:
    tfg_root = Path(args.tfg_root).expanduser().resolve()
    paths = Paths(
        tfg_root=tfg_root,
        c1_id=args.c1_campaign_id,
        c2_id=args.c2_campaign_id,
        output_id=args.output_id,
    )
    paths.ensure()

    data = load_inputs(paths)
    modes = normalized_modes(data)
    status_rows = build_status_rows(data)
    branch_rows = build_branch_rows(modes)
    restconf_rows = build_restconf_installation_rows(data)

    # CSV
    write_csv(paths.processed / "ch08_phase_c_status.csv", status_rows)
    write_csv(paths.processed / "ch08_phase_c_metrics.csv", modes)
    write_csv(paths.processed / "ch08_phase_c_branch_usage.csv", branch_rows)
    write_csv(paths.processed / "ch08_phase_c_restconf_installation.csv", restconf_rows)

    # Figuras
    figures: Dict[str, List[str]] = {}
    plt = import_matplotlib()
    if plt is None:
        figures["error"] = ["matplotlib no disponible"]
    else:
        figures["throughput_comparison"] = figure_throughput(
            plt,
            modes,
            paths.figures / "ch08_phase_c_throughput_comparison",
        )
        figures["branch_share"] = figure_branch_share(
            plt,
            modes,
            paths.figures / "ch08_phase_c_branch_share",
        )

    # Tablas y listings
    tables = generate_tables(paths, status_rows, modes, branch_rows, restconf_rows)
    listings = generate_listings(paths, data)

    all_pass = (
        all(row.get("status") == "PASS" for row in status_rows)
        and all(as_float(mode.get("throughput_mean_gbps")) is not None for mode in modes)
        and plt is not None
    )
    final_status = "PASS" if all_pass else "CHECK_REQUIRED"

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_id": paths.output_id,
        "status": final_status,
        "scope": "Phase C ECMP-limited evidence processing for Chapter 8",
        "c1_campaign_id": paths.c1_id,
        "c2_campaign_id": paths.c2_id,
        "processed_dir": str(paths.processed),
        "figures_dir": str(paths.figures),
        "tables_dir": str(paths.tables),
        "listings_dir": str(paths.listings),
        "docs_dir": str(paths.docs),
        "figures": figures,
        "tables": tables,
        "listings": listings,
        "csv": {
            "status": str(paths.processed / "ch08_phase_c_status.csv"),
            "metrics": str(paths.processed / "ch08_phase_c_metrics.csv"),
            "branch_usage": str(paths.processed / "ch08_phase_c_branch_usage.csv"),
            "restconf_installation": str(paths.processed / "ch08_phase_c_restconf_installation.csv"),
        },
        "methodological_note": (
            "ECMP limitado representa un demostrador controlado h1-h16 con grupos OpenFlow select. "
            "No equivale a ECMP completo global ni a reconfiguración dinámica."
        ),
    }

    write_json(paths.processed / "ch08_phase_c_processing_manifest.json", manifest)

    md = generate_markdown_summary(paths, modes, status_rows, branch_rows, manifest)
    write_text(paths.docs / "ch08_phase_c_processing_summary.md", md)

    print("Procesado Fase C — ECMP limitado")
    print("=" * 56)
    print(f"output_id: {paths.output_id}")
    print(f"C.1 campaign: {paths.c1_id}")
    print(f"C.2 campaign: {paths.c2_id}")
    print(f"processed_dir: {paths.processed}")
    print(f"figures_dir: {paths.figures}")
    print(f"tables_dir: {paths.tables}")
    print(f"listings_dir: {paths.listings}")
    print(f"figures_generated: {sum(len(v) for v in figures.values() if isinstance(v, list))}")
    print(f"latex_tables_generated: {len([k for k in tables if k.endswith('_table')])}")
    print(f"latex_listings_generated: {len([k for k in listings if k != 'listings_inputs'])}")
    print(f"status: {final_status}")
    print(f"manifest: {paths.processed / 'ch08_phase_c_processing_manifest.json'}")

    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Procesado de la Fase C ECMP limitado para el Capítulo 8.")
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--c1-campaign-id", default=DEFAULT_C1_ID)
    parser.add_argument("--c2-campaign-id", default=DEFAULT_C2_ID)
    parser.add_argument("--output-id", default=DEFAULT_OUTPUT_ID)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    return process(args)


if __name__ == "__main__":
    raise SystemExit(main())
