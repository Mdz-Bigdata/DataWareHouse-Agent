from pathlib import Path


def load_prompt(name: str) -> str:
    """
    从指定目录下读取提示词文件返回提示词内容
    :param name: 文件名称
    :return:
    """
    prompt_path = Path(__file__).parents[2] / 'prompts' / f"{name}.prompt"
    return prompt_path.read_text(encoding="utf-8")

if __name__ == '__main__':
    print(load_prompt("correct_sql"))
