#!/usr/bin/env bash
# =============================================================================
# adapters/copt/oracles/run-copt.sh — env-shim for the COPT SDP oracle
# =============================================================================
#
# COPT v8.x ships as a tarball under ${COPT_HOME} (default
# /home/tobias/copt80) and is NOT pip-installable on this machine.
# Importing `coptpy` requires:
#
#   1. LD_LIBRARY_PATH includes ${COPT_HOME}/lib AND
#      ${COPT_HOME}/lib/python/deps (the latter holds libcopt_python.so
#      which the cython wrapper dlopens at module load).
#   2. PYTHONPATH includes ${COPT_HOME}/lib/python/<py-major><py-minor>.
#
# This wrapper sets both, then exec's `python3` with the rest of argv.
# The COPT adapter TOML's `cmd`/`args` fields point here so each oracle
# subprocess is launched with a clean environment carrying these paths.
#
# License fallback: COPT searches ${COPT_LICENSE_DIR}, the binary
# directory, and ${HOME}/copt/ for license.dat / license.key. If none
# match it starts in size-limited free mode (n ≤ 2000 PSD dimension).
# v0.1 SDPLIB problems all fit under that cap; explicit removal of any
# pre-existing license file in ${HOME}/copt/ ensures the free path is
# what runs (worklog will document this).

set -euo pipefail

: "${COPT_HOME:=/home/tobias/copt80}"

if [ ! -d "${COPT_HOME}" ]; then
  echo "run-copt.sh: COPT_HOME=${COPT_HOME} does not exist" >&2
  exit 2
fi

PY_MM="$(python3 -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor}")')"
PY_PKG_DIR="${COPT_HOME}/lib/python/${PY_MM}"

if [ ! -d "${PY_PKG_DIR}" ]; then
  echo "run-copt.sh: no coptpy bindings for Python ${PY_MM} at ${PY_PKG_DIR}" >&2
  exit 2
fi

export LD_LIBRARY_PATH="${COPT_HOME}/lib:${COPT_HOME}/lib/python/deps:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="${PY_PKG_DIR}:${PYTHONPATH:-}"

exec python3 "$@"
