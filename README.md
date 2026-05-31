# 🏠 Aria 家庭助手 — 全屋智能语音管家

Aria 家庭助手 是一个接入 AI 大模型、面向全屋智能家居的语音助手系统。它能像豆包或小智 AI 一样流畅地自然语言交流，同时还能控制 HomeAssistant 中的智能设备、执行系统巡检任务、作为 Agent 调度中心。旨在成为家庭中**所有设备都可以交给它管**的超级大管家。

## ✨ 功能特性

### 🧠 AI 自然对话
- 接入 DeepSeek / OpenAI 兼容 API 的流式 LLM
- 自动意图路由：闲聊、查询、控制、任务自动分流
- 流式字幕输出：逐字显示回复，无等待感
- 上下文记忆：保持 5 轮多轮对话，支持免唤醒追问

### 🎤 语音交互
- **浏览器语音输入**：Chrome 内置 Web Speech API，点击即说
- **流式 TTS 合成**：微软 EdgeTTS（在线高自然度），边合成边播放
- **队列化音频播放**：多段 TTS 无缝衔接，不会重叠或截断
- **macOS say 离线兜底**：在线 TTS 不可用时自动切到本地语音

### 🏡 智能家居控制（HomeAssistant）
- 已对接 **126 个 HA 实体设备**
- 自然语言控制：*"打开客厅灯"*、*"空调调到26度"*、*"离家模式"*
- 支持灯光、空调、窗帘、场景等多种设备类型
- 房间级路由：识别 *"卧室"*、*"客厅"* 等空间指令

### 🔧 Agent 任务执行
- 系统巡检命令：*"检查服务器状态"*
- 定时任务调度
- 命令执行与结果播报

### 🎨 科幻风格 Web UI
- 深色沉浸式界面，环境光动画（orb 呼吸效果）
- Tiffany 蓝 + 深藏蓝配色
- 大号悬浮麦克风按钮
- 流式字幕逐字显示
- 自适应布局：手机/平板/桌面

### 🔒 安全与可靠性
- 四层 TTS 自动降级（缓存 → EdgeTTS → macOS say）
- 流式超时保护（LLM 15秒、TTS 30秒）
- 打断机制：说话时自动停止当前 TTS
- 回音消除（AEC）集成
- 速率限制：防止请求过频

## 🏗️ 系统架构

```
用户输入（语音/文字）
    ↓
[VAD 语音活动检测] — 音频帧进入
    ↓
[STT 语音识别] — 云端/本地引擎
    ↓
[Intent Router] — 四层路由匹配
    ├─ 闲聊 → LLM 流式生成 → 流式 TTS
    ├─ 家居控制 → HA API 调用 → TTS 播报
    ├─ 查询 → 本地处理 → TTS 播报
    └─ Agent 任务 → 后台执行 → TTS 播报
```

### 技术栈

| 层 | 技术 |
|------|------|
| 后端框架 | Python FastAPI + Uvicorn |
| WebSocket | 全双工通信，JSON + 二进制音频帧 |
| LLM | Hermes CLI Gateway / OpenAI 兼容 API |
| TTS | EdgeTTS（在线）+ macOS say（离线） |
| 家居 | HomeAssistant REST API |
| 前端 | 纯 HTML/CSS/JS，无框架依赖 |
| 音频播放 | Web Audio API 队列化播放 |
| 语音输入 | Web Speech Recognition API |
| 流式字幕 | WebSocket 逐 token 推送 |

## 📦 安装

### 前置要求

- Python 3.11+
- macOS（TTS 离线兜底需要 macOS say 命令）
- HomeAssistant 实例（可选）

### 步骤

```bash
# 1. 克隆仓库
git clone https://github.com/你的用户名/aria.git
cd aria

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 HA 令牌和地址

# 5. 生成自签名证书（HTTPS 必需）
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"

# 6. 启动服务
./start.sh
```

### 依赖清单

```
fastapi
uvicorn
websockets
httpx
edge-tts
```

## 🚀 运行

### 一键启动

```bash
./start.sh
```

服务默认监听：
- **HTTPS**: `https://localhost:8653/`
- 局域网访问：`https://<你的IP>:8653/`

### 手动启动

```bash
source venv/bin/activate
HASS_TOKEN="your_token" HASS_URL="http://your-ha:8123" \
  python -m uvicorn butler.server:app \
  --host 0.0.0.0 --port 8653 \
  --ssl-certfile=cert.pem --ssl-keyfile=key.pem
```

### 参数说明

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `HASS_TOKEN` | HomeAssistant 长寿命令牌 | — |
| `HASS_URL` | HA 服务器地址 | `http://192.168.2.45:8123` |
| `TTS_VOICE` | EdgeTTS 声线 | `zh-CN-XiaoxiaoNeural` |
| `TTS_RATE` | TTS 语速 | `+0%` |
| `TTS_VOLUME` | TTS 音量 | `+0%` |
| `PORT` | 服务器端口 | `8653` |

## 🖥️ Web 界面

打开浏览器访问 `https://localhost:8653/`

- **🎤 麦克风按钮**：点击说话，再次点击停止
- **⌨️ 文字输入**：底部输入框可打字发送
- **💬 流式字幕**：回复文字逐字显示
- **🌐 状态指示**：左上角连接状态 + HA 设备数

首次访问会提示证书不安全，点击 **高级 → 继续前往** 即可。

## 🏡 HomeAssistant 配置

1. 在 HA 中创建 **长寿命访问令牌**
   - 用户资料 → 安全 → 长寿命访问令牌 → 创建
2. 将令牌设为环境变量 `HASS_TOKEN`
3. 确保 HA 地址可访问（默认 `http://192.168.2.45:8123`）

## 📁 项目结构

```
aria/
├── butler/                  # 核心后端
│   ├── server.py            # FastAPI WebSocket 服务器
│   ├── orchestrator.py      # 对话编排器（VAD→STT→LLM→TTS）
│   ├── llm.py               # 流式 LLM 客户端
│   ├── tts.py               # 分层 TTS 引擎
│   ├── router.py            # 四层意图路由
│   ├── hass.py              # HomeAssistant 连接器
│   ├── agent.py             # Agent 任务执行器
│   ├── session.py           # 设备会话管理
│   ├── security.py          # 认证与速率限制
│   ├── monitor.py           # 指标收集与告警
│   ├── interrupt.py         # 打断管理
│   ├── config.py            # 配置管理
│   ├── audio/               # 音频处理
│   │   ├── vad.py           # 语音活动检测
│   │   └── aec.py           # 回声消除
│   └── stt.py               # 语音识别
├── static/
│   └── index.html           # Web 界面
├── run.py                   # 开发启动入口
├── start.sh                 # 生产启动脚本
├── cert.pem                 # SSL 证书
├── key.pem                  # SSL 密钥
└── requirements.txt         # Python 依赖
```

## 🔄 API

### WebSocket 端点

`wss://<host>:8653/ws`

**客户端 → 服务器消息：**
```json
{"type": "text", "text": "打开客厅灯"}
```

**服务器 → 客户端消息：**
```json
{"type": "llm_start"}
{"type": "llm_token", "text": "好"}
{"type": "tts_start", "format": "mp3"}
<binary: MP3 audio chunks>
{"type": "tts_end", "chunks": 15}
{"type": "llm_end", "text": "好的，已打开客厅灯"}
```

### REST 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 + 设备列表 |
| `/metrics` | GET | 详细指标 + TTS 统计 |
| `/api/hass/status` | GET | HA 连接状态 |
| `/push?text=消息` | GET | 主动推送消息到设备 |
| `/push` | POST | JSON 格式推送 |

## 🛣️ 路线图

- [ ] 本地 TTS 引擎（Piper TTS / CosyVoice 2）— 零延迟首音
- [ ] ESP32 硬件客户端
- [ ] 唤醒词（Hey Aria）
- [ ] 多设备协同（跨房间对话迁移）
- [ ] 声纹识别（区分家庭成员）
- [ ] Tailscale 远程访问

## 📄 许可证

MIT
