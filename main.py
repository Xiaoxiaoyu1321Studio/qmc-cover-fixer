#!/usr/bin/env python3
"""QQ 音乐封面修复器 - 入口。

用法:
    python main.py                 # 启动图形界面
    python main.py --cli -s 源目录 -t 目标目录   # 命令行模式（无界面）
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="QQ 音乐封面修复器")
    parser.add_argument("--cli", action="store_true", help="命令行模式（不启动界面）")
    parser.add_argument("-s", "--source", default="", help="源目录")
    parser.add_argument("-t", "--target", default="", help="目标目录（默认=源目录）")
    parser.add_argument("--no-skip", action="store_true", help="不跳过已有封面的文件")
    parser.add_argument("--no-fallback", action="store_true", help="不使用文件名推断")
    parser.add_argument("--no-copy-on-fail", action="store_true", help="封面失败时不复制")
    parser.add_argument("--no-title", action="store_true", help="匹配维度不含歌名")
    parser.add_argument("--no-artist", action="store_true", help="匹配维度不含歌手")
    parser.add_argument("--no-album", action="store_true", help="匹配维度不含专辑")
    parser.add_argument("--size", type=int, default=500, choices=[500, 1000], help="封面尺寸")
    parser.add_argument("--interval", type=float, default=0.35, help="API 请求间隔（秒）")
    args = parser.parse_args()

    if not args.cli:
        from qq_cover_fixer.gui import run_app
        return run_app()

    if not args.source:
        print("错误：CLI 模式需要 -s/--source 指定源目录", file=sys.stderr)
        return 2

    from qq_cover_fixer.worker import FixWorker, RunOptions, STATUS_LABELS

    options = RunOptions(
        source_dir=args.source,
        target_dir=args.target or args.source,
        skip_with_cover=not args.no_skip,
        use_filename_fallback=not args.no_fallback,
        copy_on_fail=not args.no_copy_on_fail,
        match_title=not args.no_title,
        match_artist=not args.no_artist,
        match_album=not args.no_album,
        cover_size=args.size,
        request_interval=args.interval,
    )

    class _Cli:
        def __init__(self) -> None:
            self.results = []
            self.stats = None

        def on_file(self, result) -> None:
            self.results.append(result)
            print(f"[{STATUS_LABELS.get(result.status, result.status):<8}] "
                  f"{result.rel_path}  {result.log}")

        def on_log(self, text: str) -> None:
            print(text)

        def on_finished(self, summary: dict) -> None:
            self.stats = summary

    cli = _Cli()
    worker = FixWorker(options)
    worker.file_done.connect(cli.on_file)
    worker.log.connect(cli.on_log)
    worker.run_finished.connect(cli.on_finished)
    worker.run()  # 直接同步执行

    st = cli.stats.get("stats", {}) if cli.stats else {}
    print("=" * 60)
    print(f"处理完成：成功 {st.get('success', 0)}，无封面 {st.get('no_cover', 0)}，"
          f"跳过 {st.get('skipped', 0)}，失败 {st.get('failed', 0)}，"
          f"耗时 {cli.stats.get('elapsed', 0):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
