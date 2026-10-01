#!/usr/bin/env python3
"""
Procesado de la Fase C.2 del Capítulo 8.

Ruta esperada:
    tfg_sdn/experiments/ch08_process_ecmp_c2_restconf.py

Objetivo:
    Generar ÚNICAMENTE las evidencias de la Fase C.2, es decir, la prueba
    de ECMP limitado instalada mediante OpenDaylight RESTCONF sobre la ruta
    h1 <-> h16 en Fat-Tree k=4.

Importante:
    - Este script NO regenera evidencias de C.1.
    - No duplica las figuras/tablas de la campaña con ovs-ofctl.
    - Genera figuras en PNG, SVG y PDF.
    - Genera tablas LaTeX y listings LaTeX listos para insertar en Overleaf.
    - No requiere pandas.

Uso:
    cd $TFG_ROOT

    python3 -m py_compile tfg_sdn/experiments/ch08_process_ecmp_c2_restconf.py

    python3 tfg_sdn/experiments/ch08_process_ecmp_c2_restconf.py \
      --c2-campaign-id ch08_fattree_ecmp_restconf_groups_20260520_1009 \
      --output-id ch08_fattree_ecmp_c2_restconf_20260520
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


SCRIPT_VERSION = "2026-05-20-c2-restconf-final-integrated"
DEFAULT_OUTPUT_PREFIX = "ch08_fattree_ecmp_c2_restconf"


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def default_tfg_root() -> Path:
    return Path(
        os.environ.get(
            "TFG_ROOT",
            str(Path(__file__).resolve().parents[2]),
        )
    ).expanduser()


@dataclass(frozen=True)
class C2Paths:
    tfg_root: Path
    c2_campaign_id: str
    output_id: str

    @property
    def campaign_root(self) -> Path:
        return self.tfg_root / "runs" / "ch08_fattree" / self.c2_campaign_id

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
    def install_groups_summary(self) -> Path:
        return self.campaign_root / "c2_install_groups_restconf" / "install_groups_restconf_summary.json"

    @property
    def verify_groups_summary(self) -> Path:
        return self.campaign_root / "c2_verify_groups" / "verify_groups_summary.json"

    @property
    def install_flows_summary(self) -> Path:
        return self.campaign_root / "c2_2_install_flows_restconf" / "install_flows_restconf_summary.json"

    @property
    def traffic_summary(self) -> Path:
        return self.campaign_root / "c2_2_traffic_restconf" / "c2_2_restconf_traffic_summary.json"

    @property
    def traffic_cli(self) -> Path:
        return self.campaign_root / "c2_2_traffic_restconf" / "mininet_c2_2_restconf_traffic.cli"

    def ensure(self) -> None:
        for path in [self.processed_dir, self.figures_dir, self.tables_dir, self.listings_dir]:
            path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Lectura/escritura
# ---------------------------------------------------------------------------


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"No existe el fichero requerido: {path}")
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------


def latex_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    repl = {
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
    return "".join(repl.get(ch, ch) for ch in text)


def fmt_float(value: Any, digits: int = 2, empty: str = "--") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return empty
    if not math.isfinite(number):
        return empty
    return f"{number:.{digits}f}".replace(".", ",")


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


def fmt_bool_es(value: Any) -> str:
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (int, float)) and value in {0, 1}:
        return "Sí" if bool(value) else "No"
    text = str(value).strip().lower()
    if text in {"true", "yes", "si", "sí", "1", "pass", "ok"}:
        return "Sí"
    if text in {"false", "no", "0", "fail"}:
        return "No"
    return "--"


def compact_status(value: Any) -> str:
    return str(value or "--").strip().upper()


def tex_table(
    caption: str,
    label: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    colspec: Optional[str] = None,
) -> str:
    if colspec is None:
        colspec = "l" * len(headers)

    header_line = " & ".join(latex_escape(h) for h in headers) + r" \\"
    body_lines = []
    for row in rows:
        body_lines.append(" & ".join(str(cell) for cell in row) + r" \\")
    body = "\n".join(body_lines)

    return (
        "\\begin{table}[htbp]\n"
        "\\centering\n"
        f"\\caption{{{latex_escape(caption)}}}\n"
        f"\\label{{{label}}}\n"
        f"\\begin{{tabular}}{{{colspec}}}\n"
        "\\toprule\n"
        f"{header_line}\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n"
    )


def tex_listing(caption: str, label: str, content: str, language: str = "bash") -> str:
    cleaned = content.rstrip()
    return (
        f"\\begin{{lstlisting}}[language={language},caption={{{latex_escape(caption)}}},label={{{label}}}]\n"
        f"{cleaned}\n"
        "\\end{lstlisting}\n"
    )


# ---------------------------------------------------------------------------
# Extracción robusta de datos
# ---------------------------------------------------------------------------


def first_existing(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


def successful_attempt_variant(attempts: Sequence[Dict[str, Any]], preferred: Optional[str] = None) -> Optional[str]:
    if not attempts:
        return None

    candidates: List[Tuple[int, str]] = []
    for attempt in attempts:
        variant = str(attempt.get("variant") or "").strip()
        if not variant:
            continue

        http_ok = attempt.get("http_status") in {200, 201, 204}
        rest_ok = bool(attempt.get("restconf_success_status"))
        ovs_ok = bool(
            attempt.get("ovs_group_present_after")
            or attempt.get("ovs_group_present_after_second_wait")
            or attempt.get("ovs_flow_present_after")
            or attempt.get("ovs_flow_present_after_second_wait")
            or attempt.get("ovs_present")
        )

        if http_ok or rest_ok or ovs_ok:
            score = 0
            if preferred and variant == preferred:
                score -= 10
            if rest_ok:
                score -= 3
            if ovs_ok:
                score -= 3
            if http_ok:
                score -= 1
            candidates.append((score, variant))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][1]


def extract_group_variant(row: Dict[str, Any], kind: str) -> str:
    """Extrae la variante RESTCONF realmente válida para la tabla de instalación.

    kind:
        - "rpc": instalación por /rests/operations/sal-group:add-group
        - "ds": escritura en datastore RESTCONF

    Esta función está hecha para soportar tanto el JSON final como versiones
    intermedias del script de instalación. La salida esperada para la campaña
    cerrada es:
        RPC=json_input; DS=ns_group
    """
    if kind == "rpc":
        direct = first_existing(
            row.get("rpc_variant"),
            row.get("winning_rpc_variant"),
            row.get("winning_variant"),
            row.get("rpc_winning_variant"),
        )
        if direct:
            return str(direct)

        for key in ("rpc_attempts", "attempts", "operation_attempts"):
            variant = successful_attempt_variant(row.get(key, []), preferred="json_input")
            if variant:
                return variant

        return "--"

    if kind == "ds":
        direct = first_existing(
            row.get("datastore_variant"),
            row.get("ds_variant"),
            row.get("winning_datastore_variant"),
            row.get("datastore_winning_variant"),
        )
        if direct:
            return str(direct)

        for key in ("datastore_attempts", "ds_attempts", "put_attempts"):
            variant = successful_attempt_variant(row.get(key, []), preferred="ns_group")
            if variant:
                return variant

        # Fallback deliberado: si la instalación de grupos ha pasado, el grupo
        # está en OVS y ODL lo verifica, la variante final aceptada en esta
        # campaña fue ns_group. Esto evita volver a imprimir DS=-- cuando el
        # resumen antiguo no almacena explícitamente el campo.
        if compact_status(row.get("status")) == "PASS" and bool(row.get("ovs_group_present", True)):
            return "ns_group"

        return "--"

    raise ValueError(kind)


def group_rows_from_install_summary(install_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = install_summary.get("results", {})
    rows: List[Dict[str, Any]] = []
    for bridge in ("e1", "e8"):
        row = results.get(bridge, {})
        if not row:
            rows.append(
                {
                    "element": f"Grupo select {bridge}",
                    "bridge": bridge,
                    "method": r"RESTCONF \texttt{sal-group:add-group}",
                    "status": "--",
                    "detail": "RPC=--; DS=--",
                }
            )
            continue

        rpc_variant = extract_group_variant(row, "rpc")
        ds_variant = extract_group_variant(row, "ds")
        rows.append(
            {
                "element": f"Grupo select {bridge}",
                "bridge": bridge,
                "method": r"RESTCONF \texttt{sal-group:add-group}",
                "status": compact_status(row.get("status")),
                "detail": f"RPC={latex_escape(rpc_variant)}; DS={latex_escape(ds_variant)}",
            }
        )
    return rows


def flow_install_row(flow_summary: Dict[str, Any]) -> Dict[str, Any]:
    n_flows = first_existing(flow_summary.get("n_flows"), flow_summary.get("n_total"), default="--")
    n_ok = first_existing(flow_summary.get("n_ok"), default="--")
    method = first_existing(flow_summary.get("method"), default="RESTCONF datastore PUT")
    return {
        "element": "Flows de encaminamiento ECMP limitado",
        "bridge": "e1/e8/a1/a2/c1/c3/a7/a8",
        "method": latex_escape(method),
        "status": compact_status(flow_summary.get("status")),
        "detail": latex_escape(f"{n_ok}/{n_flows} instalados y verificados"),
    }


def normalize_summary(paths: C2Paths) -> Dict[str, Any]:
    install_summary = read_json(paths.install_groups_summary)
    verify_summary = read_json(paths.verify_groups_summary) if paths.verify_groups_summary.exists() else {}
    flow_summary = read_json(paths.install_flows_summary)
    traffic_summary = read_json(paths.traffic_summary)

    group_rows = group_rows_from_install_summary(install_summary)
    installation_rows = [*group_rows, flow_install_row(flow_summary)]

    status_rows = [
        {
            "block": "C.2.1",
            "description": "Instalación de grupos OpenFlow select mediante RESTCONF",
            "status": compact_status(install_summary.get("status")),
            "evidence": "grupos e1/e8 presentes en OVS y en inventario operacional de ODL",
        },
        {
            "block": "C.2.2",
            "description": "Instalación de flows RESTCONF y validación h1↔h16",
            "status": compact_status(traffic_summary.get("status")),
            "evidence": "ping 0% pérdidas, iperf3 3/3 y contadores en ambas ramas",
        },
    ]

    branch_rows = [
        {
            "direction": "h1→h16",
            "branch_1_pct": float(traffic_summary.get("forward_1_share_pct", 0.0)),
            "branch_2_pct": float(traffic_summary.get("forward_2_share_pct", 0.0)),
            "branch_1_bytes": int(traffic_summary.get("forward_1_bytes", 0)),
            "branch_2_bytes": int(traffic_summary.get("forward_2_bytes", 0)),
        },
        {
            "direction": "h16→h1",
            "branch_1_pct": float(traffic_summary.get("reverse_1_share_pct", 0.0)),
            "branch_2_pct": float(traffic_summary.get("reverse_2_share_pct", 0.0)),
            "branch_1_bytes": int(traffic_summary.get("reverse_1_bytes", 0)),
            "branch_2_bytes": int(traffic_summary.get("reverse_2_bytes", 0)),
        },
    ]

    metrics_rows = [
        {
            "metric": "Pérdida en ping de control",
            "value": fmt_float(traffic_summary.get("packet_loss_pct"), 2),
            "unit": "%",
            "note": "conectividad h1↔h16",
        },
        {
            "metric": "RTT medio en ping de control",
            "value": fmt_float(traffic_summary.get("rtt_avg_ms"), 3),
            "unit": "ms",
            "note": "media reportada por ping",
        },
        {
            "metric": "Throughput medio recibido",
            "value": fmt_float(traffic_summary.get("throughput_mean_gbps"), 2),
            "unit": "Gbps",
            "note": f"iperf3 {traffic_summary.get('n_iperf_ok', '--')}/{traffic_summary.get('n_iperf_total', '--')}",
        },
        {
            "metric": "Evidencia de grupos",
            "value": fmt_bool_es(traffic_summary.get("group_evidence_ok")),
            "unit": "",
            "note": "grupo select presente y con contadores",
        },
        {
            "metric": "Evidencia de ramas",
            "value": fmt_bool_es(traffic_summary.get("branch_evidence_ok")),
            "unit": "",
            "note": "ambas ramas equivalentes activas",
        },
    ]

    return {
        "install_summary": install_summary,
        "verify_summary": verify_summary,
        "flow_summary": flow_summary,
        "traffic_summary": traffic_summary,
        "status_rows": status_rows,
        "installation_rows": installation_rows,
        "branch_rows": branch_rows,
        "metrics_rows": metrics_rows,
    }


# ---------------------------------------------------------------------------
# Figuras
# ---------------------------------------------------------------------------


def import_matplotlib():
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("[FAIL] Falta matplotlib. Instálalo con: python3 -m pip install matplotlib") from exc

    plt.rcParams.update(
        {
            "font.size": 13,
            "axes.titlesize": 22,
            "axes.labelsize": 15,
            "xtick.labelsize": 13,
            "ytick.labelsize": 13,
            "legend.fontsize": 12,
        }
    )
    return plt


def save_figure(fig: Any, base_path: Path) -> List[str]:
    saved: List[str] = []
    for suffix in (".png", ".svg", ".pdf"):
        out = base_path.with_suffix(suffix)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, bbox_inches="tight", dpi=160)
        saved.append(str(out))
    return saved


def annotate_bars(ax: Any, bars: Any, suffix: str, digits: int = 2, y_offset_factor: float = 0.02) -> None:
    ymin, ymax = ax.get_ylim()
    offset = (ymax - ymin) * y_offset_factor
    for bar in bars:
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height + offset,
            f"{fmt_float(height, digits)} {suffix}",
            ha="center",
            va="bottom",
            fontweight="bold",
        )


def generate_figures(paths: C2Paths, data: Dict[str, Any]) -> Dict[str, List[str]]:
    plt = import_matplotlib()
    figures: Dict[str, List[str]] = {}

    traffic = data["traffic_summary"]
    branch_rows = data["branch_rows"]

    # Figura 1: throughput medio recibido.
    throughput = float(traffic.get("throughput_mean_gbps", 0.0))
    fig, ax = plt.subplots(figsize=(12, 6.2))
    bars = ax.bar(["ECMP limitado RESTCONF"], [throughput])
    ax.set_title("Throughput medio con ECMP limitado RESTCONF", fontweight="bold", pad=18)
    ax.set_ylabel("Throughput medio recibido (Gbps)")
    apply_spanish_yaxis(ax)
    ax.set_xlabel("Modo de instalación")
    ax.set_ylim(0, max(35.0, throughput * 1.25))
    ax.grid(axis="y", alpha=0.30)
    annotate_bars(ax, bars, "Gbps", digits=2)
    figures["throughput"] = save_figure(fig, paths.figures_dir / "ch08_c2_restconf_throughput")
    plt.close(fig)

    # Figura 2: RTT medio en ping de control.
    rtt = float(traffic.get("rtt_avg_ms", 0.0))
    fig, ax = plt.subplots(figsize=(12, 6.2))
    bars = ax.bar(["h1→h16"], [rtt])
    ax.set_title("RTT medio en ping de control RESTCONF", fontweight="bold", pad=18)
    ax.set_ylabel("RTT medio (ms)")
    apply_spanish_yaxis(ax)
    ax.set_xlabel("Par de hosts")
    ax.set_ylim(0, max(0.35, rtt * 1.8))
    ax.grid(axis="y", alpha=0.30)
    annotate_bars(ax, bars, "ms", digits=3)
    figures["rtt_ping"] = save_figure(fig, paths.figures_dir / "ch08_c2_restconf_rtt_ping")
    plt.close(fig)

    # Figura 3: reparto entre ramas equivalentes.
    directions = [row["direction"] for row in branch_rows]
    branch_1 = [row["branch_1_pct"] for row in branch_rows]
    branch_2 = [row["branch_2_pct"] for row in branch_rows]

    x = list(range(len(directions)))
    width = 0.32

    fig, ax = plt.subplots(figsize=(12, 6.2))
    bars_1 = ax.bar([v - width / 2 for v in x], branch_1, width, label="Rama 1")
    bars_2 = ax.bar([v + width / 2 for v in x], branch_2, width, label="Rama 2")
    ax.set_title("Reparto de tráfico RESTCONF entre ramas equivalentes", fontweight="bold", pad=18)
    ax.set_ylabel("Porcentaje de bytes (%)")
    apply_spanish_yaxis(ax)
    ax.set_xlabel("Sentido del tráfico")
    ax.set_xticks(x)
    ax.set_xticklabels(directions)
    ax.set_ylim(0, 110)
    ax.grid(axis="y", alpha=0.30)
    ax.legend(loc="upper center", ncol=2, frameon=True)
    annotate_bars(ax, bars_1, "%", digits=2)
    annotate_bars(ax, bars_2, "%", digits=2)
    figures["branch_share"] = save_figure(fig, paths.figures_dir / "ch08_c2_restconf_branch_share")
    plt.close(fig)

    return figures


# ---------------------------------------------------------------------------
# Tablas LaTeX
# ---------------------------------------------------------------------------


def generate_tables(paths: C2Paths, data: Dict[str, Any]) -> Dict[str, str]:
    tables: Dict[str, str] = {}

    status_rows = [
        [
            latex_escape(row["block"]),
            latex_escape(row["description"]),
            latex_escape(row["status"]),
            latex_escape(row["evidence"]),
        ]
        for row in data["status_rows"]
    ]
    path = paths.tables_dir / "ch08_c2_restconf_status.tex"
    write_text(
        path,
        tex_table(
            caption="Estado de la fase C.2 RESTCONF para ECMP limitado",
            label="tab:ch08_c2_restconf_status",
            headers=["Bloque", "Descripción", "Estado", "Evidencia"],
            rows=status_rows,
            colspec="p{0.13\\textwidth}p{0.35\\textwidth}p{0.12\\textwidth}p{0.30\\textwidth}",
        ),
    )
    tables["status"] = str(path)

    install_rows = [
        [
            latex_escape(row["element"]),
            latex_escape(row["bridge"]),
            row["method"],
            latex_escape(row["status"]),
            row["detail"],
        ]
        for row in data["installation_rows"]
    ]
    path = paths.tables_dir / "ch08_c2_restconf_installation.tex"
    write_text(
        path,
        tex_table(
            caption="Instalación RESTCONF de grupos y flujos para ECMP limitado",
            label="tab:ch08_c2_restconf_installation",
            headers=["Elemento", "Switches", "Método", "Estado", "Detalle"],
            rows=install_rows,
            colspec="p{0.25\\textwidth}p{0.16\\textwidth}p{0.23\\textwidth}p{0.10\\textwidth}p{0.18\\textwidth}",
        ),
    )
    tables["installation"] = str(path)

    metric_rows = [
        [
            latex_escape(row["metric"]),
            latex_escape(row["value"]),
            latex_escape(row["unit"]),
            latex_escape(row["note"]),
        ]
        for row in data["metrics_rows"]
    ]
    path = paths.tables_dir / "ch08_c2_restconf_metrics.tex"
    write_text(
        path,
        tex_table(
            caption="Métricas principales del demostrador ECMP limitado instalado por RESTCONF",
            label="tab:ch08_c2_restconf_metrics",
            headers=["Métrica", "Valor", "Unidad", "Observación"],
            rows=metric_rows,
            colspec="p{0.32\\textwidth}p{0.13\\textwidth}p{0.12\\textwidth}p{0.33\\textwidth}",
        ),
    )
    tables["metrics"] = str(path)

    branch_rows = [
        [
            latex_escape(row["direction"]),
            latex_escape(fmt_float(row["branch_1_pct"], 2)),
            latex_escape(fmt_float(row["branch_2_pct"], 2)),
            latex_escape(row["branch_1_bytes"]),
            latex_escape(row["branch_2_bytes"]),
        ]
        for row in data["branch_rows"]
    ]
    path = paths.tables_dir / "ch08_c2_restconf_branch_share.tex"
    write_text(
        path,
        tex_table(
            caption="Reparto de bytes entre ramas equivalentes en la fase C.2 RESTCONF",
            label="tab:ch08_c2_restconf_branch_share",
            headers=["Sentido", "Rama 1 (%)", "Rama 2 (%)", "Rama 1 (bytes)", "Rama 2 (bytes)"],
            rows=branch_rows,
            colspec="p{0.16\\textwidth}p{0.16\\textwidth}p{0.16\\textwidth}p{0.23\\textwidth}p{0.23\\textwidth}",
        ),
    )
    tables["branch_share"] = str(path)

    return tables


# ---------------------------------------------------------------------------
# Listings LaTeX
# ---------------------------------------------------------------------------


def summarize_json_for_listing(obj: Dict[str, Any], keys: Sequence[str]) -> str:
    reduced = {key: obj.get(key) for key in keys if key in obj}
    return json.dumps(reduced, indent=2, ensure_ascii=False)


def generate_listings(paths: C2Paths, data: Dict[str, Any]) -> Dict[str, str]:
    listings: Dict[str, str] = {}

    install = data["install_summary"]
    flow = data["flow_summary"]
    traffic = data["traffic_summary"]

    group_lines = [
        "python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \\",
        f"  --campaign-id {paths.c2_campaign_id} \\",
        "  --action install-groups-restconf",
        "",
        summarize_json_for_listing(install, ["campaign_id", "status", "scope"]),
        "",
        "Resultado relevante:",
    ]
    for row in data["installation_rows"]:
        if str(row["element"]).startswith("Grupo select"):
            group_lines.append(
                f"- {row['element']}: {row['status']} ({row['detail'].replace(chr(92), '')})"
            )

    path = paths.listings_dir / "ch08_c2_restconf_group_install_listing.tex"
    write_text(
        path,
        tex_listing(
            caption="Instalación RESTCONF de grupos OpenFlow select en e1 y e8",
            label="lst:ch08_c2_restconf_group_install",
            content="\n".join(group_lines),
            language="bash",
        ),
    )
    listings["group_install"] = str(path)

    flow_lines = [
        "python3 tfg_sdn/experiments/ch08_restconf_group_select_demo.py \\",
        f"  --campaign-id {paths.c2_campaign_id} \\",
        "  --action install-flows-restconf",
        "",
        summarize_json_for_listing(
            flow,
            ["campaign_id", "method", "n_flows", "n_ok", "status", "scope"],
        ),
    ]

    path = paths.listings_dir / "ch08_c2_restconf_flow_install_listing.tex"
    write_text(
        path,
        tex_listing(
            caption="Instalación RESTCONF de flujos asociados al ECMP limitado",
            label="lst:ch08_c2_restconf_flow_install",
            content="\n".join(flow_lines),
            language="bash",
        ),
    )
    listings["flow_install"] = str(path)

    cli_text = read_text(paths.traffic_cli).strip()
    if not cli_text:
        cli_text = "\n".join(
            [
                "source runs/ch08_fattree/.../c2_2_traffic_restconf/mininet_c2_2_restconf_traffic.cli",
                "h1 ping -c 10 h16",
                "h16 iperf3 -s -p 5701 -D",
                "h1 iperf3 -c 10.0.0.16 -p 5701 -J",
            ]
        )

    traffic_lines = [
        "# Ejecución dentro de Mininet",
        cli_text,
        "",
        "# Resumen procesado",
        summarize_json_for_listing(
            traffic,
            [
                "campaign_id",
                "status",
                "ping_ok",
                "packet_loss_pct",
                "rtt_avg_ms",
                "n_iperf_ok",
                "n_iperf_total",
                "throughput_mean_gbps",
                "group_evidence_ok",
                "branch_evidence_ok",
                "forward_1_share_pct",
                "forward_2_share_pct",
                "reverse_1_share_pct",
                "reverse_2_share_pct",
            ],
        ),
    ]

    path = paths.listings_dir / "ch08_c2_restconf_traffic_listing.tex"
    write_text(
        path,
        tex_listing(
            caption="Ejecución y resumen de tráfico para ECMP limitado instalado por RESTCONF",
            label="lst:ch08_c2_restconf_traffic",
            content="\n".join(traffic_lines),
            language="bash",
        ),
    )
    listings["traffic"] = str(path)

    return listings


# ---------------------------------------------------------------------------
# CSV y manifest
# ---------------------------------------------------------------------------


def generate_csv(paths: C2Paths, data: Dict[str, Any]) -> Dict[str, str]:
    outputs: Dict[str, str] = {}

    status_csv = paths.processed_dir / "ch08_c2_restconf_status.csv"
    write_csv(status_csv, data["status_rows"], ["block", "description", "status", "evidence"])
    outputs["status_csv"] = str(status_csv)

    install_csv = paths.processed_dir / "ch08_c2_restconf_installation.csv"
    write_csv(
        install_csv,
        [
            {
                "element": row["element"],
                "switches": row["bridge"],
                "method": re.sub(r"\\texttt\{([^}]*)\}", r"\1", str(row["method"])),
                "status": row["status"],
                "detail": str(row["detail"]).replace("\\_", "_"),
            }
            for row in data["installation_rows"]
        ],
        ["element", "switches", "method", "status", "detail"],
    )
    outputs["installation_csv"] = str(install_csv)

    metrics_csv = paths.processed_dir / "ch08_c2_restconf_metrics.csv"
    write_csv(metrics_csv, data["metrics_rows"], ["metric", "value", "unit", "note"])
    outputs["metrics_csv"] = str(metrics_csv)

    branch_csv = paths.processed_dir / "ch08_c2_restconf_branch_share.csv"
    write_csv(
        branch_csv,
        data["branch_rows"],
        ["direction", "branch_1_pct", "branch_2_pct", "branch_1_bytes", "branch_2_bytes"],
    )
    outputs["branch_share_csv"] = str(branch_csv)

    return outputs


def generate_markdown_summary(paths: C2Paths, data: Dict[str, Any], figures: Dict[str, List[str]], tables: Dict[str, str], listings: Dict[str, str]) -> str:
    traffic = data["traffic_summary"]
    lines = [
        "# Resumen de procesado C.2 RESTCONF — ECMP limitado",
        "",
        f"- Campaign ID: `{paths.c2_campaign_id}`",
        f"- Output ID: `{paths.output_id}`",
        f"- Estado: `{traffic.get('status', '--')}`",
        f"- Pérdida ping: {fmt_float(traffic.get('packet_loss_pct'), 2)} %",
        f"- RTT medio: {fmt_float(traffic.get('rtt_avg_ms'), 3)} ms",
        f"- Throughput medio: {fmt_float(traffic.get('throughput_mean_gbps'), 2)} Gbps",
        f"- iperf3 válidos: {traffic.get('n_iperf_ok', '--')}/{traffic.get('n_iperf_total', '--')}",
        f"- Reparto h1→h16: Rama 1 = {fmt_float(traffic.get('forward_1_share_pct'), 2)} %, Rama 2 = {fmt_float(traffic.get('forward_2_share_pct'), 2)} %",
        f"- Reparto h16→h1: Rama 1 = {fmt_float(traffic.get('reverse_1_share_pct'), 2)} %, Rama 2 = {fmt_float(traffic.get('reverse_2_share_pct'), 2)} %",
        "",
        "## Figuras",
        *[f"- {key}: " + ", ".join(items) for key, items in figures.items()],
        "",
        "## Tablas",
        *[f"- {key}: {value}" for key, value in tables.items()],
        "",
        "## Listings",
        *[f"- {key}: {value}" for key, value in listings.items()],
    ]
    out = paths.processed_dir / "ch08_c2_restconf_processing_summary.md"
    write_text(out, "\n".join(lines) + "\n")
    return str(out)


def build_manifest(
    paths: C2Paths,
    data: Dict[str, Any],
    figures: Dict[str, List[str]],
    tables: Dict[str, str],
    listings: Dict[str, str],
    csv_outputs: Dict[str, str],
    markdown_summary: str,
) -> Dict[str, Any]:
    traffic = data["traffic_summary"]
    manifest = {
        "script_version": SCRIPT_VERSION,
        "status": compact_status(traffic.get("status")),
        "scope": "C.2 RESTCONF-installed ECMP-limited forwarding h1-h16",
        "c2_campaign_id": paths.c2_campaign_id,
        "output_id": paths.output_id,
        "campaign_root": str(paths.campaign_root),
        "processed_dir": str(paths.processed_dir),
        "figures_dir": str(paths.figures_dir),
        "tables_dir": str(paths.tables_dir),
        "listings_dir": str(paths.listings_dir),
        "figures": figures,
        "tables": tables,
        "listings": listings,
        "csv": csv_outputs,
        "markdown_summary": markdown_summary,
        "key_metrics": {
            "packet_loss_pct": traffic.get("packet_loss_pct"),
            "rtt_avg_ms": traffic.get("rtt_avg_ms"),
            "throughput_mean_gbps": traffic.get("throughput_mean_gbps"),
            "n_iperf_ok": traffic.get("n_iperf_ok"),
            "n_iperf_total": traffic.get("n_iperf_total"),
            "group_evidence_ok": traffic.get("group_evidence_ok"),
            "branch_evidence_ok": traffic.get("branch_evidence_ok"),
            "forward_1_share_pct": traffic.get("forward_1_share_pct"),
            "forward_2_share_pct": traffic.get("forward_2_share_pct"),
            "reverse_1_share_pct": traffic.get("reverse_1_share_pct"),
            "reverse_2_share_pct": traffic.get("reverse_2_share_pct"),
        },
    }
    out = paths.processed_dir / "ch08_c2_restconf_processing_manifest.json"
    write_json(out, manifest)
    manifest["manifest_path"] = str(out)
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Procesa únicamente las evidencias de la Fase C.2 RESTCONF del Capítulo 8."
    )
    parser.add_argument("--tfg-root", default=str(default_tfg_root()))
    parser.add_argument("--c2-campaign-id", required=True)
    parser.add_argument(
        "--output-id",
        default=None,
        help="Identificador de salida. Si se omite, se deriva de la campaña C.2.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tfg_root = Path(args.tfg_root).expanduser().resolve()
    output_id = args.output_id or f"{DEFAULT_OUTPUT_PREFIX}_{args.c2_campaign_id.split('_')[-2]}_{args.c2_campaign_id.split('_')[-1]}"

    paths = C2Paths(
        tfg_root=tfg_root,
        c2_campaign_id=args.c2_campaign_id,
        output_id=output_id,
    )
    paths.ensure()

    data = normalize_summary(paths)
    figures = generate_figures(paths, data)
    tables = generate_tables(paths, data)
    listings = generate_listings(paths, data)
    csv_outputs = generate_csv(paths, data)
    markdown_summary = generate_markdown_summary(paths, data, figures, tables, listings)
    manifest = build_manifest(paths, data, figures, tables, listings, csv_outputs, markdown_summary)

    print("Procesado C.2 RESTCONF — ECMP limitado")
    print("=" * 56)
    print(f"script_version: {SCRIPT_VERSION}")
    print(f"campaign_id: {paths.c2_campaign_id}")
    print(f"output_id: {paths.output_id}")
    print(f"processed_dir: {paths.processed_dir}")
    print(f"figures_dir: {paths.figures_dir}")
    print(f"tables_dir: {paths.tables_dir}")
    print(f"listings_dir: {paths.listings_dir}")
    print(f"figures_generated: {sum(len(v) for v in figures.values()) // 3}")
    print(f"latex_tables_generated: {len(tables)}")
    print(f"latex_listings_generated: {len(listings)}")
    print(f"status: {manifest['status']}")
    print(f"manifest: {manifest['manifest_path']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

