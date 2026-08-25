"""运行历史记录：持久化到 JSON 文件（跨平台，位于用户主目录）。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

HISTORY_DIR = Path(
    os.environ.get("QQCF_HISTORY_DIR") or (Path.home() / ".qmc_cover_fixer")
)
HISTORY_FILE = HISTORY_DIR / "history.json"
MAX_RUNS = 50          # 最多保留的运行次数
MAX_FILES_PER_RUN = 5000  # 单次运行最多存档的文件明细条数


def load_history() -> List[Dict[str, Any]]:
    """读取全部历史运行记录（新的在前）。"""
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_run(run: Dict[str, Any]) -> None:
    """追加一次运行记录并裁剪数量。"""
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        runs = load_history()
        runs.insert(0, run)
        del runs[MAX_RUNS:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def build_run_record(
    source_dir: str,
    target_dir: str,
    files: List[Dict[str, Any]],
    stats: Dict[str, int],
    elapsed: float,
    stopped: bool,
) -> Dict[str, Any]:
    """把一次运行整理成可存档的记录。"""
    return {
        "id": int(time.time() * 1000),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source_dir,
        "target": target_dir,
        "total": len(files),
        "success": stats.get("success", 0),
        "no_cover": stats.get("no_cover", 0),
        "skipped": stats.get("skipped", 0),
        "failed": stats.get("failed", 0),
        "elapsed": round(elapsed, 2),
        "stopped": stopped,
        "files": files[:MAX_FILES_PER_RUN],
    }


def clear_history() -> None:
    """清空历史记录。"""
    try:
        if HISTORY_FILE.exists():
            os.remove(HISTORY_FILE)
    except Exception:
        pass
