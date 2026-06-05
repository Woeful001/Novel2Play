import os
import json
import argparse
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

SYSTEM_PROMPT = """
你是一个专业的剧本改编专家。请将小说片段转换为**分场剧本**，输出严格的 JSON 格式，不要有任何额外文字。

输出 JSON 结构如下：
{
    "title": "剧本标题（根据内容推断）",
    "characters": [
        {"name": "角色名", "description": "一句话外貌/性格/身份特点"}
    ],
    "scenes": [
        {
            "scene_id": 1,
            "location": "地点",
            "time": "时间段（如白天/夜晚/雨夜）",
            "description": "场景的视觉和氛围描述（一两句话）",
            "elements": [
                { "type": "action", "content": "动作描述（无对话时的行为、表情、环境变化）" },
                { "type": "line", "speaker": "说话人", "text": "台词原文", "action": "说话时的动作", "emotion": "情绪" },
                { "type": "narrate", "content": "旁白/内心独白/画外音（只在必要时使用）", "voice": "语气" }
            ]
        }
    ]
}

**创作要求**：
1. **按原文的时间顺序划分场景**：地点或时间变化时，开启新 scene。每个 scene 的 elements 数组按原文顺序依次放入动作、对白、旁白。
2. **旁白（narrate）尽可能少**：除非是重要的心理活动、无法表演的环境描写或过渡说明，否则不要使用旁白。能用动作和对白表现的，一律写成 action 或 line。
3. **动作要具体**：写清楚角色做了什么、表情如何、与环境的互动。例如“安子低下头，手指抠着盒子边缘”而不是“安子不高兴”。
4. **对白保留原文**，并为每句对白添加简单的动作和情绪。
5. **角色描述精简**：每个角色用一句话点明外貌、身份、性格即可，不要展开长篇。
6. **每段小说文本尽量输出 3~5 个 scene**，每个 scene 的 elements 数量不限，但要保证原文所有重要情节都覆盖。

**输入的小说片段**：
"""


def read_novel(file_path: str) -> str:
    """读取小说文本文件"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"小说文件不存在: {file_path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def call_deepseek(novel_content: str, api_key: str, max_tokens: int = 4000) -> dict:
    """调用 DeepSeek API，返回解析后的 JSON 对象"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": novel_content}
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"}  # 强制 JSON 输出
    }

    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        # 提取模型返回的内容
        content = result["choices"][0]["message"]["content"]
        # 解析 JSON
        data = json.loads(content)
        return data
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"API 请求失败: {e}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"模型返回的不是有效 JSON: {e}\n原始内容: {content}")


def save_to_yaml(data: dict, output_path: str):
    """将字典保存为 YAML 文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, indent=2)
    print(f"✅ 剧本已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="AI小说转剧本工具")
    parser.add_argument("--input", "-i", default="novel.txt", help="输入小说文件路径 (默认: novel.txt)")
    parser.add_argument("--output", "-o", default="script.yaml", help="输出 YAML 文件路径 (默认: script.yaml)")
    parser.add_argument("--max-tokens", type=int, default=8000, help="API 最大输出 token 数 (默认: 5000)")
    args = parser.parse_args()

    # 检查 API Key
    if not DEEPSEEK_API_KEY:
        print("❌ 错误: 未找到 DeepSeek API Key。")
        print("   请复制 .env.example 为 .env，并填入你的真实 API Key。")
        return

    # 1. 读取小说
    try:
        print(f"📖 正在读取小说: {args.input}")
        novel_text = read_novel(args.input)
        if len(novel_text) < 50:
            print("⚠️ 警告: 小说内容过短，可能影响转换效果。")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        print("   请确保小说文件存在，或使用 --input 指定路径。")
        return

    # 2. 调用 DeepSeek
    print(f"🤖 正在调用 DeepSeek API (最长等待 {args.max_tokens} tokens)...")
    try:
        script_data = call_deepseek(novel_text, DEEPSEEK_API_KEY, args.max_tokens)
    except RuntimeError as e:
        print(f"❌ {e}")
        return

    # 3. 保存 YAML
    save_to_yaml(script_data, args.output)

    # 4. 输出简要统计
    print("\n📊 转换统计:")
    print(f"   - 角色数: {len(script_data.get('characters', []))}")
    print(f"   - 场景数: {len(script_data.get('scenes', []))}")
    print(f"   - 台词数: {len(script_data.get('lines', []))}")


if __name__ == "__main__":
    main()