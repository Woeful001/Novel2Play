import os
import json
import argparse
from pathlib import Path
from collections import OrderedDict

import requests
import yaml
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

# 尝试导入 tkinter（用于文件选择对话框）
try:
    import tkinter as tk
    from tkinter import filedialog
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# 基础提示词（不含状态部分）
BASE_SYSTEM_PROMPT = """
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
"""


class State:
    def __init__(self):
        self.characters = OrderedDict()   # name -> description
        self.next_scene_id = 1
        self.last_summary = ""            # 上一段的剧情摘要
        self.global_title = "未命名"

    def update_from_script(self, script_data):
        """从模型返回的剧本中提取信息，更新状态"""
        # 更新标题（优先用第一个非空的）
        if script_data.get("title") and self.global_title == "未命名":
            self.global_title = script_data["title"]

        # 合并角色
        for char in script_data.get("characters", []):
            name = char["name"]
            if name not in self.characters:
                self.characters[name] = char["description"]
            else:
                # 如果新描述更长，更新
                if len(char.get("description", "")) > len(self.characters[name]):
                    self.characters[name] = char["description"]

        # 更新下一个场景编号（取所有场景中最大的 scene_id + 1）
        max_id = max([s.get("scene_id", 0) for s in script_data.get("scenes", [])], default=0)
        if max_id >= self.next_scene_id:
            self.next_scene_id = max_id + 1

        # 更新剧情摘要：取本段最后几个场景的简短描述（最多200字）
        if script_data.get("scenes"):
            last_scene = script_data["scenes"][-1]
            summary = f"场景{last_scene.get('scene_id')}：{last_scene.get('location')}，{last_scene.get('description')}。"
            # 再加上最后两句台词或动作
            elements = last_scene.get("elements", [])
            last_elems = [e for e in elements if e.get("type") in ("line", "action")][-2:]
            for e in last_elems:
                if e["type"] == "line":
                    summary += f" {e['speaker']}说：“{e['text'][:30]}”"
                else:
                    summary += f" {e['content'][:30]}"
            self.last_summary = summary[:200]

    def get_context_prompt(self):
        """生成供模型使用的上下文提示"""
        if not self.characters and self.next_scene_id == 1:
            return ""  # 第一次调用，无需上下文

        context = "\n【已有角色列表】（请复用，不要改名）：\n"
        for name, desc in self.characters.items():
            context += f"- {name}：{desc}\n"

        context += f"\n【下一个场景编号从 {self.next_scene_id} 开始】\n"

        if self.last_summary:
            context += f"\n【上一段剧情摘要】：{self.last_summary}\n"

        context += "\n请继续处理以下小说片段，保持角色和情节一致。\n"
        return context


def read_novel(file_path: str) -> str:
    """
    读取多种格式的小说文件，返回纯文本内容。
    支持格式：.txt, .md, .docx, .pdf, .epub
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    suffix = path.suffix.lower()

    # 纯文本格式
    if suffix in ('.txt', '.md'):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    # Microsoft Word
    elif suffix == '.docx':
        if docx is None:
            raise ImportError("需要安装 python-docx 库：pip install python-docx")
        doc = docx.Document(path)
        paragraphs = [p.text for p in doc.paragraphs]
        return '\n'.join(paragraphs)

    # PDF
    elif suffix == '.pdf':
        if pdfplumber is None:
            raise ImportError("需要安装 pdfplumber 库：pip install pdfplumber")
        text = ''
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
        return text

    # EPUB
    elif suffix == '.epub':
        if ebooklib is None:
            raise ImportError("需要安装 EbookLib 库：pip install EbookLib")
        book = epub.read_epub(path)
        text = ''
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                content = item.get_body_content().decode('utf-8', errors='ignore')
                # 去除简单 HTML 标签
                import re
                clean = re.sub(r'<[^>]+>', ' ', content)
                text += clean + '\n'
        return text

    else:
        raise ValueError(f"不支持的文件格式: {suffix}，目前支持 .txt, .md, .docx, .pdf, .epub")


def split_text(text, max_chars=4000):
    """按段落切分，每块不超过 max_chars"""
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


def call_deepseek(prompt: str, api_key: str, max_tokens: int = 8000) -> dict:
    """调用 API，prompt 是 user 消息内容（包含上下文和小说片段）"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",  # 使用 chat 模型，性价比高
        "messages": [
            {"role": "system", "content": BASE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"}
    }
    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        # 清理 markdown
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        return json.loads(content)
    except Exception as e:
        raise RuntimeError(f"API 调用失败: {e}")


def select_file() -> str | None:
    """弹出文件选择对话框，返回选中的文件路径，若取消则返回 None"""
    if not TKINTER_AVAILABLE:
        print("⚠️ tkinter 不可用（可能在没有图形界面的环境中运行），请通过命令行参数 --input 指定文件。")
        return None

    root = tk.Tk()
    root.withdraw()  # 隐藏主窗口
    # 设置支持的文件类型
    filetypes = [
        ("所有支持的文件", "*.txt *.md *.docx *.pdf *.epub"),
        ("文本文件", "*.txt *.md"),
        ("Word 文档", "*.docx"),
        ("PDF 文件", "*.pdf"),
        ("EPUB 电子书", "*.epub"),
        ("所有文件", "*.*")
    ]
    file_path = filedialog.askopenfilename(
        title="请选择小说文件",
        filetypes=filetypes
    )
    root.destroy()
    return file_path if file_path else None


def main():
    parser = argparse.ArgumentParser(description="AI小说转剧本工具（支持多格式 + 图形化文件选择）")
    parser.add_argument("--input", "-i", help="输入小说文件路径（如果未提供，将弹出文件选择对话框）")
    parser.add_argument("--output", "-o", default="script.yaml", help="输出 YAML 文件")
    parser.add_argument("--max-tokens", type=int, default=8000, help="每块 API 最大输出 token")
    parser.add_argument("--chunk-size", type=int, default=4000, help="每块文本最大字符数")
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("❌ 请配置 .env 中的 DEEPSEEK_API_KEY")
        return

    # 确定输入文件路径
    input_file = args.input
    if not input_file:
        # 尝试弹出图形化选择对话框
        print("🔍 未提供 --input 参数，正在打开文件选择对话框...")
        input_file = select_file()
        if not input_file:
            print("❌ 未选择任何文件，程序退出。")
            return
        print(f"✅ 已选择文件: {input_file}")

    # 读取并解析小说内容
    try:
        full_text = read_novel(input_file)
        print(f"📖 成功读取文件，共 {len(full_text)} 字符")
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return

    chunks = split_text(full_text, max_chars=args.chunk_size)
    print(f"📖 原文 {len(full_text)} 字符，拆分为 {len(chunks)} 块")

    state = State()
    all_scripts = []  # 保存每个块返回的原始剧本（用于最终合并）

    for idx, chunk in enumerate(chunks, 1):
        print(f"\n🔄 处理第 {idx}/{len(chunks)} 块...")
        context = state.get_context_prompt()
        user_prompt = f"{context}\n【待处理的小说片段】：\n{chunk}"
        try:
            script = call_deepseek(user_prompt, DEEPSEEK_API_KEY, args.max_tokens)
            if "scenes" not in script:
                script["scenes"] = []
            if "characters" not in script:
                script["characters"] = []
            all_scripts.append(script)
            state.update_from_script(script)
            print(f"   ✅ 完成，本块场景数 {len(script['scenes'])}，累计角色 {len(state.characters)}")
        except Exception as e:
            print(f"   ❌ 失败：{e}")
            return

    # 合并所有块的剧本
    final_scenes = []
    for script in all_scripts:
        final_scenes.extend(script.get("scenes", []))
    final_characters = [{"name": n, "description": d} for n, d in state.characters.items()]

    final_script = {
        "title": state.global_title,
        "characters": final_characters,
        "scenes": final_scenes
    }

    # 保存 YAML
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(final_script, f, allow_unicode=True, sort_keys=False, indent=2)

    total_lines = sum(
        1 for scene in final_scenes
        for elem in scene.get("elements", [])
        if elem.get("type") == "line"
    )
    print("\n📊 最终统计:")
    print(f"   - 总角色: {len(final_characters)}")
    print(f"   - 总场景: {len(final_scenes)}")
    print(f"   - 总台词: {total_lines}")
    print(f"✅ 剧本保存至 {args.output}")


if __name__ == "__main__":
    main()