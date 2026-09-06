import requests
import json
import sys

def test_kb_search():
    url = "http://localhost:8001/api/v1/chat/completions"
    api_key = "5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c"
    
    payload = {
        "agent_id": "knowledge-base",
        "messages": [
            {"role": "user", "content": "如何换电"}
        ],
        "stream": True
    }
    
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    print(f"发送请求到: {url} ...")
    try:
        response = requests.post(url, json=payload, headers=headers, stream=True)
        response.raise_for_status()
        
        print("\n收到响应 (流式):\n" + "-"*30)
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith('data: '):
                    data_str = decoded_line[6:]
                    if data_str == "[DONE]":
                        break
                    
                    data = json.loads(data_str)
                    
                    # 打印所有 event 类型用于调试
                    event_type = data.get("type", "unknown")
                    if event_type == "log":
                        title = data.get("title", "")
                        print(f"\n[LOG] {title}: {data.get('details')[:100]}...")
                    
                    # 检查不同类型的 chunk
                    if "content" in data:
                        print(data["content"], end="", flush=True)
                    elif data.get("type") == "log" and ("引用来源" in data.get("title", "") or "工具完成" in data.get("title", "")):
                        print(f"\n\n[发现引用数据]: {data['details'][:200]}...\n")
                    elif data.get("type") == "meta":
                        print(f"[元数据]: Agent={data.get('agent_name')}, Model={data.get('model')}\n")

    except Exception as e:
        print(f"\n请求失败: {e}")

if __name__ == "__main__":
    test_kb_search()
