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

# 系统提示词（固定，引导模型输出 JSON 格式的剧本结构）
SYSTEM_PROMPT = """
你是一个专业的剧本结构化专家。你的任务是将小说片段转换为结构化的剧本数据。

请严格按照以下 JSON 格式输出，不要输出任何其他文字、注释或 Markdown 标记：
{
    "title": "小说标题（如果无法确定，使用'未知标题'）",
    "characters": [
        {"name": "角色名", "description": "简短描述"}
    ],
    "scenes": [
        {"location": "地点", "time": "时间（如白天/夜晚）", "description": "场景描述"}
    ],
    "lines": [
        {"speaker": "说话人", "text": "台词内容", "action": "动作描述（可留空字符串）"}
    ]
}

要求：
- 从文本中提取所有出现的主要角色。
- 根据对话发生的环境推断场景。
- 将对话转换为台词，叙述性文字可适当转换为动作描述。
- 如果信息不足，某些字段可以为空数组或空字符串，但必须保持 JSON 结构完整。
"""


def read_novel(file_path: str) -> str:
    """读取小说文本文件"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"小说文件不存在: {file_path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def call_deepseek(novel_content: str, api_key: str, max_tokens: int = 2000) -> dict:
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
    parser.add_argument("--max-tokens", type=int, default=2000, help="API 最大输出 token 数 (默认: 2000)")
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