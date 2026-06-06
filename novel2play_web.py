#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Novel2Play - AI小说转剧本工具 (Web版)
支持图形化操作，可在线修改提示词，实时显示处理进度
"""

import os
import sys
import json
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from collections import OrderedDict

import requests
import yaml
from flask import Flask, render_template_string, request, jsonify
from dotenv import load_dotenv

# 尝试导入可选解析库
try:
    import docx
except ImportError:
    docx = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import ebooklib
    from ebooklib import epub
except ImportError:
    ebooklib = None

# 获取 exe 所在目录（如果是开发环境则为 .py 所在目录）
BASE_DIR = Path(sys.argv[0] if getattr(sys, 'frozen', False) else __file__).parent
os.chdir(BASE_DIR)  # 切换工作目录到 exe 所在位置

# 加载 .env 文件（如果存在）
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

# ==================== 配置 ====================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

# 全局变量存储任务状态
tasks = {}

# 默认系统提示词
DEFAULT_SYSTEM_PROMPT = """
你是一个专业的剧本改编专家。请将小说片段转换为**分场剧本**，输出严格的 JSON 格式，不要有任何额外文字。

输出 JSON 结构如下：
{
    "title": "剧本标题（根据内容推断）",
    "characters": [
        {"name": "角色名", "description": "一句话外貌/性格/身份特点"}
    ],
    "scenes": [
        {
            "scene_id": 整数,
            "location": "地点",
            "time": "时间段",
            "description": "场景视觉和氛围描述（一两句话）",
            "elements": [
                { "type": "action", "content": "动作描述" },
                { "type": "line", "speaker": "说话人", "text": "台词原文", "action": "动作", "emotion": "情绪" },
                { "type": "narrate", "content": "旁白", "voice": "语气" }
            ]
        }
    ]
}

**创作要求**：
1. 严格按原文顺序划分场景，地点或时间变化时开启新 scene。
2. 旁白尽可能少，能用动作和对白表现的绝不使用旁白。
3. 动作要具体，对白保留原文并添加动作和情绪。
4. 角色描述精简（一句话）。
5. 如果提供了【已有角色列表】，请复用它们，不要创造重复角色。
6. 如果提供了【下一个场景编号】，请从该编号开始递增。
7. 如果提供了【剧情摘要】，请确保新内容与摘要衔接自然。

**🎯 自动识别小说正文（关键要求）**：
- 输入文本可能混有**非小说内容**，例如：出版信息（ISBN、版权页、图书在版编目）、序言/前言、学术评论、作者生平、注释、页码、目录、广告等。
- **你必须自动区分正文与非正文**。只有以下内容属于小说正文，应当转换为剧本：
  * 人物的对话、独白、书信内容
  * 人物的动作、神态、心理活动描写
  * 环境、场景的叙述性描写
  * 推动情节发展的叙事
- **凡是不属于上述类别的文字，一律忽略，不要生成任何 scene 或 element。**
- **如何识别正文开始**：寻找故事叙述的起点，通常表现为：人物出场、时间地点交代（如"四月八日"）、第一人称或第三人称叙事开始。如果输入开头全是非正文，请跳过直到遇到正文。
- **示例**：输入中出现"ISBN 978-7-02-016977-1"、"陀思妥耶夫斯基的处女作"、"图书在版编目（CIP）数据"等，应完全忽略，不产生任何输出。

**特别提示**：
- 如果整段输入都是非正文（如版权页、序言），则输出空场景列表 `"scenes": []`，但保留标题和角色（若无则留空）。
- 保持输出 JSON 结构完整，不要因为忽略非正文而破坏格式。
"""

# 当前使用的提示词
current_system_prompt = DEFAULT_SYSTEM_PROMPT
PROMPT_CONFIG_FILE = BASE_DIR / "prompt_config.json"

def load_prompt_from_file():
    global current_system_prompt
    try:
        if PROMPT_CONFIG_FILE.exists():
            with open(PROMPT_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "prompt" in data:
                    current_system_prompt = data["prompt"]
    except Exception as e:
        print(f"加载提示词配置失败: {e}")

def save_prompt_to_file(prompt):
    global current_system_prompt
    try:
        with open(PROMPT_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({"prompt": prompt}, f, indent=2, ensure_ascii=False)
        current_system_prompt = prompt
        return True
    except Exception as e:
        print(f"保存提示词配置失败: {e}")
        return False

load_prompt_from_file()

def save_api_key_to_env(api_key):
    """将 API Key 写入 .env 文件"""
    try:
        with open(ENV_FILE, 'w', encoding='utf-8') as f:
            f.write(f"DEEPSEEK_API_KEY={api_key}\n")
        # 重新加载环境变量
        load_dotenv(ENV_FILE, override=True)
        return True
    except Exception as e:
        print(f"保存 API Key 失败: {e}")
        return False

# ==================== 核心处理逻辑 ====================
class State:
    def __init__(self):
        self.characters = OrderedDict()
        self.next_scene_id = 1
        self.last_summary = ""
        self.global_title = "未命名"

    def update_from_script(self, script_data):
        if script_data.get("title") and self.global_title == "未命名":
            self.global_title = script_data["title"]
        for char in script_data.get("characters", []):
            name = char["name"]
            if name not in self.characters:
                self.characters[name] = char["description"]
            else:
                if len(char.get("description", "")) > len(self.characters[name]):
                    self.characters[name] = char["description"]
        max_id = max([s.get("scene_id", 0) for s in script_data.get("scenes", [])], default=0)
        if max_id >= self.next_scene_id:
            self.next_scene_id = max_id + 1
        if script_data.get("scenes"):
            last_scene = script_data["scenes"][-1]
            summary = f"场景{last_scene.get('scene_id')}：{last_scene.get('location')}，{last_scene.get('description')}。"
            elements = last_scene.get("elements", [])
            last_elems = [e for e in elements if e.get("type") in ("line", "action")][-2:]
            for e in last_elems:
                if e["type"] == "line":
                    summary += f" {e['speaker']}说：“{e['text'][:30]}”"
                else:
                    summary += f" {e['content'][:30]}"
            self.last_summary = summary[:200]

    def get_context_prompt(self):
        if not self.characters and self.next_scene_id == 1:
            return ""
        context = "\n【已有角色列表】（请复用，不要改名）：\n"
        for name, desc in self.characters.items():
            context += f"- {name}：{desc}\n"
        context += f"\n【下一个场景编号从 {self.next_scene_id} 开始】\n"
        if self.last_summary:
            context += f"\n【上一段剧情摘要】：{self.last_summary}\n"
        context += "\n请继续处理以下小说片段，保持角色和情节一致。\n"
        return context

def read_novel(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    suffix = path.suffix.lower()
    if suffix in ('.txt', '.md'):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    elif suffix == '.docx':
        if docx is None:
            raise ImportError("需要安装 python-docx 库")
        doc = docx.Document(path)
        return '\n'.join(p.text for p in doc.paragraphs)
    elif suffix == '.pdf':
        if pdfplumber is None:
            raise ImportError("需要安装 pdfplumber 库")
        text = ''
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
        return text
    elif suffix == '.epub':
        if ebooklib is None:
            raise ImportError("需要安装 EbookLib 库")
        book = epub.read_epub(path)
        text = ''
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                content = item.get_body_content().decode('utf-8', errors='ignore')
                import re
                clean = re.sub(r'<[^>]+>', ' ', content)
                text += clean + '\n'
        return text
    else:
        raise ValueError(f"不支持的文件格式: {suffix}")

def split_text(text, max_chars=4000):
    paragraphs = text.split('\n')
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 <= max_chars:
            current += para + "\n"
        else:
            if current:
                chunks.append(current.strip())
            current = para + "\n"
    if current:
        chunks.append(current.strip())
    return chunks

def call_deepseek(prompt: str, api_key: str, system_prompt: str, max_tokens: int = 8000) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"}
    }
    response = requests.post("https://api.deepseek.com/v1/chat/completions", headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    result = response.json()
    content = result["choices"][0]["message"]["content"].strip()
    # 清理 markdown
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()
    return json.loads(content)

def process_novel_task(task_id, file_path, api_key, chunk_size, max_tokens, output_dir):
    tasks[task_id]["status"] = "running"
    tasks[task_id]["progress"] = 0
    tasks[task_id]["message"] = "读取文件..."
    try:
        full_text = read_novel(file_path)
        tasks[task_id]["message"] = f"读取完成，共 {len(full_text)} 字符"
        chunks = split_text(full_text, max_chars=chunk_size)
        total_chunks = len(chunks)
        tasks[task_id]["total_chunks"] = total_chunks
        tasks[task_id]["message"] = f"分块完成，共 {total_chunks} 块"

        state = State()
        all_scripts = []

        for idx, chunk in enumerate(chunks, 1):
            # 检查停止请求
            if tasks[task_id].get("stop_requested"):
                tasks[task_id]["status"] = "stopped"
                tasks[task_id]["message"] = "用户已停止转换"
                return

            tasks[task_id]["progress"] = idx - 1
            tasks[task_id]["current_chunk"] = idx
            tasks[task_id]["message"] = f"正在处理第 {idx}/{total_chunks} 块..."
            context = state.get_context_prompt()
            user_prompt = f"{context}\n【待处理的小说片段】：\n{chunk}"
            try:
                script = call_deepseek(user_prompt, api_key, current_system_prompt, max_tokens)
                if "scenes" not in script:
                    script["scenes"] = []
                if "characters" not in script:
                    script["characters"] = []
                all_scripts.append(script)
                state.update_from_script(script)
            except Exception as e:
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["message"] = f"处理失败：{e}"
                return

        # 检查停止请求（完成所有块后）
        if tasks[task_id].get("stop_requested"):
            tasks[task_id]["status"] = "stopped"
            tasks[task_id]["message"] = "用户已停止转换"
            return

        final_scenes = []
        for script in all_scripts:
            final_scenes.extend(script.get("scenes", []))
        final_characters = [{"name": n, "description": d} for n, d in state.characters.items()]
        final_script = {
            "title": state.global_title,
            "characters": final_characters,
            "scenes": final_scenes
        }
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_file = Path(output_dir) / f"script_{timestamp}.yaml"
        with open(output_file, "w", encoding="utf-8") as f:
            yaml.dump(final_script, f, allow_unicode=True, sort_keys=False, indent=2)
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["progress"] = total_chunks
        tasks[task_id]["output_file"] = str(output_file)
        tasks[task_id]["message"] = f"处理完成，剧本保存至 {output_file}"
        tasks[task_id]["total_lines"] = sum(1 for scene in final_scenes for elem in scene.get("elements", []) if elem.get("type") == "line")
        tasks[task_id]["total_scenes"] = len(final_scenes)
        tasks[task_id]["total_characters"] = len(final_characters)
        tasks[task_id]["title"] = state.global_title
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["message"] = f"处理失败：{e}"

# ==================== Flask 路由 ====================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <title>Novel2Play - AI小说转剧本工具</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f0f2f5;
            margin: 0;
            padding: 20px;
            color: #333;
            font-size: 14px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
        }
        .card {
            background: white;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            padding: 20px;
            margin-bottom: 20px;
        }
        .left-panel {
            flex: 1;
            min-width: 280px;
        }
        .right-panel {
            flex: 2;
            min-width: 400px;
        }
        h2 {
            margin-top: 0;
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            display: inline-block;
            padding-bottom: 5px;
            font-size: 1.5rem;
        }
        textarea {
            width: 100%;
            height: 400px;
            font-family: 'Courier New', monospace;
            font-size: 13px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 8px;
            resize: vertical;
        }
        .file-area {
            margin: 20px 0;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
        }
        .btn {
            background: #3498db;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.2s;
            margin-right: 10px;
        }
        .btn:hover { background: #2980b9; }
        .btn:disabled { background: #95a5a6; cursor: not-allowed; }
        .btn-stop {
            background: #e74c3c;
        }
        .btn-stop:hover { background: #c0392b; }
        .progress-bar {
            width: 100%;
            background-color: #e0e0e0;
            border-radius: 10px;
            margin: 15px 0;
            overflow: hidden;
        }
        .progress-fill {
            height: 24px;
            background-color: #3498db;
            width: 0%;
            text-align: center;
            line-height: 24px;
            color: white;
            font-size: 13px;
            transition: width 0.3s;
        }
        #statusMsg {
            margin: 10px 0;
            font-weight: bold;
            word-break: break-all;
        }
        .log {
            background: #f8f9fa;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 10px;
            height: 200px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 12px;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .stats {
            background: #e8f4fd;
            border-radius: 8px;
            padding: 10px;
            margin-top: 15px;
            word-break: break-word;
        }
        .stats p { margin: 5px 0; }
        .api-input {
            margin: 15px 0;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
        }
        .api-input input {
            flex: 2;
            min-width: 200px;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
        }
        .api-input label {
            white-space: nowrap;
        }
        .note {
            color: #7f8c8d;
            font-size: 12px;
            margin-top: 10px;
        }
        @media (max-width: 768px) {
            body { padding: 10px; }
            .left-panel, .right-panel { min-width: 100%; }
            .container { gap: 10px; }
            .btn { padding: 6px 12px; font-size: 12px; }
            textarea { height: 300px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="left-panel">
            <div class="card">
                <h2>📝 系统提示词</h2>
                <textarea id="systemPrompt">{{ prompt }}</textarea>
                <div style="margin-top: 10px;">
                    <button class="btn" id="savePromptBtn" onclick="savePrompt()">💾 保存提示词</button>
                </div>
                <div class="note">提示词将保存在程序目录下的 prompt_config.json 中，重启后自动加载。</div>
            </div>
            <div class="card">
                <h2>⚙️ 设置</h2>
                <div class="api-input">
                    <label>DeepSeek API Key:</label>
                    <input type="password" id="apiKey" placeholder="sk-..." value="{{ api_key }}">
                    <button class="btn" onclick="saveApiKey()">💾 保存 Key</button>
                </div>
                <div class="note">保存后 Key 会写入 .env 文件，下次启动自动加载。</div>
            </div>
        </div>
        <div class="right-panel">
            <div class="card">
                <h2>📂 小说文件</h2>
                <div class="file-area">
                    <input type="file" id="fileInput" accept=".txt,.md,.docx,.pdf,.epub" style="flex:1;">
                    <button class="btn" id="uploadBtn" onclick="startConversion()">🚀 开始转换</button>
                    <button class="btn btn-stop" id="stopBtn" onclick="stopConversion()" disabled>⏹️ 停止</button>
                </div>
                <div id="progressArea" style="display: none;">
                    <div class="progress-bar">
                        <div class="progress-fill" id="progressFill">0%</div>
                    </div>
                    <div id="statusMsg">等待开始...</div>
                    <div class="log" id="logArea"></div>
                    <div class="stats" id="statsArea" style="display: none;"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentTaskId = null;
        let pollInterval = null;
        let lastChunk = 0;
        let stopClicked = false;          // 标记用户是否点击了停止
        let manualStopMsgDisplayed = false; // 是否已显示手动停止的日志

        function savePrompt() {
            const prompt = document.getElementById('systemPrompt').value;
            fetch('/api/save_prompt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: prompt })
            })
            .then(res => res.json())
            .then(data => {
                alert(data.success ? '提示词保存成功！' : '保存失败：' + data.error);
            });
        }

        function saveApiKey() {
            const apiKey = document.getElementById('apiKey').value;
            if (!apiKey) {
                alert('请输入 API Key');
                return;
            }
            fetch('/api/save_api_key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: apiKey })
            })
            .then(res => res.json())
            .then(data => {
                alert(data.success ? 'API Key 保存成功！' : '保存失败：' + data.error);
            });
        }

        function startConversion() {
            const fileInput = document.getElementById('fileInput');
            const apiKey = document.getElementById('apiKey').value;
            if (!fileInput.files || fileInput.files.length === 0) {
                alert('请先选择小说文件');
                return;
            }
            if (!apiKey) {
                alert('请填写 DeepSeek API Key');
                return;
            }
            const file = fileInput.files[0];
            const formData = new FormData();
            formData.append('file', file);
            formData.append('api_key', apiKey);
            formData.append('chunk_size', '4000');
            formData.append('max_tokens', '8000');

            // 重置所有状态
            document.getElementById('uploadBtn').disabled = true;
            document.getElementById('stopBtn').disabled = false;
            document.getElementById('progressArea').style.display = 'block';
            document.getElementById('logArea').innerHTML = '';
            document.getElementById('statsArea').style.display = 'none';
            document.getElementById('progressFill').style.width = '0%';
            document.getElementById('progressFill').innerText = '0%';
            document.getElementById('statusMsg').innerText = '上传文件中...';
            lastChunk = 0;
            stopClicked = false;
            manualStopMsgDisplayed = false;

            fetch('/api/convert', {
                method: 'POST',
                body: formData
            })
            .then(res => res.json())
            .then(data => {
                if (data.task_id) {
                    currentTaskId = data.task_id;
                    startPolling();
                } else {
                    throw new Error(data.error || '未知错误');
                }
            })
            .catch(err => {
                alert('启动转换失败：' + err.message);
                document.getElementById('uploadBtn').disabled = false;
                document.getElementById('stopBtn').disabled = true;
            });
        }

        function stopConversion() {
            if (!currentTaskId) return;
            // 标记停止请求，并立即冻结状态显示
            stopClicked = true;
            document.getElementById('stopBtn').disabled = true;
            document.getElementById('statusMsg').innerText = '正在停止，请稍候...';
            // 添加一条手动停止的日志（避免重复添加）
            if (!manualStopMsgDisplayed) {
                const logDiv = document.getElementById('logArea');
                logDiv.innerHTML += `[${new Date().toLocaleTimeString()}] ⏹️ 正在停止转换...\n`;
                logDiv.scrollTop = logDiv.scrollHeight;
                manualStopMsgDisplayed = true;
            }
            // 发送停止请求
            fetch(`/api/stop/${currentTaskId}`, { method: 'POST' })
            .then(res => res.json())
            .then(data => {
                if (!data.success) {
                    console.error('停止请求失败', data.error);
                }
            })
            .catch(err => console.error('停止请求失败', err));
        }

        function startPolling() {
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(() => {
                if (!currentTaskId) return;
                fetch(`/api/progress/${currentTaskId}`)
                .then(res => res.json())
                .then(data => {
                    // 如果用户已经点击停止且任务尚未变成 stopped，则忽略后端的状态消息更新（保持显示“正在停止...”）
                    if (stopClicked && data.status !== 'stopped') {
                        // 仍需要更新进度条（也许进度还在增长）
                        if (data.total_chunks && data.total_chunks > 0) {
                            const percent = Math.round((data.progress / data.total_chunks) * 100);
                            document.getElementById('progressFill').style.width = percent + '%';
                            document.getElementById('progressFill').innerText = percent + '%';
                        }
                        // 不更新 statusMsg 和日志（除了完成/失败状态）
                        if (data.status === 'completed') {
                            // 如果任务竟然完成了，解除冻结
                            stopClicked = false;
                        } else if (data.status === 'failed') {
                            stopClicked = false;
                        }
                        // 如果状态变为 stopped，会在下面处理
                    } else {
                        // 正常更新
                        updateProgressUI(data);
                    }

                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'stopped') {
                        clearInterval(pollInterval);
                        document.getElementById('uploadBtn').disabled = false;
                        document.getElementById('stopBtn').disabled = true;
                        if (data.status === 'completed') {
                            showStats(data);
                        }
                        if (data.status === 'stopped') {
                            // 确保状态消息为最终停止消息
                            document.getElementById('statusMsg').innerText = '用户已停止转换';
                            const logDiv = document.getElementById('logArea');
                            // 避免重复添加停止日志
                            if (!logDiv.innerHTML.includes('✅ 已停止')) {
                                logDiv.innerHTML += `[${new Date().toLocaleTimeString()}] ✅ 已停止\n`;
                            }
                        }
                        stopClicked = false;
                    }
                });
            }, 1000);
        }

        function updateProgressUI(data) {
            if (data.total_chunks && data.total_chunks > 0) {
                const percent = Math.round((data.progress / data.total_chunks) * 100);
                document.getElementById('progressFill').style.width = percent + '%';
                document.getElementById('progressFill').innerText = percent + '%';
            }
            document.getElementById('statusMsg').innerText = data.message;
            // 只有当前块编号变化时才添加日志
            if (data.current_chunk && data.current_chunk !== lastChunk) {
                lastChunk = data.current_chunk;
                const logDiv = document.getElementById('logArea');
                logDiv.innerHTML += `[${new Date().toLocaleTimeString()}] 处理第 ${data.current_chunk}/${data.total_chunks} 块...\n`;
                logDiv.scrollTop = logDiv.scrollHeight;
            }
            if (data.status === 'completed') {
                const logDiv = document.getElementById('logArea');
                logDiv.innerHTML += `[${new Date().toLocaleTimeString()}] ✅ 转换完成！\n`;
            } else if (data.status === 'failed') {
                const logDiv = document.getElementById('logArea');
                logDiv.innerHTML += `[${new Date().toLocaleTimeString()}] ❌ 失败：${data.message}\n`;
            }
        }

        function showStats(data) {
            const statsDiv = document.getElementById('statsArea');
            statsDiv.style.display = 'block';
            statsDiv.innerHTML = `
                <h3>📊 转换结果</h3>
                <p><strong>标题：</strong> ${data.title || '未命名'}</p>
                <p><strong>角色数：</strong> ${data.total_characters || 0}</p>
                <p><strong>场景数：</strong> ${data.total_scenes || 0}</p>
                <p><strong>台词数：</strong> ${data.total_lines || 0}</p>
                <p><strong>输出文件：</strong> ${data.output_file || '未知'}</p>
            `;
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    return render_template_string(HTML_TEMPLATE, prompt=current_system_prompt, api_key=api_key)

@app.route('/api/save_prompt', methods=['POST'])
def save_prompt():
    data = request.get_json()
    prompt = data.get('prompt', '')
    if not prompt:
        return jsonify({"success": False, "error": "提示词不能为空"})
    if save_prompt_to_file(prompt):
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "保存文件失败"})

@app.route('/api/save_api_key', methods=['POST'])
def save_api_key():
    data = request.get_json()
    api_key = data.get('api_key', '').strip()
    if not api_key:
        return jsonify({"success": False, "error": "API Key 不能为空"})
    if save_api_key_to_env(api_key):
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "保存 .env 文件失败"})

@app.route('/api/stop/<task_id>', methods=['POST'])
def stop_task(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    if task["status"] in ("running", "pending"):
        task["stop_requested"] = True
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "任务已完成或已停止"})

@app.route('/api/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({"error": "没有上传文件"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "文件名为空"}), 400

    # 保存临时文件
    temp_dir = BASE_DIR / "temp"
    temp_dir.mkdir(exist_ok=True)
    temp_path = temp_dir / file.filename
    file.save(temp_path)

    api_key = request.form.get('api_key', '')
    if not api_key:
        api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return jsonify({"error": "未提供 API Key，请在页面填写或保存后重试"}), 400

    chunk_size = int(request.form.get('chunk_size', 4000))
    max_tokens = int(request.form.get('max_tokens', 8000))

    task_id = str(uuid.uuid4())
    output_dir = BASE_DIR  # 输出到 exe 所在目录

    tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "message": "任务已创建",
        "total_chunks": 0,
        "current_chunk": 0,
        "output_file": None,
        "total_lines": 0,
        "total_scenes": 0,
        "total_characters": 0,
        "title": "",
        "stop_requested": False
    }

    thread = threading.Thread(target=process_novel_task, args=(task_id, str(temp_path), api_key, chunk_size, max_tokens, str(output_dir)))
    thread.daemon = True
    thread.start()

    return jsonify({"task_id": task_id})

@app.route('/api/progress/<task_id>')
def progress(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"status": "not_found"}), 404
    return jsonify({
        "status": task["status"],
        "progress": task.get("progress", 0),
        "total_chunks": task.get("total_chunks", 0),
        "current_chunk": task.get("current_chunk", 0),
        "message": task.get("message", ""),
        "output_file": task.get("output_file", ""),
        "total_lines": task.get("total_lines", 0),
        "total_scenes": task.get("total_scenes", 0),
        "total_characters": task.get("total_characters", 0),
        "title": task.get("title", "")
    })

def open_browser():
    webbrowser.open("http://127.0.0.1:5000")

if __name__ == '__main__':
    threading.Timer(1.0, open_browser).start()
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)