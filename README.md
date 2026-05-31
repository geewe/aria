# 🏠 Aria 家庭助手 — 全屋智能语音管家

> 版本 4.1.0 · 全双工语音管线 · AI 大模型驱动 · HomeAssistant 深度集成

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

### 🎤 语音交互
- **浏览器语音输入**：Chrome 内置 Web Speech API，点击即说
- **流式 TTS 合成**：微软 EdgeTTS（在线高自然度），边合成边播放
- **队列化音频播放**：多段 TTS 无缝衔接，不会重叠或截断
- **macOS say 离线兜底**：在线 TTS 不可用时自动切到本地语音
- **打断机制**：说话时自动停止当前 TTS

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

### 5. 启动

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
- [ ] 唤醒词（"Hey Aria"，Porcupine / openWakeWord）
- [ ] 多设备协同（跨房间对话迁移）
- [ ] 声纹识别（区分家庭成员）
- [ ] 网络搜索集成（实时数据查询）
- [ ] WebSocket 安全认证
- [ ] Tailscale 远程访问
- [ ] 语音助手场景编辑器（可视化配置）

---

## 📄 许可证

MIT
