import http from 'http';
import path from 'path';
import { fileURLToPath } from 'url';
import { readFile } from 'fs/promises';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const publicDir = path.join(__dirname, '..', 'public');
const port = Number(process.env.PORT || 3000);

function hasAzureConfig() {
  return Boolean(
    process.env.AZURE_OPENAI_ENDPOINT &&
      process.env.AZURE_OPENAI_API_KEY &&
      process.env.AZURE_REALTIME_DEPLOYMENT &&
      process.env.AZURE_AUDIO_DEPLOYMENT
  );
}

async function callAzureChat({ deployment, messages, temperature = 0.6 }) {
  const endpoint = process.env.AZURE_OPENAI_ENDPOINT;
  const apiKey = process.env.AZURE_OPENAI_API_KEY;
  const apiVersion = process.env.AZURE_OPENAI_API_VERSION || '2024-10-21';
  const url = `${endpoint}/openai/deployments/${deployment}/chat/completions?api-version=${apiVersion}`;

  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'api-key': apiKey
    },
    body: JSON.stringify({ messages, temperature })
  });

  if (!res.ok) {
    throw new Error(`Azure call failed: ${res.status} ${await res.text()}`);
  }

  const data = await res.json();
  return data?.choices?.[0]?.message?.content?.trim() || '（模型没有返回内容）';
}

function sendJson(res, code, data) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

function contentType(filePath) {
  if (filePath.endsWith('.html')) return 'text/html; charset=utf-8';
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8';
  if (filePath.endsWith('.js')) return 'text/javascript; charset=utf-8';
  return 'text/plain; charset=utf-8';
}

function safePath(urlPath) {
  const clean = urlPath === '/' ? '/index.html' : urlPath;
  const normalized = path.normalize(clean).replace(/^\.+/, '');
  return path.join(publicDir, normalized);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
      if (data.length > 1_000_000) reject(new Error('Body too large'));
    });
    req.on('end', () => {
      resolve(data ? JSON.parse(data) : {});
    });
    req.on('error', reject);
  });
}

async function handleApi(req, res) {
  if (req.method === 'GET' && req.url === '/api/health') {
    sendJson(res, 200, { ok: true, mode: hasAzureConfig() ? 'azure' : 'mock' });
    return;
  }

  if (req.method !== 'POST') {
    sendJson(res, 405, { error: 'Method not allowed' });
    return;
  }

  const body = await readBody(req);

  if (req.url === '/api/realtime/chat') {
    const { input = '', history = [] } = body;
    if (!input.trim()) return sendJson(res, 400, { error: 'input is required' });

    if (!hasAzureConfig()) {
      return sendJson(res, 200, {
        mode: 'mock',
        reply: `收到：${input}\n\n建议：你可以把这句话再扩展 1 个细节（时间/数字/场景），我会继续追问帮助你练习。`
      });
    }

    const reply = await callAzureChat({
      deployment: process.env.AZURE_REALTIME_DEPLOYMENT,
      messages: [
        { role: 'system', content: '你是实时语音陪练助手，回复简洁、有互动感，并给出下一步提问。' },
        ...history.slice(-8),
        { role: 'user', content: input }
      ],
      temperature: 0.7
    });
    return sendJson(res, 200, { mode: 'azure', reply });
  }

  if (req.url === '/api/audio/analyze') {
    const { transcript = '' } = body;
    if (!transcript.trim()) return sendJson(res, 400, { error: 'transcript is required' });

    if (!hasAzureConfig()) {
      return sendJson(res, 200, {
        mode: 'mock',
        summary: `【摘要】\n${transcript.slice(0, 90)}...\n\n【行动项】\n1) 跟进客户关键诉求\n2) 补充报价与时效\n\n【风险点】\n- 关键承诺未确认`
      });
    }

    const summary = await callAzureChat({
      deployment: process.env.AZURE_AUDIO_DEPLOYMENT,
      messages: [
        { role: 'system', content: '你是音频质检助手。输出三段：摘要、行动项（列表）、风险点（列表）。' },
        { role: 'user', content: `请分析以下转写文本：\n${transcript}` }
      ],
      temperature: 0.3
    });
    return sendJson(res, 200, { mode: 'azure', summary });
  }

  if (req.url === '/api/audio/script') {
    const { topic = '', tone = '专业', duration = '60秒' } = body;
    if (!topic.trim()) return sendJson(res, 400, { error: 'topic is required' });

    if (!hasAzureConfig()) {
      return sendJson(res, 200, {
        mode: 'mock',
        script: `大家好，今天用${duration}带你了解：${topic}。\n先讲痛点，再给你一个可以立刻执行的小技巧。\n如果你觉得有帮助，欢迎继续留言交流。`
      });
    }

    const script = await callAzureChat({
      deployment: process.env.AZURE_AUDIO_DEPLOYMENT,
      messages: [
        { role: 'system', content: '你是短视频口播文案助手，输出自然口语化中文，3段以内。' },
        { role: 'user', content: `请生成一段${duration}的口播文案。主题：${topic}。风格：${tone}。` }
      ],
      temperature: 0.8
    });
    return sendJson(res, 200, { mode: 'azure', script });
  }

  sendJson(res, 404, { error: 'Not found' });
}

const server = http.createServer(async (req, res) => {
  try {
    if (!req.url) return sendJson(res, 400, { error: 'Invalid request' });
    if (req.url.startsWith('/api/')) return await handleApi(req, res);

    const urlPath = req.url.split('?')[0];
    const filePath = safePath(urlPath);
    const file = await readFile(filePath).catch(async () => readFile(path.join(publicDir, 'index.html')));
    res.writeHead(200, { 'Content-Type': contentType(filePath) });
    res.end(file);
  } catch (error) {
    sendJson(res, 500, { error: error.message });
  }
});

server.listen(port, () => {
  console.log(`MVP apps running at http://localhost:${port}`);
});
