#!/usr/bin/env bash
set -uo pipefail

OUT=${1:?output file required}
{
  echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "hostname=$(hostname -f 2>/dev/null || hostname)"
  echo
  echo '===== uname ====='
  uname -a || true
  echo
  echo '===== lscpu ====='
  lscpu || true
  echo
  echo '===== numactl -H ====='
  numactl -H 2>&1 || true
  echo
  echo '===== free -h ====='
  free -h || true
  echo
  echo '===== selected CPU flags ====='
  grep -m1 '^flags' /proc/cpuinfo 2>/dev/null | tr ' ' '\n' | grep -E 'amx|avx512|avx2' | sort -u || true
  echo
  echo '===== vllm ====='
  command -v vllm || true
  vllm --version 2>&1 || true
  echo
  echo '===== python ====='
  python3 --version || true
} > "$OUT"
