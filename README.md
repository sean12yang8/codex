# Azure Realtime + Audio Web MVP

两个最小可运行网页端产品：
- **RealtimeTalk**（基于 Azure GPT-Realtime 1.5 思路）：实时多轮对话、语音输入、可打断。
- **AudioStudio**（基于 Azure GPT-Audio 1.5 思路）：转写摘要、口播文案生成与播放。

## 快速启动
```bash
npm install
npm start
```
打开 `http://localhost:3000`。

## 运行模式
- **Mock 模式（默认可跑）**：未配置 Azure 环境变量时，后端返回可演示结果。
- **Azure 模式**：配置以下变量后调用 Azure OpenAI 接口。

```bash
export AZURE_OPENAI_ENDPOINT="https://<your-resource>.openai.azure.com"
export AZURE_OPENAI_API_KEY="<your-key>"
export AZURE_REALTIME_DEPLOYMENT="gpt-realtime-1-5"
export AZURE_AUDIO_DEPLOYMENT="gpt-audio-1-5"
# 可选
export AZURE_OPENAI_API_VERSION="2024-10-21"
```

## 接口
- `GET /api/health`
- `POST /api/realtime/chat`
- `POST /api/audio/analyze`
- `POST /api/audio/script`

## 规划文档
- `docs/azure-realtime-audio-mvp-plan.md`
