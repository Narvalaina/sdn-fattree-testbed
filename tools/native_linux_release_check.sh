#!/usr/bin/env bash
# Portable/native-Linux preflight for the curated SDN Fat-Tree repository.
# It does not start Mininet or OpenDaylight experiments. Those remain manual gates.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${1:-/tmp/sdn-fattree-native-check-${STAMP}}"
mkdir -p "$OUT"
LOG="$OUT/preflight.log"
SUMMARY="$OUT/summary.txt"

PASS=0
FAIL=0
SKIP=0

exec > >(tee -a "$LOG") 2>&1

record() {
  local status="$1"; shift
  printf '%-5s %s\n' "$status" "$*" | tee -a "$SUMMARY" >/dev/null
  case "$status" in
    PASS) PASS=$((PASS+1));;
    FAIL) FAIL=$((FAIL+1));;
    SKIP) SKIP=$((SKIP+1));;
  esac
}

run_check() {
  local label="$1"; shift
  echo
  echo "=== $label ==="
  if "$@"; then
    record PASS "$label"
  else
    record FAIL "$label"
  fi
}

cd "$ROOT"
echo "Native Linux release preflight"
echo "Repository: $ROOT"
echo "Output:     $OUT"
echo "Started:    $(date --iso-8601=seconds 2>/dev/null || date)"

{
  echo "# Environment"
  echo "date=$(date --iso-8601=seconds 2>/dev/null || date)"
  echo "pwd=$PWD"
  echo "user=${USER:-}"
  echo "kernel=$(uname -r 2>/dev/null || true)"
  echo "python=$(python3 --version 2>&1 || true)"
  echo "ovs_vsctl=$(ovs-vsctl --version 2>&1 | head -n 1 || true)"
  echo "ovs_ofctl=$(ovs-ofctl --version 2>&1 | head -n 1 || true)"
  echo "mininet=$(mn --version 2>&1 | head -n 1 || true)"
  echo "java=$(java -version 2>&1 | head -n 1 || true)"
  echo "iperf3=$(iperf3 --version 2>&1 | head -n 1 || true)"
  echo "git=$(git --version 2>&1 || true)"
} > "$OUT/environment.txt"
cat "$OUT/environment.txt"

# Clean Python caches before the public audit because compileall/pytest create them.
find . -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf .pytest_cache
run_check "Public release audit before runtime outputs" python3 tools/release_audit.py
run_check "Python compileall" python3 -m compileall -q tfg_sdn tools tests
run_check "tfgctl help" python3 tfgctl --help

if python3 -c 'import pytest' >/dev/null 2>&1; then
  run_check "Pytest portable suite" python3 -m pytest -q
else
  record SKIP "Pytest portable suite (pytest not installed)"
fi

TMP_META="$OUT/fattree_k4_generated"
rm -rf "$TMP_META"
run_check "Fat-Tree k=4 metadata generation" \
  python3 -m tfg_sdn.mininet.topos.fattree --k 4 --export --outdir "$TMP_META"

for f in \
  ch06_fattree_k4_manifest.json \
  ch06_fattree_k4_nodes.csv \
  ch06_fattree_k4_links.csv \
  ch06_fattree_k4_hostmap.csv \
  ch06_fattree_k4_ports.csv; do
  if cmp -s "data/topology/fattree_k4/$f" "$TMP_META/$f"; then
    record PASS "metadata byte-identity: $f"
  else
    record FAIL "metadata byte-identity: $f"
  fi
done

# Exercise doctor because it is part of the public CLI. Move its generated
# machine-specific evidence outside the repository immediately afterwards.
rm -rf runs
if python3 tfgctl doctor; then
  latest="$(find runs -mindepth 1 -maxdepth 1 -type d -name '*_doctor' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
  if [[ -n "${latest:-}" && -d "$latest" ]]; then
    cp -a "$latest" "$OUT/doctor"
  fi
  rm -rf runs
  record PASS "tfgctl doctor"
else
  rm -rf runs
  record FAIL "tfgctl doctor"
fi

# Runtime checks above create caches; remove them before the final public audit.
find . -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf .pytest_cache
run_check "Public release audit after runtime cleanup" python3 tools/release_audit.py

if command -v ss >/dev/null 2>&1; then
  echo
  echo "=== OpenDaylight port visibility (informational) ==="
  ss -lnt 2>/dev/null | grep -E ':(8181|6653)\b' | tee "$OUT/odl_ports.txt" || true
  if [[ -s "$OUT/odl_ports.txt" ]]; then
    record PASS "ODL ports visible now (informational)"
  else
    record SKIP "ODL ports not listening during portable preflight"
  fi
fi

{
  echo
  echo "PASS=$PASS"
  echo "FAIL=$FAIL"
  echo "SKIP=$SKIP"
  if [[ "$FAIL" -eq 0 ]]; then
    echo "PRECHECK=PASS"
  else
    echo "PRECHECK=FAIL"
  fi
  echo "Evidence directory: $OUT"
} | tee -a "$SUMMARY"

cat "$SUMMARY"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
fi
exit 1
