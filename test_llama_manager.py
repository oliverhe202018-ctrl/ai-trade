"""
Simple test script for LLaMA server manager
"""
import asyncio
import aiohttp
import json


async def test_server():
    """Test connection to llama-server"""
    host = "127.0.0.1"
    port = 8080
    
    print(f"Testing connection to {host}:{port}...")
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            # 健康检查
            async with session.get(f"http://{host}:{port}/health") as resp:
                print(f"Health check status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    print(f"Health data: {json.dumps(data, indent=2)}")
                
                # 尝试获取模型列表
                async with session.get(f"http://{host}:{port}/v1") as resp:
                    print(f"Models endpoint status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"Models: {json.dumps(data, indent=2)}")
                
                # 测试 chat
                data = {
                    "model": "qwen_uncensored",
                    "messages": [
                        {"role": "user", "content": "你好，请给我写liny Python 代码，计算 1 到 100 的累计和。"}
                    ],
                    "max_tokens": 100,
                }
                
                async with session.post(
                    f"http://{host}:{port}/v1/chat/completions",
                    json=data,
                ) as resp:
                    print(f"Chat completion status: {resp.status}")
                    if resp.status == 200:
                        result = await resp.json()
                        print(f"Response: {json.dumps(result, indent=2)[:500]}...")
                    else:
                        error = await resp.text()
                        print(f"Error: {error[:200]}")
                        
    except Exception as e:
        print(f"Error: {e}")
        print("Make sure llama-server is running first!")


if __name__ == "__main__":
    asyncio.run(test_server())