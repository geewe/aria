# 🏠 Aria 家庭助手 — 全屋智能语音管家

> 版本 4.2.0 · macOS 桌面客户端 · 类 Siri 唤醒体验 · 全双工语音管线 · AI 大模型驱动 · HomeAssistant 深度集成

Aria 是一个接入 AI 大模型、面向全屋智能家居的语音助手系统。它能像豆包或小智 AI 一样流畅地进行自然语言交流，同时控制 HomeAssistant 中的智能设备、查询信息、执行系统任务。旨在成为家庭中**所有设备都可以交给它管**的超级大管家。

---

## ✨ 功能特性

### 🧠 AI 自然对话
- 接入 DeepSeek / OpenAI 兼容 API 的流式 LLM
- **自动意图路由**：闲聊、家居控制、信息查询、Agent 任务自动分流
- **流式字幕输出**：逐 token 显示回复，零等待感
- 上下文记忆：保持 5 轮多轮对话，支持免唤醒追问
- **智能降级**：API 超时或模板匹配时自动返回有用回复

### 💱 加密货币实时价格
- 支持 10+ 中文币名：比特币、以太坊、狗狗币、莱特币、瑞波、波卡、索拉纳…
- **多交易所并行查询**：Binance / OKX / CoinGecko / MEXC，取最快结果
- 网络不可用时返回友好提示，不阻塞其他功能

### 🏡 智能家居控制（HomeAssistant）
- 已对接 **126 个 HA 实体设备**
- 自然语言控制：*"打开客厅灯"*、*"空调调到26度"*、*"离家模式"*
- **跨领域匹配**：自动处理 light / switch / climate 等不同设备类型
- 中文同义词扩展：主灯=顶灯=大灯，卫生间灯可命中 switch 实体
- 房间级路由：识别 *"卧室"*、*"客厅"* 等空间指令
- 场景联动：离家/回家/睡眠/观影一键切换

### 🔧 Agent 任务执行
- 系统巡检：*"检查服务器状态"* → CPU/内存/磁盘/运行时间
- 命令执行：安全沙箱下的 shell 命令
- 任务调度：定时任务管理

### 🎤 语音交互与唤醒
- **唤醒词系统**：浏览器持续采集麦克风音频 → WebSocket 实时流 → 服务器 Porcupine/VAD 检测 → 自动录音
- **三种唤醒模式**：Porcupine 离线唤醒词（需 Picovoice Access Key）/ VAD 能量检测（零配置）/ Auto 自动选择
- **浏览器语音输入**：点击麦克风按钮使用 Web Speech API 识别
- **流式 TTS 合成**：微软 EdgeTTS（在线高自然度），边合成边播放
- **队列化音频播放**：多段 TTS 无缝衔接，不会重叠或截断
- **macOS say 离线兜底**：在线 TTS 不可用时自动切到本地语音
- **打断机制**：说话时自动停止当前 TTS

### 🖥️ macOS 桌面客户端 (类 Siri)
- **菜单栏常驻图标**：点击切换唤醒/手动对话
- **浮动覆盖窗口**：右上角弹出，类似 Siri 界面
- **波形动画**：聆听时实时音频可视化
- **Porcupine 唤醒词**（需 Access Key）：说 "Computer"（或自定义词）唤醒
- **VAD 语音触发**（零配置）：检测到语音自动进入聆听
- **桌面通知**：唤醒/回复推送系统通知
- **全局快捷键**：支持键盘触发对话（开发中）

### 🩺 系统自检诊断
- **全量 15 项诊断**：Python 版本 / 系统资源 / 音频设备 / SSL 证书 / 配置文件 / 环境变量 / 端口状态 / 网络连通性 / DNS 解析 / LLM API / HomeAssistant / Edge TTS / macOS TTS / WebSocket / 对话记录
- **快速诊断**：跳过耗时项，500ms 内出结果
- Web UI 诊断面板：✅ ✓ ⚠ ✕ 可视化

### 🌐 网络自适应
- **快速网络检测**：1s 内判定是否离线，30s 缓存
- **离线自动降级**：断网时直接走模板 + 本地 TTS，无需等待超时
- 网络状态实时推送至客户端

### 🎨 科幻风格 Web UI
- 深色沉浸式界面，环境光动画（orb 呼吸效果）
- Tiffany 蓝 + 深藏蓝配色方案
- 大号悬浮麦克风按钮
- 流式字幕逐字显示
- 系统诊断面板
- 自适应布局：手机 / 平板 / 桌面

### 🔒 安全与可靠性
- 四层 TTS 自动降级：缓存 → EdgeTTS → macOS say
- 流式超时保护：LLM 15 秒、TTS 30 秒
- SSL/TLS 加密传输
- 速率限制：防止请求过频
- 速率限制：按设备 ID 限流

---

## 🏗️ 系统架构

```
┌──────────────────────────────────────┐
│    macOS 桌面客户端 (菜单栏常驻)        │
│  ┌─────────────────────────────────┐  │
│  │ 唤醒词检测引擎 (Porcupine/VAD)  │  │
│  │ 浮动覆盖层 (类 Siri 弹窗)       │  │
│  │ WebSocket ↔ Aria 服务器         │  │
│  └─────────────┬───────────────────┘  │
└────────────────┼──────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────┐
│         唤醒词检测引擎               │
│  Porcupine / VAD / 浏览器音频流     │
│  检测到唤醒词 → {"type": "wake"}    │
└──────────────────┬──────────────────┘
                   │ (自动启动录音)
                   ↓
用户输入（语音 / 文字）
    ↓
[WebSocket 全双工连接]
    ↓
┌─────────────────────────────────────┐
│         ConversationOrchestrator       │
│  ┌──────┐  ┌──────┐  ┌──────┐       │
│  │ VAD  │→ │ STT  │→ │Intent│       │
│  │检测  │  │识别   │  │Router│       │
│  └──────┘  └──────┘  └──┬───┘       │
│                          │            │
│     ┌────────────────────┼────┐       │
│     ↓         ↓         ↓    ↓       │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌────┐  │
│  │ LLM  │ │  HA  │ │Query │ │Agent│  │
│  │对话  │ │家居   │ │查询  │ │任务  │  │
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬──┘  │
│     ↓         ↓         ↓       ↓     │
│  ┌────────────────────────────────┐   │
│  │     TTS Engine (4层降级)        │   │
│  │  Cache → EdgeTTS → macOS say   │   │
│  └────────────────────────────────┘   │
└─────────────────────────────────────┘
    ↓
客户端：流式字幕 + 队列化音频播放
```

### 技术栈

| 层 | 技术 |
|------|------|
| 后端框架 | Python FastAPI + Uvicorn |
| WebSocket | 全双工通信，JSON + 二进制音频帧 |
| LLM | Hermes CLI Gateway / OpenAI 兼容 API |
| TTS | EdgeTTS（在线）+ macOS say（离线）+ 预录音频缓存 |
| 家居 | HomeAssistant REST API |
| 前端 | 纯 HTML / CSS / JS，零依赖 |
| 音频播放 | MediaSource + 队列化缓冲播放 |
| 语音输入 | Web Speech Recognition API |
| 唤醒词 | Porcupine / VAD 能量检测 / 浏览器音频流式唤醒 |
| 桌面客户端 | Python + rumps + pyobjc (macOS 菜单栏应用) |
| 流式字幕 | WebSocket 逐 token 推送 |
| 网络检测 | TCP 探测 + DNS 解析 + HTTP HEAD |

---

## 📦 安装

### 前置要求

- Python 3.10+
- macOS（TTS 离线兜底需要 macOS `say` 命令）
- HomeAssistant 实例（可选）
- LLM API 端点（Hermes CLI Gateway / OpenAI）

### 1. 克隆仓库

```bash
git clone https://github.com/geewe/aria.git
cd aria
```

### 2. 创建虚拟环境

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml 填入你的配置:
#   - HomeAssistant URL 和令牌
#   - LLM API 地址和密钥
#   - TTS 语音偏好
```

或使用环境变量：

```bash
export HASS_URL="http://192.168.1.100:8123"
export HASS_TOKEN="your_long_lived_token"
export LLM_API_URL="http://localhost:8642/v1/chat/completions"
export LLM_API_KEY="your-api-key"
```

### 4. 生成 SSL 证书（HTTPS 必需）

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=localhost"
```

### 5. 启动桌面客户端 (类 Siri 体验)

```bash
# 菜单栏常驻助手 (需要先启动服务器)
bash aria-desktop.sh

# 或指定参数
ARIA_SERVER="wss://127.0.0.1:8653" ARIA_WAKE_KEYWORD="computer" bash aria-desktop.sh

# 设置 Porcupine Access Key 以获得唤醒词支持
export PORCUPINE_ACCESS_KEY="你的密钥"
bash aria-desktop.sh
```

桌面客户端会出现在 macOS 菜单栏，常驻后台监听唤醒词。

### 6. 浏览器访问

如果不想使用桌面客户端，直接打开浏览器:

```bash
# 开发模式
python3 run.py --port 8653

# 生产模式 (HTTPS)
python3 -m uvicorn butler.server:app --host 0.0.0.0 --port 8653 --ssl-certfile cert.pem --ssl-keyfile key.pem
```

打开浏览器访问 `https://localhost:8653/`

---

## 📁 项目结构

```
aria/
├── butler/                     # 核心引擎
│   ├── server.py               # FastAPI + WebSocket 服务
│   ├── orchestrator.py         # 对话编排器 (VAD→STT→LLM→TTS)
│   ├── llm.py                  # 流式 LLM 客户端 (含模板降级)
│   ├── tts.py                  # 四层 TTS 引擎
│   ├── stt.py                  # 语音识别 (SenseVoice / Whisper)
│   ├── router.py               # 四层意图路由器
│   ├── hass.py                 # HomeAssistant 深度集成
│   ├── agent.py                # Agent 任务执行器
│   ├── crypto.py               # 加密货币价格查询
│   ├── config.py               # YAML + 环境变量配置
│   ├── diagnostics.py          # 系统自检诊断引擎
│   ├── session.py              # 设备会话管理
│   ├── security.py             # 认证与速率限制
│   ├── monitor.py              # 指标收集与告警
│   ├── interrupt.py            # 打断管理
│   ├── audio/                  # 音频处理模块
│   │   ├── vad.py              # 语音活动检测
│   │   └── aec.py              # 回声消除
│   └── network/                # 网络连通性检测
├── static/
│   └── index.html              # Web 科幻风格界面
├── run.py                      # 开发启动入口
├── start.sh                    # 生产启动脚本
├── aria-desktop.sh             # 桌面客户端启动脚本
├── desktop/                    # 桌面客户端 (macOS 菜单栏应用)
│   ├── __init__.py
│   ├── client.py               # 主应用 (rumps 菜单栏)
│   └── overlay.py              # 浮动覆盖窗口 (AppKit)
├── config.yaml.example         # 配置模板
├── requirements.txt            # Python 依赖
└── README.md                   # 项目文档
```

---

## 🔄 API

### WebSocket 端点

`wss://<host>:8653/ws`

**客户端 → 服务器消息：**
```json
{"type": "text", "text": "打开客厅灯"}
{"type": "ping"}
{"type": "interrupt"}
{"type": "vad", "state": "speech_start"}
{"type": "wake_audio_start"}                              // 浏览器开始流式上传唤醒音频
{"type": "wake_audio_stop"}                               // 浏览器停止上传唤醒音频
<binary: PCM16 16000Hz mono audio frames>                 // 唤醒音频帧
{"type": "wake", "enable": true/false}                    // 启用/禁用服务器端麦克风唤醒
```

**服务器新增消息：**
```json
{"type": "wake"}                                          // 服务器检测到唤醒词, 通知客户端开始录音
```

**服务器 → 客户端消息：**
```json
{"type": "connected", "device_id": "dev_xxx", "version": "4.1.0"}
{"type": "state_change", "from": "idle", "to": "processing"}
{"type": "user_text", "text": "打开客厅灯"}
{"type": "network_status", "online": true}
{"type": "llm_start", "text": "好的"}
{"type": "llm_token", "text": "好"}
{"type": "tts_start", "format": "mp3"}
<binary: MP3 audio chunks>
{"type": "tts_end", "chunks": 15}
{"type": "llm_end", "text": "好的，已打开客厅灯"}
{"type": "wake"}                                             // 唤醒词/语音触发
```

### REST 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 全模块健康检查 + 设备列表 |
| `/metrics` | GET | 详细性能指标 + TTS 统计 |
| `/api/hass/status` | GET | HomeAssistant 连接状态 |
| `/api/hass/test?text=指令` | GET | 测试 HA 命令解析执行 |
| `/api/config` | GET | 查看当前配置 |
| `/api/config/reload` | POST | 热重载配置 |
| `/api/diagnostics` | GET | 全量 15 项系统自检 |
| `/api/diagnostics/quick` | GET | 快速诊断 (10 项, ~500ms) |
| `/push?text=消息` | GET | 主动推送消息到设备 |
| `/push` | POST | JSON 格式推送 |

---

## 🗺️ 路线图

- [ ] 本地 TTS 引擎（Piper TTS / CosyVoice 2）
- [ ] ESP32 硬件客户端
- [x] 唤醒词（Porcupine + VAD + 浏览器音频流）
- [ ] 多设备协同（跨房间对话迁移）
- [ ] 声纹识别（区分家庭成员）
- [ ] 网络搜索集成（实时数据查询）
- [ ] WebSocket 安全认证
- [ ] Tailscale 远程访问
- [ ] 语音助手场景编辑器（可视化配置）

---

## 📄 许可证

MIT
