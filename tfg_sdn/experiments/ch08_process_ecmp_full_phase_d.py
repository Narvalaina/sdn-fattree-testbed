#!/usr/bin/env python3
"""
Capítulo 8 — Procesado de la Fase D: intento exploratorio de ECMP completo.

Ruta esperada:
    tfg_sdn/experiments/ch08_process_ecmp_full_phase_d.py

Objetivo:
    Generar figuras, tablas LaTeX y listings para redactar la Fase D del
    Capítulo 8 a partir de las campañas ya cerradas operacionalmente:

    - Datapath ovs-ofctl: D.0, D.1 y D.2.
    - Migración OpenDaylight RESTCONF: D.3.

Premisas de redacción:
    - No presentar el resultado como ECMP completo funcional.
    - Presentarlo como intento exploratorio avanzado con migración RESTCONF parcial.
    - Evitar códigos internos de evidencia en elementos visibles del documento.
    - Mantener estética homogénea con las figuras previas del Capítulo 8.

Uso recomendado:
    cd /path/to/sdn-fattree-testbed
    export TFG_ROOT="$PWD"
    export PYTHONPATH="$TFG_ROOT:$PYTHONPATH"

    python3 tfg_sdn/experiments/ch08_process_ecmp_full_phase_d.py \
      --datapath-campaign-id ch08_fattree_ecmp_full_20260521_0853 \
      --restconf-campaign-id ch08_fattree_ecmp_full_restconf_20260521_1705 \
      --output-id ch08_fattree_ecmp_full_phase_d_20260521
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import matplotlib.pyplot as plt


SCRIPT_VERSION = "2026-05-21-phase-d-final-processing-v3-formal-clean"

DEFAULT_DATAPATH_CAMPAIGN = "ch08_fattree_ecmp_full_20260521_0853"
DEFAULT_RESTCONF_CAMPAIGN = "ch08_fattree_ecmp_full_restconf_20260521_1705"
DEFAULT_OUTPUT_ID = "ch08_fattree_ecmp_full_phase_d_20260521"

GROUP_SWITCHES = ["e1", "a1", "a2", "e8", "a7", "a8"]
AGGREGATION_SWITCHES = ["a1", "a2", "a7", "a8"]

SWITCH_ROLE = {
    "e1": "Borde origen",
    "e8": "Borde destino",
    "a1": "Agregación",
    "a2": "Agregación",
    "a7": "Agregación",
    "a8": "Agregación",
}

SCENARIO_LABELS_SHORT = {
    "Prototipo datapath punto a punto": "Datapath\npunto a punto",
    "Prueba de estrés datapath con dos pares": "Datapath\nestrés",
    "Migración RESTCONF punto a punto": "RESTCONF\npunto a punto",
    "Prueba de estrés RESTCONF con dos pares": "RESTCONF\nestrés",
}


@dataclass(frozen=True)
class Paths:
    tfg_root: Path
    datapath_campaign_id: str
    restconf_campaign_id: str
    output_id: str

    @property
    def runs_root(self) -> Path:
        return self.tfg_root / "runs" / "ch08_fattree"

    @property
    def datapath_root(self) -> Path:
        return self.runs_root / self.datapath_campaign_id

    @property
    def restconf_root(self) -> Path:
        return self.runs_root / self.restconf_campaign_id

    @property
    def processed_dir(self) -> Path:
        return self.tfg_root / "data" / "processed" / "ch08_fattree" / self.output_id

    @property
    def figures_dir(self) -> Path:
        return self.tfg_root / "figures" / "ch08_resultados" / self.output_id

    @property
    def tables_dir(self) -> Path:
        return self.tfg_root / "tables" / "ch08" / self.output_id

    @property
    def listings_dir(self) -> Path:
        return self.tfg_root / "listings" / "ch08" / self.output_id

    @property
    def docs_dir(self) -> Path:
        return self.tfg_root / "docs" / "ch08_resultados" / self.output_id

    def ensure(self) -> None:
        for path in [self.processed_dir, self.figures_dir, self.tables_dir, self.listings_dir, self.docs_dir]:
            path.mkdir(parents=True, exist_ok=True)


def default_tfg_root() -> Path:
    return Path(os.environ.get("TFG_ROOT", str(Path(__file__).resolve().parents[2]))).expanduser()


def read_json(path: Path, default: Optional[Any] = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"No existe el archivo requerido: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def latex_escape(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
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


def fmt_float(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number):
        return "--"
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


def bytes_to_gb(value: Any) -> float:
    try:
        return float(value or 0) / 1e9
    except (TypeError, ValueError):
        return 0.0


def packets_to_millions(value: Any) -> float:
    try:
        return float(value or 0) / 1e6
    except (TypeError, ValueError):
        return 0.0


def clean_status(status: Any) -> str:
    mapping = {
        "PASS": "Completado",
        "PASS_DATAPATH_EXPLORATORY": "Completado como prototipo exploratorio",
        "PARTIAL_DATAPATH_STRESS": "Parcial en prueba de estrés datapath",
        "PARTIAL_RESTCONF_TRAFFIC": "Parcial con tráfico RESTCONF",
        "PARTIAL_RESTCONF_STRESS": "Parcial en prueba de estrés RESTCONF",
        "CHECK_REQUIRED": "Revisión requerida",
        "MISSING": "No disponible",
    }
    return mapping.get(str(status), str(status).replace("_", " ").lower())


def listing_status(status: Any) -> str:
    mapping = {
        "PASS": "completado",
        "PASS_DATAPATH_EXPLORATORY": "completado como prototipo exploratorio",
        "PARTIAL_DATAPATH_STRESS": "resultado parcial en prueba de estrés datapath",
        "PARTIAL_RESTCONF_TRAFFIC": "resultado parcial con tráfico RESTCONF",
        "PARTIAL_RESTCONF_STRESS": "resultado parcial en prueba de estrés RESTCONF",
        "CHECK_REQUIRED": "requiere revisión",
        "MISSING": "no disponible",
    }
    return mapping.get(str(status), str(status).replace("_", " ").lower())


def status_is_good(status: Any) -> bool:
    text = str(status)
    return text.startswith("PASS") or text.startswith("PARTIAL")


def make_table(path: Path, caption: str, label: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    col_spec = "l" * len(columns)
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        rf"\caption{{{latex_escape(caption)}}}",
        rf"\label{{{label}}}",
        r"\small",
        rf"\begin{{tabular}}{{{col_spec}}}",
        r"\hline",
        " & ".join(latex_escape(col) for col in columns) + r" \\",
        r"\hline",
    ]
    for row in rows:
        lines.append(" & ".join(latex_escape(cell) for cell in row) + r" \\")
    lines += [r"\hline", r"\end{tabular}", r"\end{table}", ""]
    write_text(path, "\n".join(lines))


def make_listing(path: Path, caption: str, label: str, body: str, language: Optional[str] = "bash") -> None:
    if language:
        start = rf"\begin{{lstlisting}}[language={language},caption={{{latex_escape(caption)}}},label={{{label}}}]"
    else:
        start = rf"\begin{{lstlisting}}[caption={{{latex_escape(caption)}}},label={{{label}}}]"
    text = "\n".join([start, body.rstrip(), r"\end{lstlisting}", ""])
    write_text(path, text)


def save_figure(fig: plt.Figure, out_base: Path) -> List[str]:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    paths: List[str] = []
    for ext in ["png", "svg", "pdf"]:
        out = out_base.with_suffix(f".{ext}")
        if ext == "png":
            fig.savefig(out, dpi=300, bbox_inches="tight")
        else:
            fig.savefig(out, bbox_inches="tight")
        paths.append(str(out))
    plt.close(fig)
    return paths


def add_bar_labels(ax: plt.Axes, bars: Iterable[Any], unit: str = "", decimals: int = 2) -> None:
    for bar in bars:
        height = bar.get_height()
        label = f"{fmt_float(height, decimals)}{unit}"
        ax.annotate(
            label,
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
        )


def first_ping(summary: Dict[str, Any], key: str = "h1_h16") -> Dict[str, Any]:
    if "ping" in summary:
        return summary.get("ping", {})
    return summary.get("pings", {}).get(key, {})


def get_status_summary(paths: Paths) -> Dict[str, Any]:
    return {
        "d0": read_json(paths.datapath_root / "d0_design" / "d0_summary.json"),
        "d1": read_json(paths.datapath_root / "d1_datapath_ovs" / "summary" / "d1_datapath_summary.json"),
        "d2": read_json(paths.datapath_root / "d2_stress_ovs" / "summary" / "d2_stress_summary.json"),
        "d3_cleanup": read_json(paths.restconf_root / "d3_cleanup_ovs" / "cleanup_ovs_summary.json", default={"status": "PASS"}),
        "d3_groups_install": read_json(paths.restconf_root / "d3_groups_restconf" / "install_groups_restconf_summary.json"),
        "d3_groups_verify": read_json(paths.restconf_root / "d3_groups_restconf" / "verify_groups_summary.json"),
        "d3_flows_install": read_json(paths.restconf_root / "d3_flows_restconf" / "install_flows_restconf_summary.json"),
        "d3_traffic": read_json(paths.restconf_root / "d3_traffic_restconf" / "d3_restconf_traffic_summary.json"),
        "d3_stress_flows": read_json(paths.restconf_root / "d3_stress_flows_restconf" / "install_stress_flows_restconf_summary.json"),
        "d3_stress": read_json(paths.restconf_root / "d3_stress_restconf" / "d3_restconf_stress_summary.json"),
    }


def max_ping_metric(summary: Dict[str, Any], metric: str) -> Optional[float]:
    values = [v.get(metric) for v in summary.get("pings", {}).values() if v.get(metric) is not None]
    if not values:
        return None
    return max(float(v) for v in values)


def build_metric_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    d1, d2, d3t, d3s = data["d1"], data["d2"], data["d3_traffic"], data["d3_stress"]
    return [
        {
            "scenario": "Prototipo datapath punto a punto",
            "method": "ovs-ofctl",
            "status": d1.get("status"),
            "ping_loss": first_ping(d1).get("packet_loss_pct"),
            "rtt_avg_ms": first_ping(d1).get("rtt_avg_ms"),
            "iperf": f"{d1.get('iperf_ok')}/{d1.get('iperf_total')}",
            "throughput": d1.get("throughput_sum_gbps"),
            "multi_bucket": ", ".join(d1.get("multi_bucket_switches", [])) or "--",
            "aggregation_multi_bucket": ", ".join(d1.get("aggregation_multi_bucket_switches", [])) or "--",
        },
        {
            "scenario": "Prueba de estrés datapath con dos pares",
            "method": "ovs-ofctl",
            "status": d2.get("status"),
            "ping_loss": max_ping_metric(d2, "packet_loss_pct"),
            "rtt_avg_ms": max_ping_metric(d2, "rtt_avg_ms"),
            "iperf": f"{d2.get('iperf_ok')}/{d2.get('iperf_total')}",
            "throughput": d2.get("throughput_sum_gbps"),
            "multi_bucket": ", ".join(d2.get("multi_bucket_switches", [])) or "--",
            "aggregation_multi_bucket": ", ".join(d2.get("aggregation_multi_bucket_switches", [])) or "--",
        },
        {
            "scenario": "Migración RESTCONF punto a punto",
            "method": "OpenDaylight RESTCONF",
            "status": d3t.get("status"),
            "ping_loss": first_ping(d3t).get("packet_loss_pct"),
            "rtt_avg_ms": first_ping(d3t).get("rtt_avg_ms"),
            "iperf": f"{d3t.get('iperf_ok')}/{d3t.get('iperf_total')}",
            "throughput": d3t.get("throughput_sum_gbps"),
            "multi_bucket": ", ".join(d3t.get("multi_bucket_switches", [])) or "--",
            "aggregation_multi_bucket": ", ".join(d3t.get("aggregation_multi_bucket_switches", [])) or "--",
        },
        {
            "scenario": "Prueba de estrés RESTCONF con dos pares",
            "method": "OpenDaylight RESTCONF",
            "status": d3s.get("status"),
            "ping_loss": max_ping_metric(d3s, "packet_loss_pct"),
            "rtt_avg_ms": max_ping_metric(d3s, "rtt_avg_ms"),
            "iperf": f"{d3s.get('iperf_ok')}/{d3s.get('iperf_total')}",
            "throughput": d3s.get("throughput_sum_gbps"),
            "multi_bucket": ", ".join(d3s.get("multi_bucket_switches", [])) or "--",
            "aggregation_multi_bucket": ", ".join(d3s.get("aggregation_multi_bucket_switches", [])) or "--",
        },
    ]


def group_rows_from_summary(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    groups = summary.get("group_evidence", {})
    rows: List[Dict[str, Any]] = []
    for br in GROUP_SWITCHES:
        g = groups.get(br, {})
        buckets = g.get("buckets", {})
        b0 = buckets.get("bucket0", {})
        b1 = buckets.get("bucket1", {})
        total_bytes = float(g.get("group_bytes", 0) or 0)
        b0_share = 100.0 * float(b0.get("bytes", 0) or 0) / total_bytes if total_bytes > 0 else 0.0
        b1_share = 100.0 * float(b1.get("bytes", 0) or 0) / total_bytes if total_bytes > 0 else 0.0
        rows.append({
            "switch": br,
            "role": SWITCH_ROLE.get(br, "--"),
            "packets": g.get("group_packets", 0),
            "bytes": g.get("group_bytes", 0),
            "packets_m": packets_to_millions(g.get("group_packets", 0)),
            "bytes_gb": bytes_to_gb(g.get("group_bytes", 0)),
            "active_buckets": g.get("active_buckets", 0),
            "bucket0_pct": b0_share,
            "bucket1_pct": b1_share,
        })
    return rows


def scenario_labels(metric_rows: List[Dict[str, Any]]) -> List[str]:
    return [SCENARIO_LABELS_SHORT.get(row["scenario"], row["scenario"]) for row in metric_rows]


def generate_figures(paths: Paths, metric_rows: List[Dict[str, Any]], group_rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    figures: Dict[str, List[str]] = {}

    labels = scenario_labels(metric_rows)
    values = [float(row["throughput"] or 0.0) for row in metric_rows]
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(range(len(values)), values)
    ax.set_title("Throughput agregado del intento exploratorio", fontweight="bold")
    ax.set_ylabel("Throughput agregado (Gbps)")
    apply_spanish_yaxis(ax)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=0, ha="center")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    add_bar_labels(ax, bars, unit=" Gbps", decimals=2)
    ax.set_ylim(0, max(values) * 1.18 if values else 1)
    figures["throughput_summary"] = save_figure(fig, paths.figures_dir / "ch08_phase_d_throughput_summary")

    rtt_values = [float(row["rtt_avg_ms"] or 0.0) for row in metric_rows]
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(range(len(rtt_values)), rtt_values)
    ax.set_title("Latencia media en pings de control", fontweight="bold")
    ax.set_ylabel("RTT medio (ms)")
    apply_spanish_yaxis(ax)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=0, ha="center")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    add_bar_labels(ax, bars, unit=" ms", decimals=3)
    ax.set_ylim(0, max(rtt_values) * 1.25 if rtt_values else 1)
    figures["rtt_summary"] = save_figure(fig, paths.figures_dir / "ch08_phase_d_rtt_summary")

    sw_labels = [row["switch"] for row in group_rows]
    active_values = [int(row["active_buckets"] or 0) for row in group_rows]
    fig, ax = plt.subplots(figsize=(8.5, 5))
    bars = ax.bar(range(len(sw_labels)), active_values)
    ax.set_title("Activación de buckets por switch", fontweight="bold")
    ax.set_ylabel("Buckets activos")
    apply_spanish_yaxis(ax)
    ax.set_xticks(range(len(sw_labels)))
    ax.set_xticklabels(sw_labels)
    ax.set_ylim(0, 2.4)
    ax.set_yticks([0, 1, 2])
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    add_bar_labels(ax, bars, unit="", decimals=0)
    figures["bucket_activation"] = save_figure(fig, paths.figures_dir / "ch08_phase_d_bucket_activation")

    x = list(range(len(sw_labels)))
    width = 0.38
    b0_values = [float(row["bucket0_pct"] or 0.0) for row in group_rows]
    b1_values = [float(row["bucket1_pct"] or 0.0) for row in group_rows]
    fig, ax = plt.subplots(figsize=(9, 5))
    bars0 = ax.bar([i - width / 2 for i in x], b0_values, width, label="Bucket 0")
    bars1 = ax.bar([i + width / 2 for i in x], b1_values, width, label="Bucket 1")
    ax.set_title("Distribución de tráfico por bucket", fontweight="bold")
    ax.set_ylabel("Participación sobre bytes del grupo (%)")
    apply_spanish_yaxis(ax)
    ax.set_xticks(x)
    ax.set_xticklabels(sw_labels)
    ax.set_ylim(0, 110)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.legend()
    add_bar_labels(ax, bars0, unit="%", decimals=1)
    add_bar_labels(ax, bars1, unit="%", decimals=1)
    figures["bucket_distribution"] = save_figure(fig, paths.figures_dir / "ch08_phase_d_bucket_distribution")

    return figures


def generate_tables(paths: Paths, data: Dict[str, Any], metric_rows: List[Dict[str, Any]], group_rows: List[Dict[str, Any]]) -> Dict[str, str]:
    tables: Dict[str, str] = {}

    out = paths.tables_dir / "ch08_phase_d_status.tex"
    make_table(out, "Resumen del intento exploratorio de ECMP completo", "tab:ch08_phase_d_status",
        ["Bloque", "Estado", "Evidencia principal", "Interpretación"], [
            ["Diseño e inventario", clean_status(data["d0"].get("status")), "20 bridges OVS, 20 nodos OpenFlow y mapeo correcto", "Permite pasar al prototipo"],
            ["Prototipo datapath", clean_status(data["d1"].get("status")), "Tráfico h1-h16 con grupos select", "Multipath en switches de borde"],
            ["Prueba de estrés datapath", clean_status(data["d2"].get("status")), "Dos pares de hosts y 16 ejecuciones iperf", "No activa ambos buckets en agregación"],
            ["Migración RESTCONF", clean_status(data["d3_stress"].get("status")), "Grupos y reglas instalados desde ODL", "Migración lograda con resultado parcial"],
        ])
    tables["status"] = str(out)

    metric_table_rows = []
    for row in metric_rows:
        metric_table_rows.append([
            row["scenario"], row["method"], clean_status(row["status"]),
            f"{fmt_float(row['ping_loss'], 1)} %", f"{fmt_float(row['rtt_avg_ms'], 3)} ms",
            row["iperf"], f"{fmt_float(row['throughput'], 2)} Gbps", row["multi_bucket"],
        ])
    out = paths.tables_dir / "ch08_phase_d_traffic_metrics.tex"
    make_table(out, "Métricas de tráfico del intento exploratorio", "tab:ch08_phase_d_traffic_metrics",
        ["Escenario", "Método", "Estado", "Pérdida", "RTT", "iperf", "Throughput", "Buckets activos"], metric_table_rows)
    tables["traffic_metrics"] = str(out)

    out = paths.tables_dir / "ch08_phase_d_restconf_installation.tex"
    make_table(out, "Instalación y verificación mediante OpenDaylight RESTCONF", "tab:ch08_phase_d_restconf_installation",
        ["Elemento", "Estado", "Resultado", "Observación"], [
            ["Limpieza de datapath", clean_status(data["d3_cleanup"].get("status")), "e1, e8, a1, a2, a7, a8 y core", "Preparación limpia"],
            ["Instalación de grupos select", clean_status(data["d3_groups_install"].get("status")), "6/6 switches con grupo select", "RPC correcto y persistencia en datastore"],
            ["Verificación operacional de grupos", clean_status(data["d3_groups_verify"].get("status")), "OVS y ODL operational inventory", "Presencia confirmada"],
            ["Instalación de reglas punto a punto", clean_status(data["d3_flows_install"].get("status")), f"{data['d3_flows_install'].get('n_ok')}/{data['d3_flows_install'].get('n_flows')}", "RESTCONF datastore PUT"],
            ["Instalación de reglas de estrés", clean_status(data["d3_stress_flows"].get("status")), f"{data['d3_stress_flows'].get('n_ok')}/{data['d3_stress_flows'].get('n_flows')}", "RESTCONF datastore PUT"],
        ])
    tables["restconf_installation"] = str(out)

    group_table_rows = []
    for row in group_rows:
        group_table_rows.append([
            row["switch"], row["role"], f"{fmt_float(row['packets_m'], 2)}", f"{fmt_float(row['bytes_gb'], 2)}",
            f"{int(row['active_buckets'])}/2", f"{fmt_float(row['bucket0_pct'], 1)} %", f"{fmt_float(row['bucket1_pct'], 1)} %",
        ])
    out = paths.tables_dir / "ch08_phase_d_group_evidence.tex"
    make_table(out, "Evidencia de grupos select en la prueba RESTCONF final", "tab:ch08_phase_d_group_evidence",
        ["Switch", "Rol", "Paquetes (M)", "Tráfico (GB)", "Buckets", "Bucket 0", "Bucket 1"], group_table_rows)
    tables["group_evidence"] = str(out)

    out = paths.tables_dir / "ch08_phase_d_interpretation.tex"
    make_table(out, "Interpretación defendible del intento exploratorio", "tab:ch08_phase_d_interpretation",
        ["Aspecto", "Resultado", "Base experimental", "Conclusión"], [
            ["Migración a controlador", "Sí", "Grupos y reglas instalados mediante RESTCONF", "Queda validada la trazabilidad ODL-OVS"],
            ["Tráfico estable", "Sí", "Pings sin pérdida e iperf correcto", "La campaña es reproducible"],
            ["Multipath en borde", "Sí", "e1 y e8 activan dos buckets", "Evidencia clara de bifurcación"],
            ["Multipath en agregación", "No", "a1, a2, a7 y a8 usan un solo bucket", "No se afirma ECMP completo"],
            ["ECMP completo Fat-Tree", "No", "Falta activación multibucket en agregación/core", "Resultado parcial y exploratorio"],
        ])
    tables["interpretation"] = str(out)

    out = paths.tables_dir / "ch08_phase_d_tables_inputs.tex"
    write_text(out, "\n".join([rf"\input{{tables/ch08/{paths.output_id}/{Path(path).name}}}" for path in tables.values()]) + "\n")
    tables["inputs"] = str(out)
    return tables


def generate_listings(paths: Paths, data: Dict[str, Any]) -> Dict[str, str]:
    listings: Dict[str, str] = {}

    datapath_sequence = f"""# Secuencia Python-first del prototipo datapath
export CAMPAIGN_ID={paths.datapath_campaign_id}

python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \\
  --campaign-id "$CAMPAIGN_ID" --action d0-capture
python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \\
  --campaign-id "$CAMPAIGN_ID" --action d1-install
python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \\
  --campaign-id "$CAMPAIGN_ID" --action d1-prepare-traffic
python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \\
  --campaign-id "$CAMPAIGN_ID" --action d1-summarize
python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \\
  --campaign-id "$CAMPAIGN_ID" --action d2-install-extra
python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \\
  --campaign-id "$CAMPAIGN_ID" --action d2-prepare-stress
python3 tfg_sdn/experiments/ch08_ecmp_full_datapath_demo.py \\
  --campaign-id "$CAMPAIGN_ID" --action d2-summarize"""
    out = paths.listings_dir / "ch08_phase_d_datapath_sequence.tex"
    make_listing(out, "Secuencia reproducible del prototipo datapath", "lst:ch08_phase_d_datapath_sequence", datapath_sequence)
    listings["datapath_sequence"] = str(out)

    restconf_sequence = f"""# Secuencia Python-first de la migración RESTCONF
export CAMPAIGN_ID={paths.restconf_campaign_id}

python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \\
  --campaign-id "$CAMPAIGN_ID" --action d3-cleanup-ovs
python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \\
  --campaign-id "$CAMPAIGN_ID" --action d3-install-groups-restconf
python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \\
  --campaign-id "$CAMPAIGN_ID" --action d3-verify-groups
python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \\
  --campaign-id "$CAMPAIGN_ID" --action d3-install-flows-restconf
python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \\
  --campaign-id "$CAMPAIGN_ID" --action d3-summarize-traffic
python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \\
  --campaign-id "$CAMPAIGN_ID" --action d3-install-stress-flows-restconf
python3 tfg_sdn/experiments/ch08_ecmp_full_restconf_attempt.py \\
  --campaign-id "$CAMPAIGN_ID" --action d3-summarize-stress"""
    out = paths.listings_dir / "ch08_phase_d_restconf_sequence.tex"
    make_listing(out, "Secuencia reproducible de la migración RESTCONF", "lst:ch08_phase_d_restconf_sequence", restconf_sequence)
    listings["restconf_sequence"] = str(out)

    final_status = f"""Estado final del intento exploratorio
=====================================
Diseño e inventario: {listing_status(data['d0'].get('status'))}
Prototipo datapath: {listing_status(data['d1'].get('status'))}
Prueba de estrés datapath: {listing_status(data['d2'].get('status'))}
Instalación de grupos RESTCONF: {listing_status(data['d3_groups_install'].get('status'))}
Verificación de grupos RESTCONF: {listing_status(data['d3_groups_verify'].get('status'))}
Instalación de reglas RESTCONF: {listing_status(data['d3_flows_install'].get('status'))} ({data['d3_flows_install'].get('n_ok')}/{data['d3_flows_install'].get('n_flows')})
Tráfico RESTCONF: {listing_status(data['d3_traffic'].get('status'))} ({data['d3_traffic'].get('iperf_ok')}/{data['d3_traffic'].get('iperf_total')})
Reglas de estrés RESTCONF: {listing_status(data['d3_stress_flows'].get('status'))} ({data['d3_stress_flows'].get('n_ok')}/{data['d3_stress_flows'].get('n_flows')})
Prueba de estrés RESTCONF: {listing_status(data['d3_stress'].get('status'))} ({data['d3_stress'].get('iperf_ok')}/{data['d3_stress'].get('iperf_total')})
Switches con dos buckets activos: {', '.join(data['d3_stress'].get('multi_bucket_switches', []))}
Switches de agregación con dos buckets activos: {', '.join(data['d3_stress'].get('aggregation_multi_bucket_switches', [])) or 'ninguno'}"""
    out = paths.listings_dir / "ch08_phase_d_final_status.tex"
    make_listing(out, "Resumen final del intento exploratorio", "lst:ch08_phase_d_final_status", final_status, language=None)
    listings["final_status"] = str(out)

    model_excerpt = """Modelo de instalación RESTCONF empleado
=======================================
Grupos select instalados en: e1, a1, a2, e8, a7, a8
Identificador de grupo: 200
Reglas con bifurcación: destino Ethernet del host remoto y acción hacia grupo select
Reglas deterministas: destino Ethernet del host remoto y salida por puerto concreto
Verificación en OVS: descripción de grupos y contadores por bucket
Verificación en OpenDaylight: inventario operacional con presencia del grupo select"""
    out = paths.listings_dir / "ch08_phase_d_restconf_model_excerpt.tex"
    make_listing(out, "Modelo de instalación RESTCONF empleado", "lst:ch08_phase_d_restconf_model_excerpt", model_excerpt, language=None)
    listings["restconf_model_excerpt"] = str(out)

    out = paths.listings_dir / "ch08_phase_d_listings_inputs.tex"
    write_text(out, "\n".join([rf"\input{{listings/ch08/{paths.output_id}/{Path(path).name}}}" for path in listings.values()]) + "\n")
    listings["inputs"] = str(out)
    return listings


def generate_csv_outputs(paths: Paths, metric_rows: List[Dict[str, Any]], group_rows: List[Dict[str, Any]]) -> Dict[str, str]:
    outputs: Dict[str, str] = {}

    metric_csv = paths.processed_dir / "ch08_phase_d_traffic_metrics.csv"
    header = ["scenario", "method", "status", "ping_loss_pct", "rtt_avg_ms", "iperf", "throughput_sum_gbps", "multi_bucket", "aggregation_multi_bucket"]
    lines = [",".join(header)]
    for row in metric_rows:
        values = [row["scenario"], row["method"], row["status"], fmt_float(row["ping_loss"], 3), fmt_float(row["rtt_avg_ms"], 3), row["iperf"], fmt_float(row["throughput"], 6), row["multi_bucket"], row["aggregation_multi_bucket"]]
        lines.append(",".join('"' + str(v).replace('"', '""') + '"' for v in values))
    write_text(metric_csv, "\n".join(lines) + "\n")
    outputs["traffic_metrics_csv"] = str(metric_csv)

    group_csv = paths.processed_dir / "ch08_phase_d_group_evidence.csv"
    header = ["switch", "role", "packets", "bytes", "packets_m", "bytes_gb", "active_buckets", "bucket0_pct", "bucket1_pct"]
    lines = [",".join(header)]
    for row in group_rows:
        values = [row[h] for h in header]
        lines.append(",".join('"' + str(v).replace('"', '""') + '"' for v in values))
    write_text(group_csv, "\n".join(lines) + "\n")
    outputs["group_evidence_csv"] = str(group_csv)
    return outputs


def generate_markdown_summary(paths: Paths, data: Dict[str, Any], figures: Dict[str, List[str]], tables: Dict[str, str], listings: Dict[str, str]) -> str:
    d3s = data["d3_stress"]
    text = f"""# Resumen de procesado — Fase D

## Campañas de entrada

- Datapath: `{paths.datapath_campaign_id}`
- RESTCONF: `{paths.restconf_campaign_id}`

## Interpretación final

La Fase D queda cerrada como intento exploratorio avanzado de ECMP completo en Fat-Tree k=4.
El prototipo fue validado primero en datapath y posteriormente migrado a OpenDaylight RESTCONF.
La campaña final RESTCONF consiguió tráfico estable y prueba de estrés con `iperf3`, pero la activación
multibucket se mantuvo limitada a los switches de borde.

Resultado final defendible:

- Migración RESTCONF: conseguida.
- Tráfico y prueba de estrés RESTCONF: conseguidos.
- Activación multibucket en borde: conseguida.
- Activación multibucket en agregación: no conseguida.
- ECMP completo Fat-Tree: no se afirma.

## Métricas finales RESTCONF estrés

- Estado: `{clean_status(d3s.get('status'))}`
- iperf: `{d3s.get('iperf_ok')}/{d3s.get('iperf_total')}`
- Throughput agregado: `{fmt_float(d3s.get('throughput_sum_gbps'), 2)} Gbps`
- Switches con dos buckets activos: `{', '.join(d3s.get('multi_bucket_switches', []))}`
- Switches de agregación con dos buckets activos: `{', '.join(d3s.get('aggregation_multi_bucket_switches', [])) or 'ninguno'}`

## Figuras generadas

"""
    for key, paths_list in figures.items():
        text += f"- {key}:\n"
        for p in paths_list:
            text += f"  - `{p}`\n"
    text += "\n## Tablas generadas\n\n"
    for key, p in tables.items():
        text += f"- {key}: `{p}`\n"
    text += "\n## Listings generados\n\n"
    for key, p in listings.items():
        text += f"- {key}: `{p}`\n"
    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Procesa la Fase D del Capítulo 8.")
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--datapath-campaign-id", default=DEFAULT_DATAPATH_CAMPAIGN)
    parser.add_argument("--restconf-campaign-id", default=DEFAULT_RESTCONF_CAMPAIGN)
    parser.add_argument("--output-id", default=DEFAULT_OUTPUT_ID)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = Paths(
        tfg_root=Path(args.tfg_root).expanduser().resolve(),
        datapath_campaign_id=args.datapath_campaign_id,
        restconf_campaign_id=args.restconf_campaign_id,
        output_id=args.output_id,
    )
    paths.ensure()

    data = get_status_summary(paths)
    required_statuses = [
        data["d0"].get("status"),
        data["d1"].get("status"),
        data["d2"].get("status"),
        data["d3_groups_install"].get("status"),
        data["d3_groups_verify"].get("status"),
        data["d3_flows_install"].get("status"),
        data["d3_stress_flows"].get("status"),
        data["d3_traffic"].get("status"),
        data["d3_stress"].get("status"),
    ]
    global_ok = all(status_is_good(s) for s in required_statuses)

    metric_rows = build_metric_rows(data)
    group_rows = group_rows_from_summary(data["d3_stress"])
    csv_outputs = generate_csv_outputs(paths, metric_rows, group_rows)
    figures = generate_figures(paths, metric_rows, group_rows)
    tables = generate_tables(paths, data, metric_rows, group_rows)
    listings = generate_listings(paths, data)

    md_path = paths.docs_dir / "ch08_phase_d_processing_summary.md"
    write_text(md_path, generate_markdown_summary(paths, data, figures, tables, listings))

    manifest = {
        "script_version": SCRIPT_VERSION,
        "status": "PASS" if global_ok else "CHECK_REQUIRED",
        "scope": "Phase D exploratory ECMP full attempt processing",
        "tfg_root": str(paths.tfg_root),
        "datapath_campaign_id": paths.datapath_campaign_id,
        "restconf_campaign_id": paths.restconf_campaign_id,
        "output_id": paths.output_id,
        "processed_dir": str(paths.processed_dir),
        "figures_dir": str(paths.figures_dir),
        "tables_dir": str(paths.tables_dir),
        "listings_dir": str(paths.listings_dir),
        "docs_dir": str(paths.docs_dir),
        "csv_outputs": csv_outputs,
        "figures": figures,
        "tables": tables,
        "listings": listings,
        "markdown_summary": str(md_path),
        "final_interpretation": {
            "restconf_migration": True,
            "traffic_and_stress_reproduced": data["d3_stress"].get("iperf_ok") == data["d3_stress"].get("iperf_total"),
            "edge_multibucket": set(data["d3_stress"].get("multi_bucket_switches", [])) == {"e1", "e8"},
            "aggregation_multibucket": data["d3_stress"].get("aggregation_multi_bucket_switches", []),
            "claim_full_fattree_ecmp": False,
        },
    }
    manifest_path = paths.processed_dir / "ch08_phase_d_processing_manifest.json"
    write_json(manifest_path, manifest)

    print("Procesado Fase D — intento exploratorio ECMP completo")
    print("=" * 72)
    print(f"script_version: {SCRIPT_VERSION}")
    print(f"datapath_campaign_id: {paths.datapath_campaign_id}")
    print(f"restconf_campaign_id: {paths.restconf_campaign_id}")
    print(f"output_id: {paths.output_id}")
    print(f"processed_dir: {paths.processed_dir}")
    print(f"figures_dir: {paths.figures_dir}")
    print(f"tables_dir: {paths.tables_dir}")
    print(f"listings_dir: {paths.listings_dir}")
    print(f"figures_generated: {sum(len(v) for v in figures.values())} files / {len(figures)} figures")
    print(f"latex_tables_generated: {len([k for k in tables if k != 'inputs'])}")
    print(f"latex_listings_generated: {len([k for k in listings if k != 'inputs'])}")
    print(f"status: {manifest['status']}")
    print(f"manifest: {manifest_path}")
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


