# QQ 音乐封面修复器 (qmc-cover-fixer)

> 💡 本项目由 **DeepSeek Harness** 开发。

为 QQ 音乐解密后丢失专辑图的本地音频（mp3 / flac / m4a / ogg）自动补回封面：

**提取元信息 → 查询 QQ 音乐 API → 下载专辑封面 → 复制到目标目录并嵌入封面**
即使封面下载失败/未找到，也会照常复制文件。

## 功能

- 🔍 递归扫描目录，自动提取内嵌元信息（歌名 / 歌手 / 专辑）；无元信息时可用文件名推断
- ☑️ 匹配维度可勾选：歌名 / 歌手 / 专辑（控制检索与比对字段）
- 🎨 封面尺寸可选 500×500 或 1000×1000；CDN 偶发拒连时自动退避重试
- 📋 结果列表逐条显示：状态（成功 / 已复制无封面 / 跳过 / 失败）、匹配封面、耗时、**日志列**；支持右键打开/定位文件、导出 CSV
- 📊 统计分析：成功 / 失败统计卡片 + 状态分布柱状图 + 历史趋势图
- 🕘 历史记录：历次运行自动存档（含明细），可查看、清空、打开历史文件
- 🌗 基于 PyQt5 + qfluentwidgets 的 Fluent Design 界面，自动适配明暗主题
- 🖥️ 跨平台（Windows / macOS / Linux），另附 CLI 模式便于脚本化

## 安装

```bash
# Python 3.8+
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 使用

```bash
python main.py                   # 启动图形界面
```

命令行模式：

```bash
python main.py --cli -s 源目录 -t 目标目录 [--size 1000] [--no-album]
# 完整参数见: python main.py --help
```

## 关于 QQ 音乐接口

非官方接口（仅用于学习研究，请勿滥用）：

| 用途 | 接口 |
| --- | --- |
| 搜索歌曲 | `https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w=关键词&format=json&p=1&n=5&cr=1&g_tk=5381&loginUin=0&hostUin=0&inCharset=utf8&outCharset=utf-8&notice=0&platform=yqq.json&needNewCode=0` |
| 封面图片 | `https://y.gtimg.cn/music/photo_new/T002R500x500M000{albummid}.jpg`（T002=500px，T003=1000px） |

搜索结果按 歌名/歌手/专辑 打分匹配，取最优结果，其 `albummid` 用于拼装封面 URL。

## 封面写入方式（mutagen）

| 格式 | 方式 |
| --- | --- |
| mp3 | ID3v2 `APIC` 帧 |
| flac | FLAC `Picture` 块 |
| m4a | MP4 `covr` 原子 |
| ogg | VorbisComment `METADATA_BLOCK_PICTURE`（Ogg 标准，foobar2000 / VLC 等可显示） |

## 打包（可选）

```bash
pip install pyinstaller
pyinstaller -w -n QQ音乐封面修复器 main.py --collect-all qfluentwidgets
```

## 目录结构

```
main.py                    入口（GUI + CLI）
qq_cover_fixer/
  api.py                   QQ 音乐 API（搜索 / 封面下载，含限速与重试）
  audio.py                 元信息提取、封面检测与嵌入（mutagen）
  worker.py                后台处理线程（QThread）
  history.py               历史记录持久化（~/.qmc_cover_fixer/history.json）
  gui.py                   PyQt5 + qfluentwidgets 界面
requirements.txt
```
