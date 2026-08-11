"""
B站弹幕 Agent - Flask 后端服务（多用户无状态版）
每个请求携带用户凭证，服务端不存储任何状态。
支持 CORS，可部署到 Render / Railway / Fly.io 等平台。
"""

import time
import random
import re
import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # 允许所有来源跨域访问


# ========== 工具函数 ==========

def extract_bvid(url_or_bvid: str) -> str:
    """从 URL 或直接输入中提取 BV ID"""
    url_or_bvid = (url_or_bvid or "").strip()

    match = re.match(r'^(BV[a-zA-Z0-9]{10})$', url_or_bvid)
    if match:
        return match.group(1)

    match = re.search(r'(BV[a-zA-Z0-9]{10})', url_or_bvid)
    if match:
        return match.group(1)

    if 'b23.tv' in url_or_bvid:
        try:
            resp = requests.get(url_or_bvid, allow_redirects=True, timeout=10)
            match = re.search(r'(BV[a-zA-Z0-9]{10})', resp.url)
            if match:
                return match.group(1)
        except Exception:
            pass

    return None


def parse_time_string(time_str: str) -> int:
    """将时间字符串解析为秒数，支持 "90" / "1:30" / "1:02:30" """
    time_str = str(time_str or "").strip()
    if not time_str:
        return 0
    if time_str.isdigit():
        return int(time_str)
    parts = time_str.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


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


def make_session(sessdata: str, bili_jct: str) -> requests.Session:
    """创建带认证的 requests Session"""
    session = requests.Session()
    session.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")
    session.cookies.set("bili_jct", bili_jct, domain=".bilibili.com")
    return session


def get_video_info(bvid: str, session: requests.Session = None) -> dict:
    """获取视频信息"""
    own_session = session is None
    if own_session:
        session = requests.Session()

    url = "https://api.bilibili.com/x/web-interface/view"
    params = {"bvid": bvid}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": f"https://www.bilibili.com/video/{bvid}",
    }

    resp = session.get(url, params=params, headers=headers, timeout=10)
    data = resp.json()

    if own_session:
        session.close()

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


def send_danmaku_api(
    sessdata: str,
    bili_jct: str,
    bvid: str,
    text: str,
    mode: int = 1,
    color: int = 16777215,
    fontsize: int = 25,
    pool: int = 0,
    progress: int = 1,
    retry_on_rate_limit: bool = False,
    retry_on_time_exceed: bool = True,
    max_retries: int = 2,
    rate_limit_wait: float = 10.0,
) -> dict:
    """发送弹幕到指定视频

    Args:
        retry_on_rate_limit: 遇到频率限制(36703)时是否自动等待重试
        retry_on_time_exceed: 遇到时间越界(36714)时是否自动用 progress=1 重试一次
        max_retries: 最大重试次数
        rate_limit_wait: 遇到限流时等待秒数
    """
    if not sessdata or not bili_jct:
        return {"success": False, "message": "缺少认证信息，请先设置 SESSDATA 和 bili_jct"}

    session = make_session(sessdata, bili_jct)
    try:
        video_info = get_video_info(bvid, session)
        cid = video_info["cid"]
        video_duration = video_info.get("duration", 0)

        # 自动限制 progress 不超过视频时长（留 2 秒余量，更保守）
        if video_duration > 0 and progress >= video_duration:
            progress = max(1, video_duration - 2)

        url = "https://api.bilibili.com/x/v2/dm/post"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": f"https://www.bilibili.com/video/{bvid}",
            "Origin": "https://www.bilibili.com",
        }

        # B站 API 的 progress 参数单位为毫秒，需要将秒转换为毫秒
        progress_ms = int(progress) * 1000

        # 标记是否已因 36714 重试过（只重试一次）
        _retried_36714 = False
        _original_progress = progress

        payload = {
            "type": 1,
            "oid": cid,
            "msg": text,
            "bvid": bvid,
            "progress": progress_ms,
            "color": color,
            "fontsize": fontsize,
            "pool": pool,
            "mode": mode,
            "rnd": int(time.time()),
            "csrf": bili_jct,
        }

        for attempt in range(max_retries + 1):
            resp = session.post(url, data=payload, headers=headers, timeout=10)
            data = resp.json()

            if data["code"] == 0:
                return {
                    "success": True,
                    "message": "弹幕发送成功！" + (" (已自动重试)" if _retried_36714 else ""),
                    "data": {
                        "bvid": bvid,
                        "title": video_info["title"],
                        "text": text,
                        "mode": mode,
                        "color": color,
                        "progress": progress,
                    },
                }

            # 频率限制 (code=36703) 且允许重试
            if data["code"] == 36703 and retry_on_rate_limit and attempt < max_retries:
                time.sleep(rate_limit_wait)
                payload["rnd"] = int(time.time())  # 刷新 rnd
                continue

            # 时间越界 (code=36714) 且允许重试且还没重试过
            # B站 API 有时对接近视频末尾的进度返回 36714，
            # 用最安全的 progress=1 (1000ms) 重试一次
            if data["code"] == 36714 and retry_on_time_exceed and not _retried_36714:
                _retried_36714 = True
                progress = 1
                progress_ms = 1000
                payload["progress"] = progress_ms
                payload["rnd"] = int(time.time())
                continue

            # 其他错误或重试次数用完
            if data["code"] == 36703:
                msg = f"发送频率过快，B站限制了发送 (code=36703)。请等待 1-2 分钟后再试"
            elif data["code"] == 36714:
                dur_str = f"视频时长 {video_duration} 秒" if video_duration else "未知视频时长"
                msg = (f"弹幕时间点异常 (code=36714)。{dur_str}，"
                       f"原始进度 {_original_progress} 秒({int(_original_progress)*1000}ms)，"
                       f"已尝试用 1 秒重试仍失败")
            else:
                msg = f"发送失败: {data.get('message', '未知错误')} (code={data['code']})"
            return {
                "success": False,
                "message": msg,
                "data": {"code": data["code"]},
            }

        return {"success": False, "message": "重试次数已用完", "data": {}}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        session.close()


# ========== API 路由 ==========

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/login-check", methods=["POST"])
def check_login():
    """检查 B站登录状态"""
    data = request.json or {}
    sessdata = data.get("sessdata", "").strip()
    bili_jct = data.get("bili_jct", "").strip()

    if not sessdata:
        return jsonify({"logged_in": False, "message": "未设置 SESSDATA"})

    session = make_session(sessdata, bili_jct)
    try:
        url = "https://api.bilibili.com/x/web-interface/nav"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        }
        resp = session.get(url, headers=headers, timeout=10)
        data = resp.json()

        if data["code"] == 0 and data.get("data", {}).get("isLogin"):
            user = data["data"]
            return jsonify({
                "logged_in": True,
                "uid": user.get("mid"),
                "username": user.get("uname"),
                "level": user.get("level_info", {}).get("current_level", 0),
                "vip": user.get("vipStatus", 0),
                "message": f"已登录: {user.get('uname')} (UID: {user.get('mid')})",
            })
        else:
            return jsonify({
                "logged_in": False,
                "message": f"未登录或 SESSDATA 已失效: {data.get('message', '')}",
            })
    except Exception as e:
        return jsonify({"logged_in": False, "message": f"请求失败: {str(e)}"})
    finally:
        session.close()


@app.route("/api/video-info", methods=["POST"])
def video_info():
    """获取视频信息"""
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "message": "请提供视频链接或 BV ID"})

    bvid = extract_bvid(url)
    if not bvid:
        return jsonify({"success": False, "message": "无法解析 BV ID，请检查链接格式"})

    try:
        info = get_video_info(bvid)
        return jsonify({"success": True, "data": info})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/send", methods=["POST"])
def send_single():
    """发送单条弹幕"""
    data = request.json or {}
    sessdata = data.get("sessdata", "").strip()
    bili_jct = data.get("bili_jct", "").strip()
    url = data.get("url", "").strip()
    text = data.get("text", "").strip()
    mode = int(data.get("mode", 1))
    color = int(data.get("color", 16777215))
    progress_raw = data.get("progress", "1")

    if isinstance(progress_raw, str) and ":" in progress_raw:
        progress = parse_time_string(progress_raw)
    else:
        progress = int(progress_raw) if progress_raw else 1

    if not url or not text:
        return jsonify({"success": False, "message": "视频链接和弹幕内容不能为空"})

    bvid = extract_bvid(url)
    if not bvid:
        return jsonify({"success": False, "message": "无法解析 BV ID"})

    result = send_danmaku_api(sessdata, bili_jct, bvid, text, mode, color, progress=progress,
                              retry_on_rate_limit=True, max_retries=2, rate_limit_wait=10.0)
    return jsonify(result)


@app.route("/api/send-batch", methods=["POST"])
def send_batch():
    """批量发送弹幕（同步，逐条发送）"""
    data = request.json or {}
    sessdata = data.get("sessdata", "").strip()
    bili_jct = data.get("bili_jct", "").strip()
    url = data.get("url", "").strip()
    messages = data.get("messages", [])
    mode = int(data.get("mode", 1))
    color = int(data.get("color", 16777215))
    interval = float(data.get("interval", 2.0))

    if not url or not messages:
        return jsonify({"success": False, "message": "视频链接和弹幕内容不能为空"})

    bvid = extract_bvid(url)
    if not bvid:
        return jsonify({"success": False, "message": "无法解析 BV ID"})

    results = []
    for i, msg in enumerate(messages):
        result = send_danmaku_api(sessdata, bili_jct, bvid, msg, mode, color, progress=1,
                                  retry_on_rate_limit=True, max_retries=2, rate_limit_wait=10.0)
        result["index"] = i + 1
        result["total"] = len(messages)
        results.append(result)
        if i < len(messages) - 1:
            time.sleep(interval)

    success_count = sum(1 for r in results if r["success"])
    return jsonify({
        "success": True,
        "message": f"批量发送完成: {success_count}/{len(results)} 成功",
        "results": results,
    })


@app.route("/api/send-timed", methods=["POST"])
def send_timed():
    """在指定视频时间段内分布发送弹幕"""
    data = request.json or {}
    sessdata = data.get("sessdata", "").strip()
    bili_jct = data.get("bili_jct", "").strip()
    url = data.get("url", "").strip()
    messages = data.get("messages", [])
    mode = int(data.get("mode", 1))
    color = int(data.get("color", 16777215))
    distribution = data.get("distribution", "even")
    send_interval = float(data.get("send_interval", 2.0))

    time_start = parse_time_string(data.get("time_start", "0:00"))
    time_end = parse_time_string(data.get("time_end", "1:00"))

    if not url or not messages:
        return jsonify({"success": False, "message": "视频链接和弹幕内容不能为空"})

    if time_end <= time_start:
        return jsonify({"success": False, "message": "结束时间必须大于起始时间"})

    bvid = extract_bvid(url)
    if not bvid:
        return jsonify({"success": False, "message": "无法解析 BV ID"})

    count = len(messages)
    results = []

    for i, msg in enumerate(messages):
        if distribution == "even":
            if count == 1:
                progress = time_start
            else:
                step = (time_end - time_start) / (count - 1)
                progress = int(time_start + step * i)
        elif distribution == "random":
            progress = random.randint(time_start, time_end)
        else:
            progress = min(time_start + i, time_end)

        result = send_danmaku_api(sessdata, bili_jct, bvid, msg, mode, color, progress=progress,
                                  retry_on_rate_limit=True, max_retries=2, rate_limit_wait=10.0)
        result["index"] = i + 1
        result["total"] = count
        result["progress"] = progress
        result["progress_str"] = format_time(progress)
        results.append(result)

        if i < count - 1:
            time.sleep(send_interval)

    success_count = sum(1 for r in results if r["success"])
    return jsonify({
        "success": True,
        "message": f"时间段发送完成: {success_count}/{len(results)} 成功 "
                   f"({format_time(time_start)} ~ {format_time(time_end)})",
        "time_range": {
            "start": time_start,
            "end": time_end,
            "start_str": format_time(time_start),
            "end_str": format_time(time_end),
            "distribution": distribution,
        },
        "results": results,
    })


@app.route("/api/colors", methods=["GET"])
def get_colors():
    """获取预设颜色列表"""
    return jsonify({
        "colors": {
            "白色": 16777215, "红色": 65536, "蓝色": 16711680, "绿色": 65280,
            "黄色": 6553600, "青色": 16776960, "粉色": 65535, "橙色": 255,
        }
    })


@app.route("/api/health", methods=["GET"])
def health():
    """健康检查端点"""
    return jsonify({"status": "ok", "service": "bili-danmaku-agent"})


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
