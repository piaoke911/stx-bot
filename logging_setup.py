# ===================== logging_setup.py =====================
"""
logging_setup.py
================
统一日志配置：控制台 + 文件（含轮转）。

特性：
- 控制台输出：彩色/简洁格式，方便交互查看。
- 文件输出：按大小轮转，保留 N 个历史文件。
- 支持通过环境变量 LOG_DIR / LOG_MAX_BYTES / LOG_BACKUP_COUNT 调整。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
CONSOLE_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"


def setup_logging(
    level: str = "INFO",
    log_dir: Optional[str] = None,
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
) -> logging.Logger:
    """
    初始化全局日志。

    参数：
        level: 日志级别字符串
        log_dir: 日志目录，默认 logs/
        max_bytes: 单文件最大字节，默认 10 MB
        backup_count: 保留历史文件数，默认 7
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    log_dir = log_dir or os.getenv("LOG_DIR", "logs")
    max_bytes = max_bytes or int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    backup_count = backup_count or int(os.getenv("LOG_BACKUP_COUNT", "7"))

    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # 清理已有 handler，避免重复添加（尤其是反复调用时）
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    formatter = logging.Formatter(LOG_FORMAT)

    # 1) 控制台
    console = logging.StreamHandler()
    console.setLevel(numeric_level)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root_logger.addHandler(console)

    # 2) 主日志文件
    main_file = RotatingFileHandler(
        filename=str(Path(log_dir) / "stx_bot.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    main_file.setLevel(numeric_level)
    main_file.setFormatter(formatter)
    root_logger.addHandler(main_file)

    # 3) 错误日志单独文件
    err_file = RotatingFileHandler(
        filename=str(Path(log_dir) / "error.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    err_file.setLevel(logging.ERROR)
    err_file.setFormatter(formatter)
    root_logger.addHandler(err_file)

    logger = logging.getLogger("stx_quant")
    logger.setLevel(numeric_level)
    logger.info("日志系统初始化完成 | 级别=%s | 目录=%s", level.upper(), log_dir)
    return logger
