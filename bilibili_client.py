"""
Bilibili API 客户端 - 弹幕发送核心模块
支持：获取视频信息、发送弹幕、时间段分布发送、定时发送
"""

import re
import time
import json
import random
import requests


class BilibiliClient:
    """Bilibili API 客户端"""

    # 弹幕模式
    DM_MODE_SCROLL = 1   # 滚动
    DM_MODE_TOP = 4      # 顶部
    DM_MODE_BOTTOM = 5   # 底部

    # 预设颜色 (RGB 转 int)
    PRESET_COLORS = {
        "白色": 16777215,
        "红色": 65536,
        "蓝色": 16711680,
        "绿色": 65280,
        "黄色": 6553600,
        " cyan": 16776960,
        "粉色": 65535,
        "橙色": 255,
    }

    def __init__(self, sessdata: str = "", bili_jct: str = ""):
        """
        初始化客户端

        Args:
            sessdata: B站 SESSDATA cookie 值
            bili_jct: B站 bili_jct cookie 值 (CSRF token)
        """
        self.sessdata = sessdata
        self.bili_jct = bili_jct
        self.session = requests.Session()
        self._update_cookies()

    def set_credentials(self, sessdata: str, bili_jct: str):
        """更新认证信息"""
        self.sessdata = sessdata
        self.bili_jct = bili_jct
        self._update_cookies()

    def _update_cookies(self):
        """更新 session cookies"""
        self.session.cookies.set("SESSDATA", self.sessdata, domain=".bilibili.com")
        self.session.cookies.set("bili_jct", self.bili_jct, domain=".bilibili.com")

    @staticmethod
    def extract_bvid(url_or_bvid: str) -> str:
        """
        从各种格式的 URL 或直接输入中提取 BV ID

        支持格式:
        - https://www.bilibili.com/video/BV1xx411c7mD
        - https://www.bilibili.com/video/BV1xx411c7mD/
        - https://www.bilibili.com/video/BV1xx411c7mD?t=10
        - https://b23.tv/xxxxxxx (短链接，需要重定向)
        - BV1xx411c7mD
        """
        url_or_bvid = url_or_bvid.strip()

        # 直接是 BV ID
        match = re.match(r'^(BV[a-zA-Z0-9]{10})$', url_or_bvid)
        if match:
            return match.group(1)

        # 从 URL 中提取
        match = re.search(r'(BV[a-zA-Z0-9]{10})', url_or_bvid)
        if match:
            return match.group(1)

        # 短链接 b23.tv - 需要跟随重定向获取真实 URL
        if 'b23.tv' in url_or_bvid:
            try:
                resp = requests.get(url_or_bvid, allow_redirects=True, timeout=10)
                match = re.search(r'(BV[a-zA-Z0-9]{10})', resp.url)
                if match:
                    return match.group(1)
            except Exception:
                pass

        return None

    def get_video_info(self, bvid: str) -> dict:
        """
        获取视频信息

        Returns:
            {
                "bvid": "BV...",
                "aid": 12345,
                "title": "视频标题",
                "cid": 67890,
                "cover": "https://...",
                "owner": {"name": "UP主名", "mid": 123},
                "duration": 600,
            }
        """
        url = "https://api.bilibili.com/x/web-interface/view"
        params = {"bvid": bvid}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": f"https://www.bilibili.com/video/{bvid}",
        }

        resp = self.session.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()

        if data["code"] != 0:
            raise ValueError(f"获取视频信息失败: {data.get('message', '未知错误')} (code={data['code']})")

        d = data["data"]
        return {
            "bvid": d["bvid"],
            "aid": d["aid"],
            "title": d["title"],
            "cid": d["cid"],
            "cover": d.get("pic", ""),
            "owner_name": d.get("owner", {}).get("name", ""),
            "owner_mid": d.get("owner", {}).get("mid", 0),
            "duration": d.get("duration", 0),
        }

    def send_danmaku(
        self,
        bvid: str,
        text: str,
        mode: int = 1,
        color: int = 16777215,
        fontsize: int = 25,
        pool: int = 0,
        progress: int = None,
    ) -> dict:
        """
        发送弹幕到指定视频

        Args:
            bvid: 视频 BV ID
            text: 弹幕文本
            mode: 弹幕模式 (1=滚动, 4=顶部, 5=底部)
            color: 弹幕颜色 (RGB int, 如 16777215=白色)
            fontsize: 字号 (25=标准)
            pool: 弹幕池 (0=普通)
            progress: 视频进度(秒), 默认为 1

        Returns:
            {"success": True/False, "message": "...", "data": {...}}
        """
        if not self.sessdata or not self.bili_jct:
            return {
                "success": False,
                "message": "缺少认证信息，请先设置 SESSDATA 和 bili_jct",
            }

        # 获取视频 cid
        video_info = self.get_video_info(bvid)
        cid = video_info["cid"]

        if progress is None:
            progress = 1

        url = "https://api.bilibili.com/x/v2/dm/post"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": f"https://www.bilibili.com/video/{bvid}",
            "Origin": "https://www.bilibili.com",
        }

        payload = {
            "type": 1,
            "oid": cid,
            "msg": text,
            "bvid": bvid,
            "progress": progress,
            "color": color,
            "fontsize": fontsize,
            "pool": pool,
            "mode": mode,
            "rnd": int(time.time()),
            "csrf": self.bili_jct,
        }

        resp = self.session.post(url, data=payload, headers=headers, timeout=10)
        data = resp.json()

        if data["code"] == 0:
            return {
                "success": True,
                "message": "弹幕发送成功！",
                "data": {
                    "bvid": bvid,
                    "title": video_info["title"],
                    "text": text,
                    "mode": mode,
                    "color": color,
                    "progress": progress,
                },
            }
        else:
            return {
                "success": False,
                "message": f"发送失败: {data.get('message', '未知错误')} (code={data['code']})",
                "data": {"code": data["code"]},
            }

    def send_danmaku_batch(
        self,
        bvid: str,
        messages: list,
        mode: int = 1,
        color: int = 16777215,
        interval: float = 2.0,
    ) -> list:
        """
        批量发送弹幕

        Args:
            bvid: 视频 BV ID
            messages: 弹幕文本列表
            mode: 弹幕模式
            color: 弹幕颜色
            interval: 每条弹幕间隔(秒)，避免发送过快被限制

        Returns:
            发送结果列表
        """
        results = []
        for i, msg in enumerate(messages):
            result = self.send_danmaku(bvid, msg, mode=mode, color=color)
            result["index"] = i + 1
            result["total"] = len(messages)
            results.append(result)

            if i < len(messages) - 1:
                time.sleep(interval)

        return results

    def send_danmaku_timed_batch(
        self,
        bvid: str,
        messages: list,
        mode: int = 1,
        color: int = 16777215,
        time_start: int = 0,
        time_end: int = 60,
        distribution: str = "even",
        send_interval: float = 2.0,
    ) -> list:
        """
        在指定视频时间段内分布发送弹幕

        每条弹幕会被分配到 time_start ~ time_end 之间的一个视频进度位置，
        实现「指定时间段发送」的效果。

        Args:
            bvid: 视频 BV ID
            messages: 弹幕文本列表
            mode: 弹幕模式
            color: 弹幕颜色
            time_start: 时间段起始（秒）
            time_end: 时间段结束（秒）
            distribution: 分布模式
                - "even": 均匀分布
                - "random": 随机分布
                - "sequence": 顺序递增
            send_interval: 实际发送间隔（秒），防频率限制

        Returns:
            发送结果列表，每条包含分配到的 progress
        """
        count = len(messages)
        if count == 0:
            return []

        # 确保 time_end > time_start
        if time_end <= time_start:
            time_end = time_start + count

        results = []

        for i, msg in enumerate(messages):
            # 计算这条弹幕的视频进度位置
            if distribution == "even":
                if count == 1:
                    progress = time_start
                else:
                    step = (time_end - time_start) / (count - 1)
                    progress = int(time_start + step * i)
            elif distribution == "random":
                progress = random.randint(time_start, time_end)
            else:  # sequence
                progress = time_start + i
                if progress > time_end:
                    progress = time_end

            result = self.send_danmaku(
                bvid=bvid,
                text=msg,
                mode=mode,
                color=color,
                progress=progress,
            )
            result["index"] = i + 1
            result["total"] = count
            result["progress"] = progress
            result["progress_str"] = self.format_time(progress)
            results.append(result)

            if i < count - 1:
                time.sleep(send_interval)

        return results

    @staticmethod
    def parse_time_string(time_str: str) -> int:
        """
        将时间字符串解析为秒数

        支持格式:
        - "90" → 90 秒
        - "1:30" → 90 秒
        - "01:30" → 90 秒
        - "1:02:30" → 3750 秒 (1小时2分30秒)
        """
        time_str = str(time_str).strip()
        if not time_str:
            return 0

        # 纯数字 → 秒
        if time_str.isdigit():
            return int(time_str)

        # 含冒号 → 分:秒 或 时:分:秒
        parts = time_str.split(":")
        try:
            parts = [int(p) for p in parts]
        except ValueError:
            return 0

        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            return 0

    @staticmethod
    def format_time(seconds: int) -> str:
        """将秒数格式化为 mm:ss 或 h:mm:ss"""
        if seconds < 0:
            seconds = 0
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def check_login_status(self) -> dict:
        """检查登录状态"""
        if not self.sessdata:
            return {"logged_in": False, "message": "未设置 SESSDATA"}

        url = "https://api.bilibili.com/x/web-interface/nav"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        }

        resp = self.session.get(url, headers=headers, timeout=10)
        data = resp.json()

        if data["code"] == 0 and data.get("data", {}).get("isLogin"):
            user = data["data"]
            return {
                "logged_in": True,
                "uid": user.get("mid"),
                "username": user.get("uname"),
                "level": user.get("level_info", {}).get("current_level", 0),
                "vip": user.get("vipStatus", 0),
                "message": f"已登录: {user.get('uname')} (UID: {user.get('mid')})",
            }
        else:
            return {
                "logged_in": False,
                "message": f"未登录或 SESSDATA 已失效: {data.get('message', '')}",
            }
