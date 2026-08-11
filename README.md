# B站弹幕 Agent

指定视频发弹幕的工具，支持单条发送、时间段分布发送、批量发送、定时发送。

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/winccctan/bili-danmaku-agent)

## 功能

- **单条发送** — 指定视频时间点发一条弹幕
- **时间段发送** — 弹幕分布到指定视频区间（均匀/随机/顺序）
- **批量发送** — 依次发送多条到同一时间点
- **定时发送** — 到指定时刻自动发送（浏览器端倒计时）
- **弹幕样式** — 滚动/顶部/底部，8 种颜色
- **多用户** — 每人用自己的 B站 Cookie，凭证存浏览器 localStorage

## 本地运行

```bash
pip install -r requirements.txt
python app.py
# 浏览器打开 http://localhost:5000
```

## 云端部署

### 方式一：Render（推荐，免费）

1. 将项目推送到 GitHub 仓库
2. 打开 https://render.com → New → Web Service
3. 连接 GitHub 仓库
4. 配置：
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 app:app`
5. 点击 Create Web Service
6. 等待部署完成，获得 `https://xxx.onrender.com` 域名

项目已包含 `render.yaml`，也可用 Blueprint 方式部署。

### 方式二：Railway

1. 打开 https://railway.app → New Project → Deploy from GitHub
2. 选择仓库，Railway 自动检测 Python 项目
3. 添加环境变量 `PORT=5000`
4. 部署命令：`gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 app:app`

### 方式三：Fly.io

```bash
fly launch  # 自动生成配置
fly deploy
```

### 方式四：Docker

```bash
docker build -t bili-danmaku-agent .
docker run -p 5000:5000 bili-danmaku-agent
```

## 获取 B站 Cookie

1. 浏览器登录 B站
2. F12 → Application → Cookies → `https://www.bilibili.com`
3. 找到 `SESSDATA` 和 `bili_jct`，复制它们的值
4. 粘贴到应用的凭证输入框

## 技术架构

| 组件 | 技术 |
|------|------|
| 后端 | Flask + flask-cors（无状态，多用户安全） |
| 前端 | 纯 HTML/CSS/JS（无框架依赖） |
| 部署 | Gunicorn + Docker |
| 凭证存储 | 浏览器 localStorage（不上传服务器） |
| 定时任务 | 客户端 setTimeout（无需服务端常驻） |

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 前端页面 |
| GET | `/api/health` | 健康检查 |
| GET | `/api/colors` | 预设颜色 |
| POST | `/api/login-check` | 验证登录状态 |
| POST | `/api/video-info` | 获取视频信息 |
| POST | `/api/send` | 发送单条弹幕 |
| POST | `/api/send-batch` | 批量发送 |
| POST | `/api/send-timed` | 时间段分布发送 |

## 注意事项

- 仅供学习交流使用，请遵守 B站社区规范
- 不要频繁发送弹幕，建议间隔 ≥ 2 秒
- Cookie 有有效期，失效后需重新获取
