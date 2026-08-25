"""音频元信息提取与封面嵌入（基于 mutagen，跨平台）。

支持的格式与封面写入方式:
- mp3  : ID3v2 的 APIC 帧
- flac : FLAC Picture 块
- m4a  : MP4 'covr' 原子
- ogg  : VorbisComment 的 METADATA_BLOCK_PICTURE（Ogg 标准，foobar2000/VLC 等可读）
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis

SUPPORTED_EXTS = {".mp3", ".flac", ".m4a", ".ogg"}
COVER_TYPE_FRONT = 3  # PictureType.front_cover


# ---------------------------------------------------------------------- #
# 元信息提取
# ---------------------------------------------------------------------- #
def _first_text(tags_value) -> str:
    """mutagen 标签值可能是 str / list[str] / bytes，统一取首个字符串。"""
    if tags_value is None:
        return ""
    if isinstance(tags_value, (list, tuple)):
        if not tags_value:
            return ""
        tags_value = tags_value[0]
    if isinstance(tags_value, bytes):
        try:
            return tags_value.decode("utf-8", "ignore")
        except Exception:
            return ""
    return str(tags_value)


def _tags_from_mp4(audio) -> Dict[str, str]:
    tags = audio.tags or {}
    out: Dict[str, str] = {}
    for key, field in (("\xa9nam", "title"), ("\xa9ART", "artist"), ("\xa9alb", "album")):
        out[field] = _first_text(tags.get(key))
    return out


def _tags_from_generic(audio) -> Dict[str, str]:
    tags = audio.tags or {}
    out: Dict[str, str] = {}
    for key, field in (("title", "title"), ("artist", "artist"), ("album", "album")):
        if hasattr(tags, "get"):
            out[field] = _first_text(tags.get(key))
    return out


def extract_metadata(path) -> Optional[Dict[str, str]]:
    """提取 (title, artist, album)。无法解析返回 None。"""
    audio = None
    try:
        audio = MutagenFile(path)
    except Exception:
        audio = None

    # MutagenFile 对个别（如仅含标签、无音频帧）文件可能失败，按扩展名回退
    if audio is None:
        ext = Path(path).suffix.lower()
        try:
            if ext == ".mp3":
                audio = ID3(path)
            elif ext == ".flac":
                audio = FLAC(path)
            elif ext == ".m4a":
                audio = MP4(path)
            elif ext == ".ogg":
                audio = OggVorbis(path)
        except Exception:
            return None
    if audio is None:
        return None

    if isinstance(audio, ID3):
        tags = audio
        meta = {
            "title": _first_text(tags.get("TIT2")),
            "artist": _first_text(tags.get("TPE1")),
            "album": _first_text(tags.get("TALB")),
        }
    elif isinstance(audio, MP4):
        meta = _tags_from_mp4(audio)
    else:
        meta = _tags_from_generic(audio)

    return {k: (v.strip() if isinstance(v, str) else v) for k, v in meta.items()}


def parse_filename_fallback(filename: str) -> Dict[str, str]:
    """根据文件名猜测歌名/歌手。支持 "歌手 - 歌名"、"歌名 - 歌手"、"01 - 歌名" 等。"""
    stem = Path(filename).stem
    # 去掉开头的序号，如 "01 - "、"01. "、"01_"
    stem = re.sub(r"^\s*\d{1,3}\s*[-._]\s*", "", stem)
    parts = re.split(r"\s+-\s+|\s+–\s+", stem, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        # QQ 解密文件常见 "歌名 - 歌手"，取最后一段为歌名
        return {"title": parts[1].strip(), "artist": parts[0].strip(), "album": ""}
    return {"title": stem.strip(), "artist": "", "album": ""}


# ---------------------------------------------------------------------- #
# 封面检测
# ---------------------------------------------------------------------- #
def has_cover(path) -> bool:
    """判断文件是否已内嵌封面。"""
    try:
        ext = Path(path).suffix.lower()
        if ext == ".mp3":
            tags = ID3(path)
            return any(isinstance(tag, APIC) and tag.data for tag in tags.values())
        if ext == ".flac":
            audio = FLAC(path)
            return bool(audio.pictures)
        if ext == ".m4a":
            audio = MP4(path)
            return bool(audio.tags and "covr" in audio.tags)
        if ext == ".ogg":
            audio = OggVorbis(path)
            return bool(audio.tags and "metadata_block_picture" in audio.tags)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------- #
# 封面嵌入
# ---------------------------------------------------------------------- #
def _new_flac_picture(data: bytes, mime: str) -> Picture:
    pic = Picture()
    pic.type = COVER_TYPE_FRONT
    pic.mime = mime
    pic.desc = "Cover (front)"
    pic.data = data
    return pic


def _embed_mp3(path: str, data: bytes, mime: str) -> None:
    tags = ID3(path)
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime=mime, type=COVER_TYPE_FRONT, desc="Cover", data=data))
    tags.save(path)


def _embed_flac(path: str, data: bytes, mime: str) -> None:
    audio = FLAC(path)
    audio.clear_pictures()
    audio.add_picture(_new_flac_picture(data, mime))
    audio.save()


def _embed_m4a(path: str, data: bytes, mime: str) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
    audio.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
    audio.save()


def _embed_ogg(path: str, data: bytes, mime: str) -> None:
    audio = OggVorbis(path)
    if audio.tags is None:
        audio.add_tags()
    pic = _new_flac_picture(data, mime)
    audio.tags["metadata_block_picture"] = [
        base64.b64encode(pic.write()).decode("ascii")
    ]
    audio.save()


def embed_cover(path: str, data: bytes, mime: str = "image/jpeg") -> None:
    """把封面写入音频文件（就地修改目标文件）。"""
    ext = Path(path).suffix.lower()
    if ext == ".mp3":
        _embed_mp3(path, data, mime)
    elif ext == ".flac":
        _embed_flac(path, data, mime)
    elif ext == ".m4a":
        _embed_m4a(path, data, mime)
    elif ext == ".ogg":
        _embed_ogg(path, data, mime)
    else:
        raise ValueError(f"不支持的格式: {ext}")


def get_cover_info(path) -> Optional[Tuple[str, int, int]]:
    """读取已嵌入封面信息，返回 (mime, width, height)；无封面返回 None。"""
    try:
        ext = Path(path).suffix.lower()
        if ext == ".mp3":
            tags = ID3(path)
            for tag in tags.values():
                if isinstance(tag, APIC) and tag.data:
                    return tag.mime, 0, 0
        elif ext == ".flac":
            audio = FLAC(path)
            if audio.pictures:
                p = audio.pictures[0]
                return p.mime, p.width, p.height
        elif ext == ".m4a":
            audio = MP4(path)
            if audio.tags and "covr" in audio.tags:
                cov = audio.tags["covr"][0]
                mime = "image/png" if cov.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
                return mime, 0, 0
        elif ext == ".ogg":
            audio = OggVorbis(path)
            if audio.tags and "metadata_block_picture" in audio.tags:
                return "image/jpeg", 0, 0
    except Exception:
        return None
    return None
