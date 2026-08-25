"""后台处理线程：扫描目录 → 提取元信息 → 查询封面 → 复制并嵌入。

处理策略：
1. 扫描源目录下所有 mp3/flac/m4a/ogg；
2. 提取内嵌元信息，缺失时用文件名推断；
3. 调用 QQ 音乐 API 搜索并下载封面；
4. 复制源文件到目标目录（保留相对目录结构），
   下载到封面则嵌入目标副本；
5. 即使封面下载失败/未找到，也照常复制文件；
6. 每个文件一条结果记录（含日志），供界面实时展示与历史存档。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from .api import QQMusicClient
from .audio import (
    SUPPORTED_EXTS,
    embed_cover,
    extract_metadata,
    has_cover,
    parse_filename_fallback,
)

# 结果状态
ST_SUCCESS = "success"      # 成功：已复制并嵌入封面
ST_NO_COVER = "no_cover"    # 已复制，但封面未找到/下载失败
ST_SKIPPED = "skipped"      # 跳过：原文件已有封面
ST_FAILED = "failed"        # 失败：复制或写入出错

STATUS_LABELS = {
    ST_SUCCESS: "成功",
    ST_NO_COVER: "已复制(无封面)",
    ST_SKIPPED: "跳过(已有封面)",
    ST_FAILED: "失败",
}


@dataclass
class FileResult:
    """单个文件的处理结果。"""

    rel_path: str              # 相对源目录的路径（也用于目标目录）
    status: str = ST_FAILED
    cover_name: str = ""       # 匹配到的歌曲/封面信息
    duration: float = 0.0      # 处理耗时（秒）
    log: str = ""              # 日志信息
    src_meta: Dict[str, str] = field(default_factory=dict)


@dataclass
class RunOptions:
    """一次运行的选项。"""

    source_dir: str
    target_dir: str
    skip_with_cover: bool = True        # 跳过已有封面的文件
    use_filename_fallback: bool = True  # 无元信息时用文件名推断
    copy_on_fail: bool = True           # 封面失败时也复制文件
    match_title: bool = True            # 匹配维度：歌名
    match_artist: bool = True           # 匹配维度：歌手
    match_album: bool = True            # 匹配维度：专辑
    cover_size: int = 500               # 封面尺寸 500 / 1000
    request_interval: float = 0.35      # API 请求间隔（秒）


class FixWorker(QThread):
    """后台处理线程。通过信号与界面通信，绝不直接操作控件。"""

    file_done = pyqtSignal(object)      # FileResult
    progress = pyqtSignal(int, int)     # (已完成, 总数)
    log = pyqtSignal(str)               # 全局日志
    run_finished = pyqtSignal(dict)     # 汇总统计

    def __init__(self, options: RunOptions, parent=None):
        super().__init__(parent)
        self.options = options
        self._stop_flag = False
        self._files: List[Path] = []

    def stop(self) -> None:
        self._stop_flag = True

    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: C901
        opt = self.options
        started = time.time()
        t0 = time.monotonic()

        self._files = self._scan()
        total = len(self._files)
        self.log.emit(f"扫描完成：共发现 {total} 个音频文件")
        if total == 0:
            summary = self._summary(started, 0, None, 0.0, False)
            self.run_finished.emit(summary)
            return

        client = QQMusicClient(interval=opt.request_interval)
        stats = {ST_SUCCESS: 0, ST_NO_COVER: 0, ST_SKIPPED: 0, ST_FAILED: 0}
        done = 0
        stopped = False
        for src in self._files:
            if self._stop_flag:
                stopped = True
                self.log.emit("用户停止处理。")
                break
            result = self._process_one(src, client)
            stats[result.status] += 1
            done += 1
            self.file_done.emit(result)
            self.progress.emit(done, total)
        if not stopped:
            self.log.emit("全部处理完成。")

        summary = self._summary(started, done, stats, time.monotonic() - t0, stopped)
        self.run_finished.emit(summary)

    # ------------------------------------------------------------------ #
    def _scan(self) -> List[Path]:
        root = Path(self.options.source_dir)
        files: List[Path] = []
        if not root.is_dir():
            return files
        for p in sorted(root.rglob("*")):
            if self._stop_flag:
                break
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                files.append(p)
        return files

    def _process_one(self, src: Path, client: QQMusicClient) -> FileResult:  # noqa: C901
        opt = self.options
        t0 = time.monotonic()
        rel = src.relative_to(Path(opt.source_dir)).as_posix()
        result = FileResult(rel_path=rel)
        result.src_meta = extract_metadata(src) or {}

        # 1) 已有封面且勾选跳过
        if opt.skip_with_cover and has_cover(src):
            result.status = ST_SKIPPED
            result.log = "原文件已内嵌封面，跳过"
            result.duration = time.monotonic() - t0
            return result

        # 2) 组装查询信息
        title = (result.src_meta.get("title") or "").strip()
        artist = (result.src_meta.get("artist") or "").strip()
        album = (result.src_meta.get("album") or "").strip()
        if not title and opt.use_filename_fallback:
            fallback = parse_filename_fallback(src.name)
            result.log = "无内嵌元信息，使用文件名推断"
            if fallback.get("title"):
                title = fallback["title"]
            if not artist and fallback.get("artist"):
                artist = fallback["artist"]

        if not title:
            copied = self._copy(src)
            if copied is not None and opt.copy_on_fail:
                result.status = ST_NO_COVER
                result.log = "无法提取歌名，已复制原文件"
            else:
                result.status = ST_FAILED
                result.log = "无法提取歌名" if copied is not None else f"复制失败: {copied}"
            result.duration = time.monotonic() - t0
            return result

        # 3) 查询并下载封面
        cover_bytes: Optional[bytes] = None
        cover_mime = "image/jpeg"
        query_note = f"查询：{title}"
        if artist:
            query_note += f" / {artist}"
        if album:
            query_note += f" / {album}"
        self.log.emit(f"[{rel}] {query_note}")
        try:
            song = client.search_song(
                title, artist, album,
                match_title=opt.match_title,
                match_artist=opt.match_artist,
                match_album=opt.match_album,
            )
            if song:
                albummid = song.get("albummid") or ""
                songname = song.get("songname") or title
                singer = (song.get("singer") or [{}])[0].get("name") or artist
                if albummid:
                    cov = client.download_cover(albummid, opt.cover_size)
                    if cov:
                        cover_bytes, cover_mime = cov
                        result.cover_name = f"{songname} - {singer}"
            if not cover_bytes:
                self.log.emit(f"[{rel}] 未找到可用封面")
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"[{rel}] 查询异常: {exc}")

        # 4) 复制到目标目录（即使封面失败也复制）
        target = self._copy(src)
        if target is None:
            result.status = ST_FAILED
            result.log = f"复制失败: {self._copy_error}"
            result.duration = time.monotonic() - t0
            return result

        # 5) 嵌入封面
        if cover_bytes:
            try:
                embed_cover(str(target), cover_bytes, cover_mime)
                result.status = ST_SUCCESS
                result.log = f"已嵌入封面（{cover_mime.split('/')[-1]}，{opt.cover_size}px）"
            except Exception as exc:  # noqa: BLE001
                if opt.copy_on_fail:
                    result.status = ST_NO_COVER
                    result.log = f"封面嵌入失败，已保留副本: {exc}"
                else:
                    result.status = ST_FAILED
                    result.log = f"封面嵌入失败: {exc}"
        else:
            if opt.copy_on_fail:
                result.status = ST_NO_COVER
                result.log = "已复制，但未获取到封面"
            else:
                result.status = ST_FAILED
                result.log = "未获取到封面，且未开启失败复制"

        result.duration = time.monotonic() - t0
        return result

    # ------------------------------------------------------------------ #
    def _copy(self, src: Path) -> Optional[Path]:
        """复制 src 到目标目录（保留相对结构），成功返回目标路径，失败返回 None。"""
        try:
            rel = src.relative_to(Path(self.options.source_dir))
            target = Path(self.options.target_dir) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            return target
        except Exception as exc:  # noqa: BLE001
            self._copy_error = str(exc)
            return None

    def _copy_error(self) -> str:
        return getattr(self, "_copy_error", "未知错误")

    @staticmethod
    def _summary(started, done, stats=None, elapsed=0.0, stopped=False) -> dict:
        stats = stats or {ST_SUCCESS: 0, ST_NO_COVER: 0, ST_SKIPPED: 0, ST_FAILED: 0}
        return {
            "started": started,
            "elapsed": elapsed,
            "done": done,
            "stats": dict(stats),
            "stopped": stopped,
        }
