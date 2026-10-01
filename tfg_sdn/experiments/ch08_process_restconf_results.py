#!/usr/bin/env python3
"""
Procesado de resultados RESTCONF — Capítulo 8 Fat-Tree.

Ruta esperada:
    tfg_sdn/experiments/ch08_process_restconf_results.py

Objetivo:
    Consolidar la campaña RESTCONF principal del Capítulo 8 a partir de los
    artefactos reales generados en runs/ch08_fattree/<campaign_id>/.

Salida generada:
    - CSV consolidados en data/processed/ch08_fattree/<campaign_id>/
    - Figuras PNG/SVG/PDF en figures/ch08_resultados/<campaign_id>/ si matplotlib está disponible
    - Tablas LaTeX en tables/ch08/restconf_<campaign_id>/
    - Resumen Markdown en docs/ch08_resultados/<campaign_id>/

Criterios de estilo de figuras:
    - Throughput en Gbps para legibilidad.
    - Títulos en negrita.
    - Valores anotados sobre barras/puntos con unidad.
    - Exportación triple: PNG, SVG y PDF.

Notas metodológicas:
    - No requiere pandas.
    - matplotlib es opcional, aunque necesario para generar figuras.
    - No representa ECMP, balanceo dinámico, reconfiguración dinámica ni pingall universal.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


BLOCK_DIRS: Dict[str, str] = {
    "FT8.R": "restconf_forwarding",
    "FT8.2R": "ft8_2r_preflight_restconf",
    "FT8.3R": "ft8_3r_p2p_restconf",
    "FT8.4R": "ft8_4r_concurrent_restconf",
    "FT8.5R": "ft8_5r_hotspot_restconf",
}

SUMMARY_FILES: Dict[str, str] = {
    "FT8.R": "restconf_install_summary.json",
    "FT8.2R": "ft8_2r_preflight_summary.json",
    "FT8.3R": "ft8_3r_summary.json",
    "FT8.4R": "ft8_4r_summary.json",
    "FT8.5R": "ft8_5r_summary.json",
}

REPORT_FILES: Dict[str, str] = {
    "FT8.R": "install_report.txt",
    "FT8.2R": "ft8_2r_preflight_report.txt",
    "FT8.3R": "ft8_3r_report.txt",
    "FT8.4R": "ft8_4r_report.txt",
    "FT8.5R": "ft8_5r_report.txt",
}

LATEX_ROW_END = r"\\ \hline"


# ---------------------------------------------------------------------------
# Utilidades básicas
# ---------------------------------------------------------------------------


def default_tfg_root() -> Path:
    return Path(
        os.environ.get(
            "TFG_ROOT",
            str(Path(__file__).resolve().parents[2]),
        )
    ).expanduser()


def find_latest_campaign(tfg_root: Path) -> str:
    base = tfg_root / "runs" / "ch08_fattree"
    if not base.exists():
        raise FileNotFoundError(f"No existe el directorio de campañas: {base}")

    candidates = sorted(
        [
            p.name
            for p in base.iterdir()
            if p.is_dir() and p.name.startswith("ch08_fattree_restconf_")
        ],
        reverse=True,
    )

    if not candidates:
        raise FileNotFoundError(
            f"No se encontraron campañas ch08_fattree_restconf_* en {base}"
        )

    return candidates[0]


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv_rows(
    path: Path,
    rows: List[Dict[str, Any]],
    fieldnames: Optional[Sequence[str]] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if fieldnames is None:
        keys: List[str] = []
        for row in rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(key)
        fieldnames = keys

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def as_float(value: Any) -> Optional[float]:
    if value in (None, "", "None", "nan", "NaN"):
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result) or math.isinf(result):
        return None

    return result


def as_int(value: Any) -> Optional[int]:
    if value in (None, "", "None", "nan", "NaN"):
        return None

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def mean(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (as_float(x) for x in values) if v is not None]
    return statistics.mean(nums) if nums else None


def median(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (as_float(x) for x in values) if v is not None]
    return statistics.median(nums) if nums else None


def min_num(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (as_float(x) for x in values) if v is not None]
    return min(nums) if nums else None


def max_num(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (as_float(x) for x in values) if v is not None]
    return max(nums) if nums else None


def mbps_to_gbps(value: Any) -> Optional[float]:
    v = as_float(value)
    return None if v is None else v / 1000.0


def fmt_num(value: Any, decimals: int = 2) -> str:
    v = as_float(value)
    if v is None:
        return "--"
    return f"{v:.{decimals}f}".replace(".", ",")


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


def fmt_int(value: Any) -> str:
    v = as_int(value)
    return "--" if v is None else str(v)


def scenario_label(scenario_id: Any) -> str:
    """Etiqueta limpia para figuras a partir del identificador interno del escenario."""
    mapping = {
        "t1_local": "Local",
        "t2_intrapod": "Intrapod",
        "t3_interpod": "Interpod",
    }
    return mapping.get(str(scenario_id), str(scenario_id))


def latex_escape(text: Any) -> str:
    s = "" if text is None else str(text)
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
    return "".join(replacements.get(ch, ch) for ch in s)


# ---------------------------------------------------------------------------
# Rutas de campaña
# ---------------------------------------------------------------------------


class CampaignPaths:
    def __init__(self, tfg_root: Path, campaign_id: str) -> None:
        self.tfg_root = tfg_root
        self.campaign_id = campaign_id
        self.campaign_root = tfg_root / "runs" / "ch08_fattree" / campaign_id
        self.processed_dir = (
            tfg_root / "data" / "processed" / "ch08_fattree" / campaign_id
        )
        self.figures_dir = (
            tfg_root / "figures" / "ch08_resultados" / campaign_id
        )
        self.tables_dir = (
            tfg_root / "tables" / "ch08" / f"restconf_{campaign_id}"
        )
        self.docs_dir = (
            tfg_root / "docs" / "ch08_resultados" / campaign_id
        )

    def ensure_dirs(self) -> None:
        for path in [
            self.processed_dir,
            self.figures_dir,
            self.tables_dir,
            self.docs_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Consolidación
# ---------------------------------------------------------------------------


def collect_block_status(paths: CampaignPaths) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for block, dirname in BLOCK_DIRS.items():
        block_dir = paths.campaign_root / dirname
        summary_path = block_dir / SUMMARY_FILES[block]
        report_path = block_dir / REPORT_FILES[block]
        summary = read_json(summary_path)

        if summary is None:
            status = "MISSING"
            n_ok = ""
            n_total = ""
            extra = "summary JSON not found"
        else:
            status = str(summary.get("status", "UNKNOWN"))

            if block == "FT8.R":
                n_ok = summary.get("n_installed_ok", "")
                n_total = summary.get("n_flows", "")
                ovs_ver = read_json(block_dir / "ovs_verification_final.json")
                ovs_status = (
                    ovs_ver.get("status")
                    if ovs_ver
                    else summary.get("ovs_verification_status", "n/a")
                )
                extra = f"ovs_verification={ovs_status}"

            elif block == "FT8.2R":
                n_ok = summary.get("n_ping_ok", "")
                n_total = summary.get("n_ping_checks", "")
                extra = (
                    f"arp={summary.get('arp_evidence_ok')}, "
                    f"flow_counters={summary.get('flow_counter_evidence_ok')}"
                )

            else:
                n_ok = summary.get("n_ok", "")
                n_total = summary.get("n_results", "")
                extra = (
                    f"precheck={summary.get('precheck_ok', 'n/a')}, "
                    f"flow_counters={summary.get('flow_counter_evidence_ok', 'n/a')}"
                )

        rows.append(
            {
                "block": block,
                "directory": dirname,
                "status": status,
                "n_ok": n_ok,
                "n_total": n_total,
                "summary_file": rel(summary_path, paths.tfg_root),
                "report_file": (
                    rel(report_path, paths.tfg_root)
                    if report_path.exists()
                    else ""
                ),
                "extra": extra,
            }
        )

    return rows


def load_inputs(paths: CampaignPaths) -> Dict[str, List[Dict[str, Any]]]:
    base = paths.campaign_root

    return {
        "ft8_2r_ping_summary": read_csv_rows(
            base / "ft8_2r_preflight_restconf" / "ft8_2r_ping_summary.csv"
        ),
        "ft8_3r_results": read_csv_rows(
            base / "ft8_3r_p2p_restconf" / "ft8_3r_results.csv"
        ),
        "ft8_3r_scenario_summary": read_csv_rows(
            base / "ft8_3r_p2p_restconf" / "ft8_3r_scenario_summary.csv"
        ),
        "ft8_3r_flow_evidence": read_csv_rows(
            base / "ft8_3r_p2p_restconf" / "ft8_3r_flow_evidence.csv"
        ),
        "ft8_4r_results": read_csv_rows(
            base / "ft8_4r_concurrent_restconf" / "ft8_4r_results.csv"
        ),
        "ft8_4r_rep_summary": read_csv_rows(
            base / "ft8_4r_concurrent_restconf" / "ft8_4r_rep_summary.csv"
        ),
        "ft8_4r_flow_summary": read_csv_rows(
            base / "ft8_4r_concurrent_restconf" / "ft8_4r_flow_summary.csv"
        ),
        "ft8_4r_precheck_summary": read_csv_rows(
            base / "ft8_4r_concurrent_restconf" / "ft8_4r_precheck_summary.csv"
        ),
        "ft8_4r_flow_evidence": read_csv_rows(
            base / "ft8_4r_concurrent_restconf" / "ft8_4r_flow_evidence.csv"
        ),
        "ft8_5r_results": read_csv_rows(
            base / "ft8_5r_hotspot_restconf" / "ft8_5r_results.csv"
        ),
        "ft8_5r_rep_summary": read_csv_rows(
            base / "ft8_5r_hotspot_restconf" / "ft8_5r_rep_summary.csv"
        ),
        "ft8_5r_flow_summary": read_csv_rows(
            base / "ft8_5r_hotspot_restconf" / "ft8_5r_flow_summary.csv"
        ),
        "ft8_5r_precheck_summary": read_csv_rows(
            base / "ft8_5r_hotspot_restconf" / "ft8_5r_precheck_summary.csv"
        ),
        "ft8_5r_flow_evidence": read_csv_rows(
            base / "ft8_5r_hotspot_restconf" / "ft8_5r_flow_evidence.csv"
        ),
    }


def build_global_summary(
    inputs: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for row in inputs["ft8_3r_scenario_summary"]:
        rows.append(
            {
                "block": "FT8.3R",
                "test_type": "point_to_point",
                "item": row.get("scenario_id", ""),
                "src_dst": f"{row.get('src', '')}->{row.get('dst', '')}",
                "n_repetitions": row.get("n_repetitions", ""),
                "n_ok": row.get("n_ok", ""),
                "throughput_mean_mbps": row.get("throughput_mean_mbps", ""),
                "throughput_mean_gbps": mbps_to_gbps(
                    row.get("throughput_mean_mbps")
                ),
                "throughput_median_mbps": row.get(
                    "throughput_median_mbps", ""
                ),
                "throughput_median_gbps": mbps_to_gbps(
                    row.get("throughput_median_mbps")
                ),
                "throughput_min_mbps": row.get("throughput_min_mbps", ""),
                "throughput_min_gbps": mbps_to_gbps(
                    row.get("throughput_min_mbps")
                ),
                "throughput_max_mbps": row.get("throughput_max_mbps", ""),
                "throughput_max_gbps": mbps_to_gbps(
                    row.get("throughput_max_mbps")
                ),
                "aggregate_receiver_mbps": "",
                "aggregate_receiver_gbps": "",
                "rtt_avg_mean_ms": row.get("rtt_avg_mean_ms", ""),
                "note": "flujo individual",
            }
        )

    t4 = inputs["ft8_4r_rep_summary"]
    if t4:
        agg_mbps = mean(r.get("aggregate_receiver_mbps") for r in t4)
        mean_flow_mbps = mean(r.get("mean_flow_receiver_mbps") for r in t4)

        rows.append(
            {
                "block": "FT8.4R",
                "test_type": "concurrent_distributed",
                "item": "T4 aggregate",
                "src_dst": "4 concurrent flows",
                "n_repetitions": len(t4),
                "n_ok": sum(as_int(r.get("n_ok")) or 0 for r in t4),
                "throughput_mean_mbps": mean_flow_mbps,
                "throughput_mean_gbps": mbps_to_gbps(mean_flow_mbps),
                "throughput_median_mbps": median(
                    r.get("mean_flow_receiver_mbps") for r in t4
                ),
                "throughput_median_gbps": mbps_to_gbps(
                    median(r.get("mean_flow_receiver_mbps") for r in t4)
                ),
                "throughput_min_mbps": min_num(
                    r.get("mean_flow_receiver_mbps") for r in t4
                ),
                "throughput_min_gbps": mbps_to_gbps(
                    min_num(r.get("mean_flow_receiver_mbps") for r in t4)
                ),
                "throughput_max_mbps": max_num(
                    r.get("mean_flow_receiver_mbps") for r in t4
                ),
                "throughput_max_gbps": mbps_to_gbps(
                    max_num(r.get("mean_flow_receiver_mbps") for r in t4)
                ),
                "aggregate_receiver_mbps": agg_mbps,
                "aggregate_receiver_gbps": mbps_to_gbps(agg_mbps),
                "rtt_avg_mean_ms": "",
                "note": "agregado medio por repetición",
            }
        )

    t5 = inputs["ft8_5r_rep_summary"]
    if t5:
        agg_mbps = mean(r.get("aggregate_receiver_mbps") for r in t5)
        mean_flow_mbps = mean(r.get("mean_flow_receiver_mbps") for r in t5)

        rows.append(
            {
                "block": "FT8.5R",
                "test_type": "hotspot",
                "item": "T5 aggregate",
                "src_dst": "4 sources -> h16",
                "n_repetitions": len(t5),
                "n_ok": sum(as_int(r.get("n_ok")) or 0 for r in t5),
                "throughput_mean_mbps": mean_flow_mbps,
                "throughput_mean_gbps": mbps_to_gbps(mean_flow_mbps),
                "throughput_median_mbps": median(
                    r.get("mean_flow_receiver_mbps") for r in t5
                ),
                "throughput_median_gbps": mbps_to_gbps(
                    median(r.get("mean_flow_receiver_mbps") for r in t5)
                ),
                "throughput_min_mbps": min_num(
                    r.get("mean_flow_receiver_mbps") for r in t5
                ),
                "throughput_min_gbps": mbps_to_gbps(
                    min_num(r.get("mean_flow_receiver_mbps") for r in t5)
                ),
                "throughput_max_mbps": max_num(
                    r.get("mean_flow_receiver_mbps") for r in t5
                ),
                "throughput_max_gbps": mbps_to_gbps(
                    max_num(r.get("mean_flow_receiver_mbps") for r in t5)
                ),
                "aggregate_receiver_mbps": agg_mbps,
                "aggregate_receiver_gbps": mbps_to_gbps(agg_mbps),
                "rtt_avg_mean_ms": "",
                "note": "agregado medio por repetición",
            }
        )

    return rows


def enrich_gbps_columns(inputs: Dict[str, List[Dict[str, Any]]]) -> None:
    """Añade columnas *_gbps a los CSV copiados para facilitar lectura posterior."""
    for key in [
        "ft8_3r_results",
        "ft8_3r_scenario_summary",
        "ft8_4r_results",
        "ft8_4r_rep_summary",
        "ft8_4r_flow_summary",
        "ft8_5r_results",
        "ft8_5r_rep_summary",
        "ft8_5r_flow_summary",
    ]:
        for row in inputs.get(key, []):
            for src_col, dst_col in [
                ("receiver_mbps", "receiver_gbps"),
                ("sender_mbps", "sender_gbps"),
                ("throughput_mean_mbps", "throughput_mean_gbps"),
                ("throughput_median_mbps", "throughput_median_gbps"),
                ("throughput_min_mbps", "throughput_min_gbps"),
                ("throughput_max_mbps", "throughput_max_gbps"),
                ("aggregate_receiver_mbps", "aggregate_receiver_gbps"),
                ("mean_flow_receiver_mbps", "mean_flow_receiver_gbps"),
                ("min_flow_receiver_mbps", "min_flow_receiver_gbps"),
                ("max_flow_receiver_mbps", "max_flow_receiver_gbps"),
            ]:
                if src_col in row and dst_col not in row:
                    row[dst_col] = mbps_to_gbps(row.get(src_col))


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


def apply_common_style(ax: Any) -> None:
    ax.grid(True, axis="y", alpha=0.28, linewidth=0.9)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(True)
    ax.spines["right"].set_visible(True)
    ax.tick_params(axis="both", labelsize=11)


def save_figure(plt: Any, fig: Any, output_base: Path) -> List[str]:
    output_base.parent.mkdir(parents=True, exist_ok=True)

    png = output_base.with_suffix(".png")
    svg = output_base.with_suffix(".svg")
    pdf = output_base.with_suffix(".pdf")

    fig.tight_layout()
    fig.savefig(png, dpi=180, bbox_inches="tight")
    fig.savefig(svg, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)

    return [str(png), str(svg), str(pdf)]


def add_bar_labels(ax: Any, bars: Any, unit: str, decimals: int) -> None:
    for bar in bars:
        height = bar.get_height()
        if height is None or math.isnan(height):
            continue

        ax.annotate(
            f"{fmt_num(height, decimals)} {unit}",
            xy=(bar.get_x() + bar.get_width() / 2.0, height),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=10.5,
            fontweight="bold",
        )


def add_point_labels(
    ax: Any,
    labels: List[str],
    values: List[float],
    unit: str,
    decimals: int,
) -> None:
    for idx, value in enumerate(values):
        ax.annotate(
            f"{fmt_num(value, decimals)} {unit}",
            xy=(idx, value),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=10.5,
            fontweight="bold",
        )


def set_y_limit_from_zero(ax: Any, values: List[float]) -> None:
    if not values:
        return

    max_value = max(values)
    upper = max_value * 1.18 if max_value > 0 else 1.0
    ax.set_ylim(0, upper)


def bar_chart(
    plt: Any,
    labels: List[str],
    values: List[float],
    title: str,
    xlabel: str,
    ylabel: str,
    output_base: Path,
    unit: str,
    decimals: int = 2,
    rotate: int = 0,
) -> Optional[List[str]]:
    if not labels or not values or len(labels) != len(values):
        return None

    fig = plt.figure(figsize=(10.5, 5.8))
    ax = fig.add_subplot(111)

    bars = ax.bar(labels, values)

    ax.set_title(title, fontsize=18, fontweight="bold", pad=18)
    ax.set_xlabel(xlabel, fontsize=13)
    ax.set_ylabel(ylabel, fontsize=13)
    apply_spanish_yaxis(ax)

    apply_common_style(ax)
    set_y_limit_from_zero(ax, values)
    add_bar_labels(ax, bars, unit=unit, decimals=decimals)

    if rotate:
        for tick in ax.get_xticklabels():
            tick.set_rotation(rotate)
            tick.set_ha("right")

    return save_figure(plt, fig, output_base)


def line_chart(
    plt: Any,
    labels: List[str],
    values: List[float],
    title: str,
    xlabel: str,
    ylabel: str,
    output_base: Path,
    unit: str,
    decimals: int = 2,
    y_from_zero: bool = True,
) -> Optional[List[str]]:
    if not labels or not values or len(labels) != len(values):
        return None

    fig = plt.figure(figsize=(10.5, 5.8))
    ax = fig.add_subplot(111)

    ax.plot(labels, values, marker="o", linewidth=2.0, markersize=7)
    ax.margins(x=0.10)

    ax.set_title(title, fontsize=18, fontweight="bold", pad=18)
    ax.set_xlabel(xlabel, fontsize=13)
    ax.set_ylabel(ylabel, fontsize=13)
    apply_spanish_yaxis(ax)

    apply_common_style(ax)

    if y_from_zero:
        set_y_limit_from_zero(ax, values)
    else:
        min_value = min(values)
        max_value = max(values)
        span = max(max_value - min_value, max_value * 0.05, 1.0)
        ax.set_ylim(max(0, min_value - 0.2 * span), max_value + 0.25 * span)

    add_point_labels(ax, labels, values, unit=unit, decimals=decimals)

    return save_figure(plt, fig, output_base)


def valid_pairs(rows: List[Dict[str, Any]], label_fn: Any, value_fn: Any) -> tuple[List[str], List[float]]:
    labels: List[str] = []
    values: List[float] = []

    for row in rows:
        value = value_fn(row)
        if value is None:
            continue
        labels.append(label_fn(row))
        values.append(value)

    return labels, values


def generate_figures(
    paths: CampaignPaths,
    inputs: Dict[str, List[Dict[str, Any]]],
    global_summary: List[Dict[str, Any]],
    no_figures: bool,
) -> Dict[str, List[str]]:
    if no_figures:
        return {}

    plt = import_matplotlib()
    if plt is None:
        return {}

    figures: Dict[str, List[str]] = {}

    p2p = inputs["ft8_3r_scenario_summary"]

    labels, values = valid_pairs(
        p2p,
        lambda r: f"{scenario_label(r.get('scenario_id'))}\n{r.get('src')}→{r.get('dst')}",
        lambda r: mbps_to_gbps(r.get("throughput_mean_mbps")),
    )
    out = bar_chart(
        plt,
        labels,
        values,
        "Throughput medio punto a punto",
        "Escenario",
        "Throughput medio recibido (Gbps)",
        paths.figures_dir / "ch08_ft8_3r_p2p_throughput_mean",
        unit="Gbps",
        decimals=2,
    )
    if out:
        figures["ft8_3r_p2p_throughput_mean"] = out

    labels, values = valid_pairs(
        p2p,
        lambda r: f"{scenario_label(r.get('scenario_id'))}\n{r.get('src')}→{r.get('dst')}",
        lambda r: as_float(r.get("rtt_avg_mean_ms")),
    )
    out = bar_chart(
        plt,
        labels,
        values,
        "RTT medio en pings de control",
        "Escenario",
        "RTT medio (ms)",
        paths.figures_dir / "ch08_ft8_3r_p2p_rtt_mean",
        unit="ms",
        decimals=3,
    )
    if out:
        figures["ft8_3r_p2p_rtt_mean"] = out

    t4_rep = inputs["ft8_4r_rep_summary"]

    labels, values = valid_pairs(
        t4_rep,
        lambda r: f"rep{fmt_int(r.get('rep')).zfill(2)}",
        lambda r: mbps_to_gbps(r.get("aggregate_receiver_mbps")),
    )
    out = line_chart(
        plt,
        labels,
        values,
        "Throughput agregado por repetición",
        "Repetición",
        "Throughput agregado recibido (Gbps)",
        paths.figures_dir / "ch08_ft8_4r_concurrent_aggregate_by_rep",
        unit="Gbps",
        decimals=2,
        y_from_zero=True,
    )
    if out:
        figures["ft8_4r_concurrent_aggregate_by_rep"] = out

    t4_flow = inputs["ft8_4r_flow_summary"]

    labels, values = valid_pairs(
        t4_flow,
        lambda r: f"{r.get('src')}→{r.get('dst')}",
        lambda r: mbps_to_gbps(r.get("throughput_mean_mbps")),
    )
    out = bar_chart(
        plt,
        labels,
        values,
        "Throughput medio por flujo concurrente",
        "Flujo",
        "Throughput medio recibido (Gbps)",
        paths.figures_dir / "ch08_ft8_4r_concurrent_mean_by_flow",
        unit="Gbps",
        decimals=2,
    )
    if out:
        figures["ft8_4r_concurrent_mean_by_flow"] = out

    t5_rep = inputs["ft8_5r_rep_summary"]

    labels, values = valid_pairs(
        t5_rep,
        lambda r: f"rep{fmt_int(r.get('rep')).zfill(2)}",
        lambda r: mbps_to_gbps(r.get("aggregate_receiver_mbps")),
    )
    out = line_chart(
        plt,
        labels,
        values,
        "Throughput agregado hotspot por repetición",
        "Repetición",
        "Throughput agregado recibido (Gbps)",
        paths.figures_dir / "ch08_ft8_5r_hotspot_aggregate_by_rep",
        unit="Gbps",
        decimals=2,
        y_from_zero=True,
    )
    if out:
        figures["ft8_5r_hotspot_aggregate_by_rep"] = out

    t5_flow = inputs["ft8_5r_flow_summary"]

    labels, values = valid_pairs(
        t5_flow,
        lambda r: f"{r.get('src')}→{r.get('dst')}",
        lambda r: mbps_to_gbps(r.get("throughput_mean_mbps")),
    )
    out = bar_chart(
        plt,
        labels,
        values,
        "Throughput medio por flujo hotspot",
        "Flujo",
        "Throughput medio recibido (Gbps)",
        paths.figures_dir / "ch08_ft8_5r_hotspot_mean_by_flow",
        unit="Gbps",
        decimals=2,
    )
    if out:
        figures["ft8_5r_hotspot_mean_by_flow"] = out

    agg_rows = [
        r for r in global_summary if as_float(r.get("aggregate_receiver_gbps")) is not None
    ]

    labels, values = valid_pairs(
        agg_rows,
        lambda r: (
            "Tráfico distribuido"
            if r.get("block") == "FT8.4R"
            else "Tráfico hotspot"
            if r.get("block") == "FT8.5R"
            else str(r.get("item"))
        ),
        lambda r: as_float(r.get("aggregate_receiver_gbps")),
    )
    out = bar_chart(
        plt,
        labels,
        values,
        "Comparativa agregada entre tráfico distribuido y hotspot",
        "Bloque",
        "Throughput agregado medio recibido (Gbps)",
        paths.figures_dir / "ch08_restconf_t4_t5_aggregate_comparison",
        unit="Gbps",
        decimals=2,
    )
    if out:
        figures["restconf_t4_t5_aggregate_comparison"] = out

    return figures


# ---------------------------------------------------------------------------
# Tablas LaTeX
# ---------------------------------------------------------------------------


def latex_table(body: str, caption: str, short_caption: str, label: str) -> str:
    return "\n".join(
        [
            r"\begin{table}[!htbp]",
            r"\centering",
            r"\footnotesize",
            r"\setlength{\tabcolsep}{4pt}",
            r"\renewcommand{\arraystretch}{1.15}",
            f"\\caption[{latex_escape(short_caption)}]{{{latex_escape(caption)}}}",
            f"\\label{{{label}}}",
            body,
            r"\end{table}",
            "",
        ]
    )


def generate_status_table(rows: List[Dict[str, Any]], paths: CampaignPaths) -> Path:
    names = {
        "FT8.R": "Instalación forwarding RESTCONF",
        "FT8.2R": "Preflight RESTCONF",
        "FT8.3R": "Punto a punto RESTCONF",
        "FT8.4R": "Concurrente distribuido",
        "FT8.5R": "Hotspot RESTCONF",
    }

    lines = [
        r"\begin{tabularx}{\textwidth}{|p{1.5cm}|p{2.8cm}|p{1.7cm}|p{1.7cm}|X|}",
        r"\hline",
        r"\textbf{Bloque} & \textbf{Evidencia} & \textbf{Estado} & \textbf{OK/Total} & \textbf{Observación} \\ \hline",
    ]

    for row in rows:
        ok_total = f"{fmt_int(row.get('n_ok'))}/{fmt_int(row.get('n_total'))}"
        lines.append(
            f"{latex_escape(row.get('block'))} & "
            f"{latex_escape(names.get(row.get('block'), row.get('directory')))} & "
            f"{latex_escape(row.get('status'))} & "
            f"{latex_escape(ok_total)} & "
            f"{latex_escape(row.get('extra'))} {LATEX_ROW_END}"
        )

    lines.append(r"\end{tabularx}")

    out = paths.tables_dir / "ch08_restconf_campaign_status.tex"
    write_text(
        out,
        latex_table(
            "\n".join(lines),
            "Estado de los bloques experimentales de la campaña RESTCONF determinista",
            "Estado de la campaña RESTCONF",
            "tab:ch08_restconf_campaign_status",
        ),
    )
    return out


def generate_p2p_table(rows: List[Dict[str, Any]], paths: CampaignPaths) -> Optional[Path]:
    if not rows:
        return None

    lines = [
        r"\begin{tabularx}{\textwidth}{|p{2.1cm}|p{2.1cm}|p{1.6cm}|p{2.2cm}|p{2.2cm}|X|}",
        r"\hline",
        r"\textbf{Escenario} & \textbf{Par} & \textbf{OK} & \textbf{Throughput medio} & \textbf{RTT medio} & \textbf{Camino determinista} \\ \hline",
    ]

    for row in rows:
        pair = f"{row.get('src')} $\\rightarrow$ {row.get('dst')}"
        ok = f"{fmt_int(row.get('n_ok'))}/{fmt_int(row.get('n_repetitions'))}"
        throughput = f"{fmt_num(mbps_to_gbps(row.get('throughput_mean_mbps')), 2)} Gbps"
        rtt = f"{fmt_num(row.get('rtt_avg_mean_ms'), 3)} ms"
        path = row.get("expected_path", "")

        lines.append(
            f"{latex_escape(row.get('scenario_id'))} & "
            f"{pair} & "
            f"{latex_escape(ok)} & "
            f"{latex_escape(throughput)} & "
            f"{latex_escape(rtt)} & "
            f"{latex_escape(path)} {LATEX_ROW_END}"
        )

    lines.append(r"\end{tabularx}")

    out = paths.tables_dir / "ch08_ft8_3r_p2p_summary.tex"
    write_text(
        out,
        latex_table(
            "\n".join(lines),
            "Resumen de los ensayos punto a punto FT8.3R sobre forwarding RESTCONF determinista",
            "Ensayos punto a punto RESTCONF",
            "tab:ch08_ft8_3r_p2p_summary",
        ),
    )
    return out


def generate_flow_table(
    rows: List[Dict[str, Any]],
    paths: CampaignPaths,
    filename: str,
    caption: str,
    short_caption: str,
    label: str,
) -> Optional[Path]:
    if not rows:
        return None

    lines = [
        r"\begin{tabularx}{\textwidth}{|p{2.5cm}|p{2.0cm}|p{1.8cm}|p{2.4cm}|p{2.4cm}|X|}",
        r"\hline",
        r"\textbf{Flujo} & \textbf{Par} & \textbf{OK} & \textbf{Throughput medio} & \textbf{Rango} & \textbf{Camino determinista} \\ \hline",
    ]

    for row in rows:
        pair = f"{row.get('src')} $\\rightarrow$ {row.get('dst')}"
        ok = f"{fmt_int(row.get('n_ok'))}/{fmt_int(row.get('n_repetitions'))}"
        mean_value = f"{fmt_num(mbps_to_gbps(row.get('throughput_mean_mbps')), 2)} Gbps"
        rng = (
            f"{fmt_num(mbps_to_gbps(row.get('throughput_min_mbps')), 2)}--"
            f"{fmt_num(mbps_to_gbps(row.get('throughput_max_mbps')), 2)} Gbps"
        )
        path = row.get("expected_path", row.get("path", ""))

        lines.append(
            f"{latex_escape(row.get('flow_id'))} & "
            f"{pair} & "
            f"{latex_escape(ok)} & "
            f"{latex_escape(mean_value)} & "
            f"{latex_escape(rng)} & "
            f"{latex_escape(path)} {LATEX_ROW_END}"
        )

    lines.append(r"\end{tabularx}")

    out = paths.tables_dir / filename
    write_text(out, latex_table("\n".join(lines), caption, short_caption, label))
    return out


def generate_aggregate_table(
    rows: List[Dict[str, Any]],
    paths: CampaignPaths,
) -> Optional[Path]:
    if not rows:
        return None

    lines = [
        r"\begin{tabularx}{\textwidth}{|p{1.6cm}|p{3.0cm}|p{2.4cm}|p{2.4cm}|p{2.4cm}|X|}",
        r"\hline",
        r"\textbf{Bloque} & \textbf{Tipo} & \textbf{Elemento} & \textbf{Throughput medio} & \textbf{Agregado medio} & \textbf{Nota} \\ \hline",
    ]

    for row in rows:
        throughput = f"{fmt_num(row.get('throughput_mean_gbps'), 2)} Gbps"
        aggregate = (
            "--"
            if as_float(row.get("aggregate_receiver_gbps")) is None
            else f"{fmt_num(row.get('aggregate_receiver_gbps'), 2)} Gbps"
        )

        lines.append(
            f"{latex_escape(row.get('block'))} & "
            f"{latex_escape(row.get('test_type'))} & "
            f"{latex_escape(row.get('item'))} & "
            f"{latex_escape(throughput)} & "
            f"{latex_escape(aggregate)} & "
            f"{latex_escape(row.get('note'))} {LATEX_ROW_END}"
        )

    lines.append(r"\end{tabularx}")

    out = paths.tables_dir / "ch08_restconf_throughput_synthesis.tex"
    write_text(
        out,
        latex_table(
            "\n".join(lines),
            "Síntesis de throughput de la campaña RESTCONF determinista",
            "Síntesis de throughput RESTCONF",
            "tab:ch08_restconf_throughput_synthesis",
        ),
    )
    return out


def generate_latex_tables(
    paths: CampaignPaths,
    status_rows: List[Dict[str, Any]],
    inputs: Dict[str, List[Dict[str, Any]]],
    global_summary: List[Dict[str, Any]],
) -> List[Path]:
    table_paths: List[Path] = []

    table_paths.append(generate_status_table(status_rows, paths))

    p = generate_p2p_table(inputs["ft8_3r_scenario_summary"], paths)
    if p:
        table_paths.append(p)

    p = generate_flow_table(
        inputs["ft8_4r_flow_summary"],
        paths,
        "ch08_ft8_4r_concurrent_summary.tex",
        "Resumen por flujo del escenario concurrente distribuido FT8.4R",
        "Escenario concurrente distribuido RESTCONF",
        "tab:ch08_ft8_4r_concurrent_summary",
    )
    if p:
        table_paths.append(p)

    p = generate_flow_table(
        inputs["ft8_5r_flow_summary"],
        paths,
        "ch08_ft8_5r_hotspot_summary.tex",
        "Resumen por flujo del escenario hotspot FT8.5R hacia h16",
        "Escenario hotspot RESTCONF",
        "tab:ch08_ft8_5r_hotspot_summary",
    )
    if p:
        table_paths.append(p)

    p = generate_aggregate_table(global_summary, paths)
    if p:
        table_paths.append(p)

    input_file = paths.tables_dir / "ch08_restconf_tables_inputs.tex"
    lines = [
        "% Inserciones sugeridas para el Capítulo 8",
        "% Revisar ubicación exacta en chapters/ch08_resultados.tex o equivalente.",
        "",
    ]

    for path in table_paths:
        lines.append(f"\\input{{{latex_escape(rel(path, paths.tfg_root))}}}")

    lines.append("")
    write_text(input_file, "\n".join(lines))
    table_paths.append(input_file)

    return table_paths


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def generate_markdown(
    paths: CampaignPaths,
    status_rows: List[Dict[str, Any]],
    inputs: Dict[str, List[Dict[str, Any]]],
    figures: Dict[str, List[str]],
    table_paths: List[Path],
) -> Path:
    lines: List[str] = []

    lines.append(f"# Resumen de procesado — Campaña RESTCONF `{paths.campaign_id}`")
    lines.append("")
    lines.append("## Lectura metodológica")
    lines.append("")
    lines.append(
        "La campaña procesada corresponde a forwarding determinista instalado mediante "
        "OpenDaylight RESTCONF sobre Fat-Tree `k=4`. No debe describirse como ECMP, "
        "balanceo automático, reconfiguración dinámica ni conectividad universal `pingall`."
    )
    lines.append("")

    lines.append("## Estado de bloques")
    lines.append("")
    lines.append("| Bloque | Estado | OK/Total | Observación |")
    lines.append("|---|---:|---:|---|")

    for row in status_rows:
        ok_total = f"{fmt_int(row.get('n_ok'))}/{fmt_int(row.get('n_total'))}"
        lines.append(
            f"| {row.get('block')} | {row.get('status')} | {ok_total} | {row.get('extra')} |"
        )

    lines.append("")

    lines.append("## FT8.3R — Punto a punto")
    lines.append("")
    lines.append("| Escenario | Par | OK | Throughput medio (Gbps) | RTT medio (ms) |")
    lines.append("|---|---|---:|---:|---:|")

    for row in inputs["ft8_3r_scenario_summary"]:
        lines.append(
            f"| {row.get('scenario_id')} | {row.get('src')}→{row.get('dst')} | "
            f"{fmt_int(row.get('n_ok'))}/{fmt_int(row.get('n_repetitions'))} | "
            f"{fmt_num(mbps_to_gbps(row.get('throughput_mean_mbps')), 2)} | "
            f"{fmt_num(row.get('rtt_avg_mean_ms'), 3)} |"
        )

    lines.append("")

    lines.append("## FT8.4R — Concurrente distribuido")
    lines.append("")
    lines.append(
        "| Repetición | OK | Throughput agregado (Gbps) | Throughput medio por flujo (Gbps) |"
    )
    lines.append("|---:|---:|---:|---:|")

    for row in inputs["ft8_4r_rep_summary"]:
        lines.append(
            f"| {fmt_int(row.get('rep'))} | "
            f"{fmt_int(row.get('n_ok'))}/{fmt_int(row.get('n_flows'))} | "
            f"{fmt_num(mbps_to_gbps(row.get('aggregate_receiver_mbps')), 2)} | "
            f"{fmt_num(mbps_to_gbps(row.get('mean_flow_receiver_mbps')), 2)} |"
        )

    lines.append("")

    lines.append("## FT8.5R — Hotspot")
    lines.append("")
    lines.append(
        "| Repetición | OK | Throughput agregado (Gbps) | Throughput medio por flujo (Gbps) |"
    )
    lines.append("|---:|---:|---:|---:|")

    for row in inputs["ft8_5r_rep_summary"]:
        lines.append(
            f"| {fmt_int(row.get('rep'))} | "
            f"{fmt_int(row.get('n_ok'))}/{fmt_int(row.get('n_flows'))} | "
            f"{fmt_num(mbps_to_gbps(row.get('aggregate_receiver_mbps')), 2)} | "
            f"{fmt_num(mbps_to_gbps(row.get('mean_flow_receiver_mbps')), 2)} |"
        )

    lines.append("")

    lines.append("## Figuras generadas")
    lines.append("")

    if figures:
        for name, outs in figures.items():
            out_rel = ", ".join(rel(Path(p), paths.tfg_root) for p in outs)
            lines.append(f"- `{name}`: {out_rel}")
    else:
        lines.append(
            "- No se generaron figuras. Para generarlas, instala `python3-matplotlib` "
            "o ejecuta con matplotlib disponible."
        )

    lines.append("")

    lines.append("## Tablas LaTeX generadas")
    lines.append("")

    for path in table_paths:
        lines.append(f"- `{rel(path, paths.tfg_root)}`")

    lines.append("")

    lines.append("## Frases defendibles")
    lines.append("")
    lines.append(
        "- La campaña valida forwarding determinista instalado mediante OpenDaylight "
        "RESTCONF en los escenarios experimentales T1--T5."
    )
    lines.append(
        "- Los resultados se apoyan en pings de precheck, salidas JSON de `iperf3`, "
        "resúmenes CSV y contadores no nulos en flows de OVS."
    )
    lines.append(
        "- La campaña no debe presentarse como ECMP Fat-Tree ni como reconfiguración dinámica."
    )
    lines.append("")

    out = paths.docs_dir / "ch08_restconf_processing_summary.md"
    write_text(out, "\n".join(lines))

    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Procesa la campaña RESTCONF principal del Capítulo 8 Fat-Tree."
    )
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument(
        "--campaign-id",
        default=None,
        help="Si se omite, usa la última campaña ch08_fattree_restconf_*.",
    )
    parser.add_argument(
        "--no-figures",
        action="store_true",
        help="No generar figuras aunque matplotlib esté disponible.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    tfg_root = Path(args.tfg_root).expanduser().resolve()
    campaign_id = args.campaign_id or find_latest_campaign(tfg_root)
    paths = CampaignPaths(tfg_root, campaign_id)

    if not paths.campaign_root.exists():
        print(f"[FAIL] No existe la campaña: {paths.campaign_root}", file=sys.stderr)
        return 2

    paths.ensure_dirs()

    status_rows = collect_block_status(paths)
    inputs = load_inputs(paths)
    enrich_gbps_columns(inputs)
    global_summary = build_global_summary(inputs)

    write_csv_rows(paths.processed_dir / "ch08_restconf_campaign_status.csv", status_rows)
    write_csv_rows(
        paths.processed_dir / "ch08_restconf_global_traffic_summary.csv",
        global_summary,
    )

    for name, rows in inputs.items():
        if rows:
            write_csv_rows(paths.processed_dir / f"{name}.csv", rows)

    figures = generate_figures(paths, inputs, global_summary, no_figures=args.no_figures)
    table_paths = generate_latex_tables(paths, status_rows, inputs, global_summary)
    md_path = generate_markdown(paths, status_rows, inputs, figures, table_paths)

    manifest = {
        "campaign_id": campaign_id,
        "campaign_root": str(paths.campaign_root),
        "processed_dir": str(paths.processed_dir),
        "figures_dir": str(paths.figures_dir),
        "tables_dir": str(paths.tables_dir),
        "docs_dir": str(paths.docs_dir),
        "figures": figures,
        "tables": [str(path) for path in table_paths],
        "markdown_summary": str(md_path),
        "methodological_note": (
            "Procesado de campaña RESTCONF determinista. Figuras en Gbps cuando "
            "representan throughput. No ECMP, no balanceo dinámico, no pingall universal."
        ),
    }
    write_json(paths.processed_dir / "ch08_restconf_processing_manifest.json", manifest)

    all_pass = all(row.get("status") == "PASS" for row in status_rows)

    print("Procesado Capítulo 8 RESTCONF")
    print("=" * 48)
    print(f"campaign_id: {campaign_id}")
    print(f"campaign_root: {paths.campaign_root}")
    print(f"processed_dir: {paths.processed_dir}")
    print(f"figures_dir: {paths.figures_dir}")
    print(f"tables_dir: {paths.tables_dir}")
    print(f"docs_dir: {paths.docs_dir}")
    print("")
    print("Estado de bloques:")

    for row in status_rows:
        print(
            f"  {row['block']}: {row['status']} "
            f"({fmt_int(row.get('n_ok'))}/{fmt_int(row.get('n_total'))})"
        )

    print("")
    print(f"figures_generated: {len(figures)}")

    if figures:
        print("figure_formats: PNG, SVG, PDF")
    elif not args.no_figures:
        print(
            "figures_note: matplotlib no está disponible o no había datos suficientes "
            "para figuras."
        )

    print(f"latex_tables_generated: {len(table_paths)}")
    print(f"markdown_summary: {md_path}")
    print("")
    print("status:", "PASS" if all_pass else "CHECK_REQUIRED")

    return 0 if all_pass else 10


if __name__ == "__main__":
    raise SystemExit(main())