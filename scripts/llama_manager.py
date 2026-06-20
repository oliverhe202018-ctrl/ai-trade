"""
LLaMA Server Manager - 专业的 llama-server 管理_wrapper
功能：
1. 服务监控与自动_restart
2. 请求排队与并ماية控制
3. HTTP 请求 timeouts 管理
4. 请求指标记录（tokens、latency、retry）
5. 健康检查与自动恢复
"""

import asyncio
import aiohttp
import json
import time
import signal
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

# 将项目根目录 ai-trader 加入 sys.path，确保核心模块可被引用
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from core.logger_config import logger
from dataclasses import dataclass, asdict
from enum import Enum
import psutil
import subprocess
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class ServerStatus(Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    RESTARTING = "restarting"


@dataclass
class RequestMetric:
    """单条请求的指标"""
    timestamp: str
    prompt_tokens: int
    max_tokens: int
    duration_seconds: float
    retry_count: int
    success: bool
    model: str
    error_message: Optional[str] = None


@dataclass
class ServerConfig:
    """服务配置"""
    host: str = "127.0.0.1"
    port: int = 8080
    max_parallel: int = 4  # 最大并发生成数
    read_timeout: int = 300  # 读 timeout 180-300 秒
    connection_timeout: int = 30  # 连接 timeout
    health_check_interval: int = 30  # 健康检查间隔
    restart_delay: int = 10  # 重啟 delay


class LLamaServerManager:
    def __init__(self, config: ServerConfig = None):
        self.config = config or ServerConfig()
        self.server_process: Optional[subprocess.Popen] = None
        self.server_status = ServerStatus.STOPPED
        self.running = False
        self.request_metrics: List[RequestMetric] = []
        self.request_queue: List[Dict[str, Any]] = []
        self.semaphore = asyncio.Semaphore(self.config.max_parallel)
        self._shutdown_event = asyncio.Event()
        
        # 模型配置：从环境变量读取，缺失则抛出异常
        self.server_path = os.getenv("LLAMA_SERVER_PATH")
        if not self.server_path:
            raise RuntimeError("LLAMA_SERVER_PATH 未配置。请在 .env 文件中设置。")
        
        self.models = {
            "qwen_uncensored": {
                "model_path": os.getenv("LLAMA_MODEL_UNCERTAINED", ""),
                "ctx_size": 32768,
                "n_batch": 512,
                "n_ubatch": 512,
                "n_gpu_layers": 32,
            },
            "qwen_normal": {
                "model_path": os.getenv("LLAMA_MODEL_NORMAL", ""),
                "ctx_size": 131072,
                "n_batch": 8192,
                "n_ubatch": 8192,
                "n_gpu_layers": 26,
            },
        }
    
    async def start_server(self, model_key: str = "qwen_uncensored"):
        """启动 llama-server 服务"""
        if self.server_status == ServerStatus.RUNNING:
            logger.info("[INFO] Server already running")
            return
        
        logger.info(f"[INFO] Starting llama-server with model: {model_key}")
        
        config = self.models.get(model_key, self.models["qwen_uncensored"])
        model_path = config['model_path']
        if not model_path:
            raise RuntimeError(f"模型 {model_key} 路径未配置。请在 .env 文件中设置。")
        
        cmd = [
            self.server_path,
            f"-m {model_path}",
            f"--ctx-size {config['ctx_size']}",
            f"-n {config['n_batch']}",
            f"-ub {config['n_ubatch']}",
            f"-ngl {config['n_gpu_layers']}",
            f"--host {self.config.host}",
            f"--port {self.config.port}",
            "--parallel 1",  # 单并发生成，通过外部排队控制
            "--no-chunked-preprocessing",
        ]
        
        try:
            self.server_process = subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.server_status = ServerStatus.RUNNING
            self.running = True
            logger.info(f"[INFO] Server started with PID: {self.server_process.pid}")
            
            # 等待服务 ready
            await self._wait_for_server_ready()
            
        except Exception as e:
            logger.exception(f"[ERROR] Failed to start server: {e}")
            self.server_status = ServerStatus.STOPPED
            raise
    
    async def _wait_for_server_ready(self, timeout: int = 10):
        """等待服务 ready"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                    async with session.get(f"http://{self.config.host}:{self.config.port}/v1") as resp:
                        if resp.status == 200:
                            logger.info("[INFO] Server is ready")
                            return
            except:
                pass
            await asyncio.sleep(1)
        
        logger.info("[WARN] Server ready timeout")
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"http://{self.config.host}:{self.config.port}/health") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("status") == "healthy"
        except:
            pass
        return False
    
    async def check_server_alive(self) -> bool:
        """检查服务是否存活"""
        if self.server_process is None:
            return False
        try:
            process = psutil.Process(self.server_process.pid)
            return process.is_alive()
        except psutil.NoSuchProcess:
            return False
    
    async def restart_server_if_needed(self):
        """根据健康检查结果自动重启"""
        if not self.running or self.server_status != ServerStatus.RUNNING:
            return
        
        # 健康检查
        is_healthy = await self.health_check()
        server_alive = await self.check_server_alive()
        
        if not is_healthy or not server_alive:
            logger.info(f"[WARN] Server unhealthy or dead, restarting...")
            await self.stop_server()
            await asyncio.sleep(self.config.restart_delay)
            await self.start_server()
    
    async def make_request(self, prompt: str, max_tokens: int, model: str = "qwen_uncensored") -> RequestMetric:
        """发送请求并记录指标"""
        async with self.semaphore:  # 控制并发生成数
            retry_count = 0
            max_retries = 3
            last_error = None
            
            while retry_count < max_retries:
                try:
                    # 记录开始时间
                    start_time = time.time()
                    
                    headers = {
                        "Content-Type": "application/json",
                    }
                    data = {
                        "model": model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.7,
                        "top_p": 0.9,
                    }
                    
                    timeout = aiohttp.ClientTimeout(
                        total=self.config.read_timeout,
                        connect=self.config.connection_timeout,
                    )
                    
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(
                            f"http://{self.config.host}:{self.config.port}/v1/chat/completions",
                            headers=headers,
                            json=data,
                        ) as resp:
                            if resp.status == 200:
                                result = await resp.json()
                                end_time = time.time()
                                
                                # 计算 token 数（简化计算）
                                prompt_tokens = len(prompt) // 4  # 简化：每 4 字符 1 token
                                
                                metric = RequestMetric(
                                    timestamp=datetime.now().isoformat(),
                                    prompt_tokens=prompt_tokens,
                                    max_tokens=max_tokens,
                                    duration_seconds=end_time - start_time,
                                    retry_count=retry_count,
                                    success=True,
                                    model=model,
                                )
                                
                                self.request_metrics.append(metric)
                                logger.info(f"[SUCCESS] Request completed in {metric.duration_seconds:.2f}s "
                                      f"(tokens: {prompt_tokens}/{max_tokens})")
                                return metric
                            else:
                                last_error = f"HTTP {resp.status}: {await resp.text()}"
                                logger.info(f"[WARN] Request failed: {last_error}")
                                
                except asyncio.TimeoutError:
                    last_error = f"Timeout after {self.config.read_timeout}s"
                    logger.info(f"[WARN] Request timeout: {last_error}")
                except Exception as e:
                    last_error = str(e)
                    logger.exception(f"[ERROR] Request error: {e}")
                
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = 2 ** retry_count  # 指数退避
                    logger.info(f"[INFO] Retrying in {wait_time}s (attempt {retry_count}/{max_retries})")
                    await asyncio.sleep(wait_time)
            
            # 所有重試失败
            end_time = time.time()
            metric = RequestMetric(
                timestamp=datetime.now().isoformat(),
                prompt_tokens=len(prompt) // 4,
                max_tokens=max_tokens,
                duration_seconds=end_time - start_time,
                retry_count=retry_count,
                success=False,
                model=model,
                error_message=last_error,
            )
            self.request_metrics.append(metric)
            logger.info(f"[ERROR] Request failed after {retry_count} retries: {last_error}")
            return metric
    
    async def process_request_queue(self):
        """处理请求排队"""
        while self.running or self.request_queue:
            if self.request_queue:
                # 获取下一个请求
                request_data = self.request_queue.pop(0)
                prompt = request_data.get("prompt", "")
                max_tokens = request_data.get("max_tokens", 1024)
                model = request_data.get("model", "qwen_uncensored")
                
                logger.info(f"[INFO] Processing request: {len(prompt)} chars, model={model}")
                await self.make_request(prompt, max_tokens, model)
            
            # 健康检查与自动重启
            await self.check_server_alive()
            if not self.check_server_alive():
                await self.restart_server_if_needed()
            
            await asyncio.sleep(1)
    
    def add_request_to_queue(self, prompt: str, max_tokens: int = 1024, model: str = "qwen_uncensored"):
        """将请求加入排队"""
        self.request_queue.append({
            "prompt": prompt,
            "max_tokens": max_tokens,
            "model": model,
        })
        logger.info(f"[INFO] Request queued: {len(self.request_queue)} requests in queue")
    
    async def stop_server(self):
        """停止服务"""
        if self.server_process is not None:
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=5)
            except:
                try:
                    self.server_process.kill()
                except:
                    pass
            self.server_process = None
        
        self.server_status = ServerStatus.STOPPED
        self.running = False
        self._shutdown_event.set()
        logger.info("[INFO] Server stopped")
    
    async def run_forever(self):
        """主循环循环"""
        logger.info("[INFO] LLaMA Server Manager started")
        
        # 设置信号处理
        loop = asyncio.get_running_loop()
        
        def signal_handler():
            logger.info("\n[INFO] Received shutdown signal")
            self._shutdown_event.set()
        
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)
        
        # 启动服务
        await self.start_server()
        
        # 主循环：处理aksikan + 健康检查
        try:
            while not self._shutdown_event.is_set():
                await self.process_request_queue()
                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            await self.stop_server()
            logger.info("[INFO] Manager stopped")


def main():
    """CLI 入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="LLaMA Server Manager")
    parser.add_argument("--model", choices=["qwen_uncensored", "qwen_normal"],
                       default="qwen_uncensored", help="模型选择")
    parser.add_argument("--host", default="127.0.0.1", help="服务地址")
    parser.add_argument("--port", type=int, default=8080, help="服务_port")
    parser.add_argument("--max-parallel", type=int, default=4, help="最大并发生成数")
    
    args = parser.parse_args()
    
    config = ServerConfig(
        host=args.host,
        port=args.port,
        max_parallel=args.max_parallel,
    )
    
    manager = LLamaServerManager(config)
    
    # 示例请求
    manager.add_request_to_queue(
        "你好，请给我写首诗 Python 代码，实现一个＾＾為に计算 1 到 100 的累计和。",
        max_tokens=512,
        model=args.model
    )
    
    # 运行
    asyncio.run(manager.run_forever())


if __name__ == "__main__":
    main()