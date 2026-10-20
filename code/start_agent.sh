#!/usr/bin/env bash
set -euo pipefail

MAPAS_CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAPAS_REPO_DIR="$(cd "${MAPAS_CODE_DIR}/.." && pwd)"

if ! command -v conda >/dev/null 2>&1; then
    echo "[启动失败] 未找到 Conda。请先安装 Miniconda/Anaconda。" >&2
    exit 1
fi

MAPAS_CONDA_BASE="$(conda info --base)"
MAPAS_PYTHON="${MAPAS_CONDA_BASE}/envs/mapas/bin/python"
if [[ ! -x "${MAPAS_PYTHON}" && -x "${MAPAS_CONDA_BASE}/envs/apaas/bin/python" ]]; then
    MAPAS_PYTHON="${MAPAS_CONDA_BASE}/envs/apaas/bin/python"
    echo "[提示] 未找到 mapas 环境，正在使用兼容环境启动。" >&2
fi

if [[ ! -x "${MAPAS_PYTHON}" ]]; then
    echo "[启动失败] 未找到 mapas 环境。请先运行：" >&2
    echo "  conda env create -f ${MAPAS_REPO_DIR}/environment.yml" >&2
    exit 1
fi

cd "${MAPAS_REPO_DIR}"
export PYTHONNOUSERSITE=1
export PYTHONPATH=
exec "${MAPAS_PYTHON}" "${MAPAS_CODE_DIR}/scripts/personal_agent.py" "$@"
