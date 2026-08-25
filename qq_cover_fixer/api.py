"""QQ 音乐 API 客户端：搜索歌曲、下载专辑封面。

接口说明（非官方，仅供学习研究）：
- 搜索接口: https://c.y.qq.com/soso/fcgi-bin/client_search_cp
  GET 参数: w=关键词  format=json  p=页  n=条数
  cr=1  g_tk=5381  loginUin=0  hostUin=0  inCharset=utf8
  outCharset=utf-8  notice=0  platform=yqq.json  needNewCode=0
- 封面图片: https://y.gtimg.cn/music/photo_new/T002R500x500M000{albummid}.jpg
  T002R500x500 / T003R1000x1000 分别为不同尺寸。
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

import requests

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
# 尺寸档位: T002 = 500px、T003 = 1000px
COVER_FAMILY = {500: "T002", 1000: "T003"}
COVER_URL_TMPL = "https://y.gtimg.cn/music/photo_new/{family}R{size}x{size}M000{albummid}.jpg"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 搜索结果条数
SEARCH_LIMIT = 5
# 网络超时
REQUEST_TIMEOUT = 20
# 搜索与下载重试次数
MAX_SEARCH_ATTEMPTS = 2
MAX_DOWNLOAD_ATTEMPTS = 4
# 每次 API 请求后的最小间隔（秒），避免被限流
MIN_REQUEST_INTERVAL = 0.35


def _norm(s: str) -> str:
    """归一化文本用于比较：小写、去括号内容、去空白标点。"""
    s = (s or "").lower()
    s = re.sub(r"[（(][^）)]*[)）]", "", s)  # 去掉括号内的附加说明
    s = re.sub(r"[\W_]+", "", s, flags=re.UNICODE)
    return s


def _is_image(data: bytes) -> Optional[str]:
    """根据魔数判断图片类型，返回 mime；不是图片返回 None。"""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class QQMusicClient:
    """QQ 音乐 API 客户端（带请求间隔限速与重试）。"""

    def __init__(self, timeout: int = REQUEST_TIMEOUT, interval: float = MIN_REQUEST_INTERVAL):
        self.timeout = timeout
        self.interval = interval
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._last_request_at = 0.0

    def _pace(self) -> None:
        """保证两次请求之间至少有 interval 秒间隔。"""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, url: str, params: Optional[Dict] = None) -> Optional[requests.Response]:
        self._pace()
        try:
            return self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException:
            return None

    # ------------------------------------------------------------------ #
    # 搜索
    # ------------------------------------------------------------------ #
    def search_song(
        self,
        title: str,
        artist: str = "",
        album: str = "",
        match_title: bool = True,
        match_artist: bool = True,
        match_album: bool = True,
        limit: int = SEARCH_LIMIT,
    ) -> Optional[Dict]:
        """按 歌名/歌手/专辑（维度可由调用方勾选）搜索，返回打分后最优的一首歌。

        返回结构包含: songname / singer(list) / albumname / albummid / songmid。
        找不到匹配返回 None。
        """
        candidates: List[Tuple[int, Dict]] = []
        queries = self._build_queries(title, artist, album,
                                      match_title, match_artist, match_album)
        if not queries:
            return None

        for q in queries:
            results = self._search_raw(q, limit)
            if not results:
                continue
            for item in results:
                sc = _score(item, title, artist, album,
                            match_title, match_artist, match_album)
                if sc is not None and sc > 0:
                    candidates.append((sc, item))
            if candidates:  # 首个有结果的查询已有命中，不再扩大搜索
                break

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _build_queries(
        title: str, artist: str, album: str,
        match_title: bool, match_artist: bool, match_album: bool,
    ) -> List[str]:
        """按启用的匹配维度构造搜索关键词（按优先级排列）。"""
        t = title.strip() if match_title and title else ""
        a = artist.strip() if match_artist and artist else ""
        al = album.strip() if match_album and album else ""

        queries: List[str] = []
        if t and a:
            queries.append(f"{a} {t}")   # 歌手+歌名 最常见
            queries.append(f"{t} {a}")
        parts = [p for p in (t, a, al) if p]
        if parts:
            joined = " ".join(parts)
            if joined not in queries:
                queries.append(joined)
        if not t and not a and al:
            queries.append(al)
        return queries

    def _search_raw(self, query: str, limit: int) -> List[Dict]:
        for _ in range(MAX_SEARCH_ATTEMPTS):
            params = {
                "w": query,
                "format": "json",
                "p": 1,
                "n": limit,
                "cr": 1,
                "g_tk": 5381,
                "loginUin": 0,
                "hostUin": 0,
                "inCharset": "utf8",
                "outCharset": "utf-8",
                "notice": 0,
                "platform": "yqq.json",
                "needNewCode": 0,
            }
            resp = self._get(SEARCH_URL, params)
            if resp is None:
                time.sleep(0.5)
                continue
            try:
                payload = resp.json()
            except ValueError:
                time.sleep(0.5)
                continue
            data = payload.get("data") or {}
            song = data.get("song") or {}
            items = song.get("list") or []
            if items:
                return items
            time.sleep(0.5)
        return []

    # ------------------------------------------------------------------ #
    # 封面下载
    # ------------------------------------------------------------------ #
    def download_cover(
        self, albummid: str, size: int = 500
    ) -> Optional[Tuple[bytes, str]]:
        """下载专辑封面，返回 (图片字节, mime)；失败返回 None。

        CDN 偶发拒连/限流，采用多次退避重试；500/1000 两个尺寸档都试一次。
        """
        sizes = [size]
        alt = 1000 if size == 500 else 500
        if alt in COVER_FAMILY:
            sizes.append(alt)
        for sz in sizes:
            family = COVER_FAMILY.get(sz, "T002")
            url = COVER_URL_TMPL.format(family=family, size=sz, albummid=albummid)
            for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
                resp = self._get(url)
                if resp is not None and resp.status_code == 200:
                    mime = _is_image(resp.content)
                    if mime and len(resp.content) >= 500:
                        return resp.content, mime
                time.sleep(1.0 * (attempt + 1))
        return None


def _score(
    song: Dict,
    title: str,
    artist: str,
    album: str,
    match_title: bool = True,
    match_artist: bool = True,
    match_album: bool = True,
) -> Optional[int]:
    """对搜索结果打分：歌名匹配 3/2 分，歌手匹配 2/1 分，专辑匹配 1 分。

    仅统计用户勾选的维度；不满足命中条件返回 None。
    命中条件随启用的维度自适应：
    - 启用歌名: 歌名必须匹配(>=2) 且总分 >= 3
    - 仅启用歌手: 歌手必须精确匹配且总分 >= 2
    - 仅启用专辑: 专辑匹配且总分 >= 1
    """
    st = _norm(song.get("songname", ""))
    t = _norm(title) if match_title else ""
    singers = song.get("singer") or []
    sa = _norm(singers[0].get("name", "")) if singers else ""
    a = _norm(artist) if match_artist else ""
    sal = _norm(song.get("albumname", ""))
    al = _norm(album) if match_album else ""

    title_score = artist_score = album_score = 0
    if t and st:
        if st == t:
            title_score = 3
        elif st in t or t in st:
            title_score = 2

    if a and sa:
        if sa == a:
            artist_score = 2
        elif sa in a or a in sa:
            artist_score = 1

    if al and sal:
        if sal == al:
            album_score = 1
        elif sal in al or al in sal:
            album_score = 1

    total = title_score + artist_score + album_score

    if match_title and t:
        # 歌名维度启用：歌名必须命中，且总分达标
        return total if (title_score >= 2 and total >= 3) else None
    if match_artist and a:
        return total if (artist_score >= 2 and total >= 2) else None
    if match_album and al:
        return total if (album_score >= 1 and total >= 1) else None
    return None
