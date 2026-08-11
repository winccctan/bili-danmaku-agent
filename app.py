"""
B站弹幕 Agent - Flask 后端服务（多用户无状态版）
每个请求携带用户凭证，服务端不存储任何状态。
支持 CORS，可部署到 Render / Railway / Fly.io 等平台。
"""

import time
import random
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_time_str(ts):
    """将 Unix 时间戳格式化为北京时间字符串"""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

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
    pages = []
    for p in d.get("pages", []):
        pages.append({
            "cid": p["cid"],
            "page": p.get("page", 1),
            "part": p.get("part", ""),
            "duration": p.get("duration", 0),
        })
    return {
        "bvid": d["bvid"],
        "aid": d["aid"],
        "title": d["title"],
        "cid": d["cid"],
        "cover": d.get("pic", ""),
        "owner_name": d.get("owner", {}).get("name", ""),
        "owner_mid": d.get("owner", {}).get("mid", 0),
        "duration": d.get("duration", 0),
        "pages": pages,
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
    page: int = 1,
    retry_on_rate_limit: bool = False,
    max_retries: int = 2,
    rate_limit_wait: float = 10.0,
) -> dict:
    """发送弹幕到指定视频，严格使用用户指定的时间点，绝不自动调整

    36703(频率限制) 会等待后重试，progress 永远不变。
    36714(时间越界) 直接返回不重试，让前端快速跳过继续下一条。
    """
    if not sessdata or not bili_jct:
        return {"success": False, "message": "缺少认证信息，请先设置 SESSDATA 和 bili_jct"}

    session = make_session(sessdata, bili_jct)
    try:
        video_info = get_video_info(bvid, session)
        pages = video_info.get("pages", [])

        # 根据 page 参数选择对应的分P
        if pages and len(pages) > 1:
            target_page = None
            for p in pages:
                if p["page"] == page:
                    target_page = p
                    break
            if not target_page:
                target_page = pages[0]
            cid = target_page["cid"]
            video_duration = target_page.get("duration", 0)
            page_title = target_page.get("part", f"P{page}")
        else:
            cid = video_info["cid"]
            video_duration = video_info.get("duration", 0)
            page_title = ""

        # 发送前预检：如果时间点超过当前分P时长，直接返回，不发请求
        if video_duration > 0 and progress > video_duration:
            page_hint = f"，当前分P: {page_title}" if page_title else ""
            return {
                "success": False,
                "message": (f"时间点 {format_time(progress)}({progress}秒) 超过当前分P时长 "
                            f"{format_time(video_duration)}({video_duration}秒){page_hint}。"
                            f"请检查是否选错了分P，或减小时间点"),
                "data": {"code": 36714, "video_duration": video_duration, "progress": progress, "page": page},
            }

        url = "https://api.bilibili.com/x/v2/dm/post"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": f"https://www.bilibili.com/video/{bvid}",
            "Origin": "https://www.bilibili.com",
        }

        # B站 API 的 progress 参数单位为毫秒，需要将秒转换为毫秒
        progress_ms = int(progress) * 1000

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

        # 统一重试：36703 和 36714 都重试，但绝不改变 progress
        for attempt in range(max_retries + 1):
            resp = session.post(url, data=payload, headers=headers, timeout=10)
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
                        "page": page,
                        "page_title": page_title,
                    },
                }

            # 36714(时间越界) 直接返回，不重试（重试同样时间没意义）
            if data["code"] == 36714:
                dur_str = f"当前分P时长 {format_time(video_duration)}({video_duration}秒)" if video_duration else "未知时长"
                page_hint = f"，当前分P: {page_title}" if page_title else ""
                return {
                    "success": False,
                    "message": (f"B站返回时间越界 (code=36714)。你设的时间点 {format_time(progress)}({progress}秒)，"
                                f"{dur_str}{page_hint}。请检查是否选错了分P，或时间点超过该分P的实际时长"),
                    "data": {"code": 36714, "video_duration": video_duration, "progress": progress, "page": page, "page_title": page_title},
                }

            # 频率限制 (36703)：等待后用同样的时间重试
            if data["code"] == 36703 and retry_on_rate_limit and attempt < max_retries:
                time.sleep(rate_limit_wait)
                payload["rnd"] = int(time.time())
                continue

            # 重试用完或其他错误
            if data["code"] == 36703:
                msg = f"发送频率过快，B站限制了发送 (code=36703)。请等待 1-2 分钟后再试"
            elif data["code"] == 36714:
                msg = f"弹幕时间点 {format_time(progress)}({progress}秒) 被B站拒绝 (code=36714)"
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


def _read_varint(data: bytes, offset: int) -> tuple:
    """读取 protobuf varint，返回 (value, new_offset)"""
    result = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, offset


def _parse_danmaku_protobuf(data: bytes) -> list:
    """解析 B站 seg.so protobuf 弹幕数据

    DanmakuElem 结构:
      field 1 (varint): id
      field 2 (varint): progress (毫秒)
      field 3 (varint): mode
      field 4 (varint): fontsize
      field 5 (varint): color
      field 6 (string): midHash
      field 7 (string): content
      field 8 (varint): ctime
      field 9 (varint): weight
      field 10 (string): action
      field 11 (varint): pool
      field 12 (string): idStr
    """
    items = []
    offset = 0
    while offset < len(data):
        # 读外层 tag (field 1, wire type 2 = length-delimited)
        tag, offset = _read_varint(data, offset)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 2:
            # length-delimited
            length, offset = _read_varint(data, offset)
            elem_data = data[offset:offset + length]
            offset += length

            if field_num == 1:
                # 这是一个 DanmakuElem
                item = _parse_single_danmaku(elem_data)
                if item:
                    items.append(item)
        elif wire_type == 0:
            # varint - skip
            _, offset = _read_varint(data, offset)
        elif wire_type == 5:
            # 32-bit - skip
            offset += 4
        elif wire_type == 1:
            # 64-bit - skip
            offset += 8
        else:
            break

    return items


def _parse_single_danmaku(data: bytes) -> dict:
    """解析单个 DanmakuElem"""
    result = {
        "id": 0,
        "progress": 0,  # 毫秒
        "mode": 1,
        "fontsize": 25,
        "color": 16777215,
        "midHash": "",
        "content": "",
        "ctime": 0,
        "pool": 0,
        "idStr": "",
    }
    offset = 0
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:
            # varint
            value, offset = _read_varint(data, offset)
            if field_num == 1:
                result["id"] = value
            elif field_num == 2:
                result["progress"] = value  # 毫秒
            elif field_num == 3:
                result["mode"] = value
            elif field_num == 4:
                result["fontsize"] = value
            elif field_num == 5:
                result["color"] = value
            elif field_num == 8:
                result["ctime"] = value
            elif field_num == 9:
                pass  # weight, 忽略
            elif field_num == 11:
                result["pool"] = value
        elif wire_type == 2:
            # length-delimited
            length, offset = _read_varint(data, offset)
            value = data[offset:offset + length]
            offset += length
            try:
                text = value.decode("utf-8")
            except UnicodeDecodeError:
                text = value.decode("utf-8", errors="replace")
            if field_num == 6:
                result["midHash"] = text
            elif field_num == 7:
                result["content"] = text
            elif field_num == 10:
                pass  # action, 忽略
            elif field_num == 12:
                result["idStr"] = text
        elif wire_type == 5:
            offset += 4
        elif wire_type == 1:
            offset += 8
        else:
            break

    return result


@app.route("/api/danmaku-list", methods=["POST"])
def danmaku_list():
    """获取视频弹幕列表，验证弹幕是否发送成功"""
    data = request.json or {}
    url = data.get("url", "").strip()
    sessdata = data.get("sessdata", "").strip()
    if not url:
        return jsonify({"success": False, "message": "请提供视频链接或 BV ID"})

    bvid = extract_bvid(url)
    if not bvid:
        return jsonify({"success": False, "message": "无法解析 BV ID"})

    try:
        info = get_video_info(bvid)
        page = int(data.get("page", 1))
        pages = info.get("pages", [])

        # 根据分P选择对应的 CID 和 duration
        if pages and len(pages) > 1:
            target_page = None
            for p in pages:
                if p["page"] == page:
                    target_page = p
                    break
            if not target_page:
                target_page = pages[0]
            cid = target_page["cid"]
            duration = target_page.get("duration", 0)
        else:
            cid = info["cid"]
            duration = info.get("duration", 0)

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": f"https://www.bilibili.com/video/{bvid}",
        }
        # 带上 SESSDATA 可以获取更完整/最新的弹幕
        cookies = {}
        if sessdata:
            cookies["SESSDATA"] = sessdata

        mode_map = {1: "滚动", 4: "顶部", 5: "底部", 6: "逆向", 7: "高级", 8: "代码", 9: "BAS"}
        danmaku_items = []

        # 使用新的 protobuf API (seg.so)，每个 segment 覆盖 6 分钟
        # 根据视频时长计算需要的 segment 数量，拉取全部 segment
        num_segments = max(1, (duration // 360) + 1) if duration > 0 else 1

        for seg_idx in range(1, num_segments + 1):
            # 加 cache-bust 参数避免 CDN 缓存
            dm_url = f"https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid={cid}&segment_index={seg_idx}&_={int(time.time()*1000)}"
            try:
                resp = requests.get(dm_url, headers=headers, cookies=cookies, timeout=10)
                if resp.status_code != 200 or len(resp.content) < 10:
                    continue
                parsed = _parse_danmaku_protobuf(resp.content)
                for item in parsed:
                    if not item["content"]:
                        continue
                    # progress 是毫秒，转成秒
                    progress_sec = item["progress"] / 1000.0
                    danmaku_items.append({
                        "progress": progress_sec,
                        "progress_str": format_time(int(progress_sec)),
                        "mode": item["mode"],
                        "mode_str": mode_map.get(item["mode"], "其他"),
                        "color": item["color"],
                        "color_hex": f"#{item['color']:06x}",
                        "text": item["content"],
                        "timestamp": item["ctime"],
                        "time_str": beijing_time_str(item["ctime"]),
                        "pool": item["pool"],
                        "dmid": item.get("idStr", ""),
                    })
            except Exception:
                continue

        # 如果新 API 没拉到数据，回退到旧 XML API
        if not danmaku_items:
            dm_url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
            resp = requests.get(dm_url, headers=headers, timeout=10)
            root = ET.fromstring(resp.content)
            for d in root.findall("d"):
                p = d.get("p", "")
                if not p:
                    continue
                parts = p.split(",")
                if len(parts) < 4:
                    continue
                progress = float(parts[0])
                mode = int(parts[1])
                color = int(parts[3])
                ts = int(parts[4]) if len(parts) > 4 else 0
                pool = int(parts[5]) if len(parts) > 5 else 0
                dmid = parts[7] if len(parts) > 7 else ""
                text = d.text or ""
                danmaku_items.append({
                    "progress": progress,
                    "progress_str": format_time(int(progress)),
                    "mode": mode,
                    "mode_str": mode_map.get(mode, "其他"),
                    "color": color,
                    "color_hex": f"#{color:06x}",
                    "text": text,
                    "timestamp": ts,
                    "time_str": beijing_time_str(ts),
                    "pool": pool,
                    "dmid": dmid,
                })

        # 按视频时间排序
        danmaku_items.sort(key=lambda x: x["progress"])

        return jsonify({
            "success": True,
            "message": f"共 {len(danmaku_items)} 条弹幕",
            "data": {
                "video_info": {
                    "bvid": info["bvid"],
                    "title": info["title"],
                    "duration": info.get("duration", 0),
                    "owner_name": info.get("owner_name", ""),
                },
                "danmaku": danmaku_items,
                "total": len(danmaku_items),
            },
        })
    except ET.ParseError:
        return jsonify({"success": False, "message": "解析弹幕数据失败，可能是 CID 错误或弹幕为空"})
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
    page = int(data.get("page", 1))

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
                              page=page, retry_on_rate_limit=True, max_retries=2, rate_limit_wait=10.0)
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
    progress_raw = data.get("progress", "1")
    page = int(data.get("page", 1))
    if isinstance(progress_raw, str) and ":" in progress_raw:
        progress = parse_time_string(progress_raw)
    else:
        progress = int(progress_raw) if progress_raw else 1

    if not url or not messages:
        return jsonify({"success": False, "message": "视频链接和弹幕内容不能为空"})

    bvid = extract_bvid(url)
    if not bvid:
        return jsonify({"success": False, "message": "无法解析 BV ID"})

    results = []
    for i, msg in enumerate(messages):
        result = send_danmaku_api(sessdata, bili_jct, bvid, msg, mode, color, progress=progress,
                                  page=page, retry_on_rate_limit=True, max_retries=2, rate_limit_wait=10.0)
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
    page = int(data.get("page", 1))

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
                                  page=page, retry_on_rate_limit=True, max_retries=2, rate_limit_wait=10.0)
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
