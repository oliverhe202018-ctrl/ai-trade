"""
Simple test script for LLaMA server manager
"""
import asyncio
import aiohttp
import json
import os
import sys

# 将项目根目录 ai-trader 加入 sys.path，确保核心模块可被引用
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from core.logger_config import logger


async def test_server():
    """Test connection to llama-server"""
    host = "127.0.0.1"
    port = 8080
    
    logger.info(f"Testing connection to {host}:{port}...")
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            # 健康检查
            async with session.get(f"http://{host}:{port}/health") as resp:
                logger.info(f"Health check status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"Health data: {json.dumps(data, indent=2)}")
                
                # 尝试获取模型列表
                async with session.get(f"http://{host}:{port}/v1") as resp:
                    logger.info(f"Models endpoint status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info(f"Models: {json.dumps(data, indent=2)}")
                
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
                    logger.info(f"Chat completion status: {resp.status}")
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info(f"Response: {json.dumps(result, indent=2)[:500]}...")
                    else:
                        error = await resp.text()
                        logger.info(f"Error: {error[:200]}")
                        
    except Exception as e:
        logger.exception(f"Error: {e}")
        logger.info("Make sure llama-server is running first!")


if __name__ == "__main__":
    asyncio.run(test_server())