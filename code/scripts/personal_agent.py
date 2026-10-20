#!/usr/bin/env python3
"""
MAPAS CLI Product Entry Point.

Usage:
    python3 code/scripts/personal_agent.py
"""

from __future__ import annotations

import os
import sys
import argparse
import logging
from logging.handlers import RotatingFileHandler

# Ensure the code root is on sys.path for direct script execution.
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(CODE_ROOT)
if CODE_ROOT not in sys.path:
    sys.path.insert(0, CODE_ROOT)

from src.memory.app.console import PersonalAgentApp
from src.memory.app.storage import (
    AgentStorageLayout,
    default_agent_data_dir,
    migrate_legacy_config,
    migrate_legacy_project_data,
)
from src.memory.core.diagnostics import log_boundary_failure


logger = logging.getLogger(__name__)


def _configure_product_logging(data_dir: str) -> str:
    """Configure bounded persistent diagnostics for the CLI-owned process."""
    absolute_dir = os.path.abspath(data_dir)
    layout = AgentStorageLayout(absolute_dir)
    log_path = layout.log_path
    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    for logger_name in ("src.memory", __name__):
        target = logging.getLogger(logger_name)
        target.setLevel(logging.INFO)
        target.addHandler(handler)
    return log_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="启动 MAPAS API 驱动的交互式终端。"
    )
    parser.add_argument("--execution-mode", choices=("single", "multi_fixed"), default="single",
                        help="single baseline or P1 serial read-only collaboration")
    args = parser.parse_args()

    # Make direct absolute-path launches behave like the repository launch
    # script while keeping runtime data and configuration in the global home.
    os.chdir(REPO_ROOT)

    # MemoryConfig owns dotenv loading and preserves the documented precedence:
    # process environment > local-only .env.local > defaults.
    data_dir = default_agent_data_dir()
    if "AGENT_DATA_DIR" not in os.environ and "AGENT_HOME" not in os.environ:
        migrate_legacy_project_data(os.path.join(REPO_ROOT, ".agent_data"), data_dir)
    config_path = os.path.join(data_dir, "config", ".env.local")
    if "AGENT_DATA_DIR" not in os.environ:
        migrate_legacy_config(os.path.join(REPO_ROOT, ".env.local"), config_path)
    log_path = _configure_product_logging(data_dir)
    try:
        app = PersonalAgentApp(data_dir=data_dir, execution_mode=args.execution_mode)
    except RuntimeError as err:
        metadata = log_boundary_failure(logger, "personal_agent_startup_failed", err)
        print(
            f"\n[启动失败] 初始化 MAPAS 失败 "
            f"({metadata['error_type']})，详见 {log_path}。"
        )
        sys.exit(1)
    except Exception as err:
        metadata = log_boundary_failure(logger, "personal_agent_startup_failed", err)
        print(f"\n[启动失败] 初始化 MAPAS 发生异常 ({metadata['error_type']})，详见 {log_path}。")
        sys.exit(1)

    # Enter interactive product loop
    app.run_interactive_loop()


if __name__ == "__main__":
    main()
