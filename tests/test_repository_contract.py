from pathlib import Path

from tfg_sdn.mininet.topos.fattree import build_fattree_spec

ROOT = Path(__file__).resolve().parents[1]


def test_expected_public_documentation_exists():
    required = [
        "README.md",
        ".gitignore",
        ".gitattributes",
        "docs/environment.md",
        "docs/linux_validation.md",
        "docs/script_catalog.md",
        "data/README.md",
        "data/SHA256SUMS",
    ]
    assert all((ROOT / rel).is_file() for rel in required)


def test_k6_structural_formula_as_parameterization_smoke_test():
    spec = build_fattree_spec(6)
    manifest = spec["manifest"]
    assert manifest["core_switches"] == 9
    assert manifest["aggregation_switches"] == 18
    assert manifest["edge_switches"] == 18
    assert manifest["hosts"] == 54
    assert manifest["switches_total"] == 45
    assert manifest["links_total"] == 162
