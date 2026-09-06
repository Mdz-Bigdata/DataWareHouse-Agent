import json
import asyncio
import httpx
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_ragflow_search():
    # 用户提供的配置
    url = "https://yunshu-ragflow.yovole.net/api/v1/retrieval"
    api_key = "ragflow-zyLST0bimbRsbAMrf6J6h-gQxFlrcZ7XvpQlGxlxLks"
    
    payload = {
        "question": "查询标识为1.3.6.1.4.1.93450.1.2349.4_SH_JQ_01的点位实时数据和历史数据。",
        "dataset_ids": ["9300cf8c26ab11f198f852ead4b3b62c"],
        "top_k": 2,
        "page_size": 10,
        "similarity_threshold": 0.4,
        "vector_similarity_weight": 0.85
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    logger.info(f"发送检索请求到: {url}")
    logger.info(f"Payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")
    
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            
            logger.info(f"HTTP 状态码: {response.status_code}")
            
            if response.status_code == 200:
                res_json = response.json()
                logger.info(f"响应代码: {res_json.get('code')}")
                
                if res_json.get("code") == 0:
                    data = res_json.get("data", [])
                    # 兼容不同的返回格式
                    chunks = data if isinstance(data, list) else data.get("chunks", [])
                    
                    logger.info(f"成功获取到 {len(chunks)} 个数据块")
                    
                    for i, chunk in enumerate(chunks):
                        doc_name = (
                            chunk.get("document_keyword") or
                            chunk.get("doc_name") or 
                            chunk.get("docnm_kwd") or 
                            chunk.get("document_name") or 
                            "未知文档"
                        )
                        similarity = chunk.get("similarity", 0.0)
                        content = chunk.get("content_with_weight") or chunk.get("content") or "无内容"
                        
                        print(f"\n--- Result {i+1} (Similarity: {similarity:.4f}, Doc: {doc_name}) ---")
                        print(f"Content: {content[:200]}..." if len(content) > 200 else f"Content: {content}")
                else:
                    logger.error(f"RAGFlow 业务错误: {res_json.get('message')}")
            else:
                logger.error(f"HTTP 请求失败: {response.text}")
                
        except Exception as e:
            logger.error(f"测试过程中发生异常: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_ragflow_search())
