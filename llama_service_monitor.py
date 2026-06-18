"""
LLaMA Server Monitor - Windows 背景服务监控器
功能：
1. 监控 llama-server 进 trình状态
2. 自动重啟功能
3. 健康检查
4. 请求排队与处理
5. 性能记录
"""

import asyncio
import aiohttp
import psutil
import time
import json
import signal
import sys
import platform
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import subprocess

# 尝试导入 threading 用于 Windows 服务
try:
    import threading
    import servicemanager
    SERVICE_AVAILABLE = True
except ImportError:
    SERVICE_AVAILABLE = False
    threading = None


class ServerState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RESTARTING = "restarting"
    CRASHED = "crashed"


@dataclass
class RequestLog:
    """请求记录"""
    timestamp: str
    prompt_length: int
    max_tokens: int
    duration: float
    retry_attempts: int
    success: bool
    model: str
    error: Optional[str] = None


class LLamaServerMonitor:
    def __init__(self):
        self.state = ServerState.STOPPED
        self.pid = None
        self.process = None
        self.running = False
        self.request_queue = []
        self.request_logs: List[RequestLog] = []
        self.lock = asyncio.Lock()
        
        # 服务配置
        self.config = {
            "llama_binary": "H:\\llama-b9616-bin-win-cuda-12.4-x64\\llama-server.exe",
            "model_path": "C:\\Users\\a2515\\ai-trader\\models\\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL.gguf",
            "host": "127.0.0.1",
            "port": 8080,
            "health_check_interval": 30,
            "restart_delay": 10,
            "max_parallel": 2,  # 由于--parallel 1，外部控制
        }
        
        # 模型参数
        self.model_config = {
            "ctx_size": 32768,
            "n_gpu_layers": 32,
            "n_batch": 512,
            "n_ubatch": 512,
        }
    
    def is_service_available(self) -> bool:
        """检查是否支持 Windows 服务"""
        return SERVICE_AVAILABLE
    
    async def start_server(self):
        """启动 llama-server"""
        if self.state == ServerState.RUNNING:
            print(f"[INFO] Server already running (PID: {self.pid})")
            return
        
        print(f"[INFO] Starting llama-server...")
        self.state = ServerState.STARTING
        
        cmd = [
            self.config["llama_binary"],
            f"-m {self.config['model_path']}",
            f"--ctx-size {self.config['ctx_size']}",
            f"-ngl {self.config['n_gpu_layers']}",
            f"-b {self.config['n_batch']}",
            f"-ub {self.config['n_ubatch']}",
            "--parallel 1",
            f"--host {self.config['host']}",
            f"--port {self.config['port']}",
            "--no-chunked-preprocessing",
        ]
        
        try:
            # 在背景启动
            self.process = subprocess.Popen(
                 cmd,
                 creationflags=subprocess.CREATE_NO_WINDOW,
                 stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE,
             )
            self.pid = self.process.pid
            
            self.state = ServerState.RUNNING
            self.running = True
            print(f"[INFO] Server started with PID: {self.pid}")
            
            # 等待服务 ready
            await self.wait_for_ready()
            
        except Exception as e:
            print(f"[ERROR] Failed to start server: {e}")
            self.state = ServerState.CRASHED
            self.running = False
            raise
    
    async def wait_for_ready(self, timeout: int = 15):
        """等待服务 ready"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # 尝试连接服务
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                    async with session.get(f"http://{self.config['host']}:{self.config['port']}/v1") as resp:
                        if resp.status == 200:
                            print("[INFO] Server is ready and healthy")
                            return True
            except Exception as e:
                pass
            
            await asyncio.sleep(1)
        
        print(f"[WARN] Server ready timeout ({timeout}s)")
        return False
    
    def is_process_alive(self) -> bool:
        """检查进 trình是否存活"""
        if self.pid is None:
            return False
        
        try:
            process = psutil.Process(self.pid)
            return process.is_alive()
        except psutil.NoSuchProcess:
            return False
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"http://{self.config['host']}:{self.config['port']}/health") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("status") == "healthy"
        except:
            pass
        return False
    
    async def check_and_restart(self):
        """检查服务状态并必要_时重启"""
        if not self.running or self.state != ServerState.RUNNING:
            return
        
        # 检查进 trình
        if not self.is_process_alive():
            print(f"[WARN] Server process died (PID: {self.pid}), restarting...")
            await self.stop_server()
            await asyncio.sleep(self.config["restart_delay"])
            await self.start_server()
            return
        
        # 健康检查
        is_healthy = await self.health_check()
        if not is_healthy:
            print("[WARN] Server health check failed, restarting...")
            await self.stop_server()
            await asyncio.sleep(self.config["restart_delay"])
            await self.start_server()
    
    async def stop_server(self):
        """停止服务"""
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                try:
                    self.process.kill()
                except:
                    pass
            
            self.process = None
            self.pid = None
        
        self.state = ServerState.STOPPED
        self.running = False
        print("[INFO] Server stopped")
    
    async def process_request(self, prompt: str, max_tokens: int = 1024, model: str = "uncensored"):
        """处理单条请求"""
        async with self.lock:
            if not self.running or self.state != ServerState.RUNNING:
                print("[WARN] Cannot process request: server not running")
                return
            
            start_time = time.time()
            retry_count = 0
            max_retries = 3
            last_error = None
            
            while retry_count < max_retries:
                try:
                    headers = {"Content-Type": "application/json"}
                    data = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0.7,
                        "top_p": 0.9,
                    }
                    
                    timeout = aiohttp.ClientTimeout(
                        total=self.config["health_check_interval"],
                        connect=10,
                    )
                    
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(
                            f"http://{self.config['host']}:{self.config['port']}/v1/chat/completions",
                            headers=headers,
                            json=data,
                        ) as resp:
                            if resp.status == 200:
                                end_time = time.time()
                                # 诵略计算 token
                                prompt_tokens = len(prompt) // 4
                                
                                log = RequestLog(
                                    timestamp=datetime.now().isoformat(),
                                    prompt_length=len(prompt),
                                    max_tokens=max_tokens,
                                    duration=end_time - start_time,
                                    retry_attempts=retry_count,
                                    success=True,
                                    model=model,
                                )
                                self.request_logs.append(log)
                                
                                print(f"[SUCCESS] Request completed in {log.duration:.2f}s "
                                      f"(queue_pos={len(self.request_queue)+1})")
                                return log
                            else:
                                last_error = f"HTTP {resp.status}"
                                
                except asyncio.TimeoutError:
                    last_error = f"Timeout after {self.config['health_check_interval']}s"
                except Exception as e:
                    last_error = str(e)
                
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = 2 ** retry_count
                    print(f"[INFO] Retry {retry_count}/{max_retries} in {wait_time}s")
                    await asyncio.sleep(wait_time)
            
            # 所有重試失败
            end_time = time.time()
            log = RequestLog(
                timestamp=datetime.now().isoformat(),
                prompt_length=len(prompt),
                max_tokens=max_tokens,
                duration=end_time - start_time,
                retry_attempts=retry_count,
                success=False,
                model=model,
                error=last_error,
            )
            self.request_logs.append(log)
            print(f"[ERROR] Request failed after {retry_count} retries: {last_error}")
            return log
    
    def add_request(self, prompt: str, max_tokens: int = 1024, model: str = "uncensored"):
        """加入请求排队"""
        self.request_queue.append({"prompt": prompt, "max_tokens": max_tokens, "model": model})
        print(f"[INFO] Request queued. Queue size: {len(self.request_queue)}")
    
    async def run(self):
        """主循环"""
        print("=" * 60)
        print("LLaMA Server Monitor Started")
        print(f"Platform: {platform.system()} {platform.release()}")
        print(f"Service mode: {'Enabled' if self.is_service_available() else 'Not available'}")
        print("=" * 60)
        
        # 信号处理
        loop = asyncio.get_running_loop()
        
        def signal_handler():
            print("\n[INFO] Received shutdown signal")
            self._shutdown()
        
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)
        
        try:
            # 主baiki
            while self.running:
                # 处理请求
                if self.request_queue:
                    request_data = self.request_queue.pop(0)
                    await self.process_request(
                        request_data["prompt"],
                        request_data.get("max_tokens", 1024),
                        request_data.get("model", "uncensored")
                    )
                
                # 健康检查
                await self.check_and_restart()
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            print("\n[INFO] Interrupted by user")
        except Exception as e:
            print(f"[ERROR] Critical error: {e}")
        finally:
            await self._shutdown()
    
    def _shutdown(self):
        """关闭所有资源"""
        self.running = False
        if self.process:
            self.stop_server()
        self.state = ServerState.STOPPED
        print("[INFO] Monitor shutdown complete")


def main():
    """CLI 入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="LLaMA Server Monitor")
    parser.add_argument("--start", action="store_true", help="启动服务")
    parser.add_argument("--stop", action="store_true", help="停止服务")
    parser.add_argument("--status", action="store_true", help="查看状态")
    parser.add_argument("--model", default="uncensored", help="模型选择")
    
    args = parser.parse_args()
    
    monitor = LLamaServerMonitor()
    
    if args.start:
        print("[INFO] Starting llama-server...")
        asyncio.run(monitor.start_server())
        print("[INFO] Server started successfully")
    
    elif args.stop:
        print("[INFO] Stopping llama-server...")
        asyncio.run(monitor.stop_server())
        print("[INFO] Server stopped")
    
    elif args.status:
        print(f"State: {monitor.state.value}")
        print(f"PID: {monitor.pid}")
        print(f"Running: {monitor.running}")
        print(f"Queue size: {len(monitor.request_queue)}")
    
    else:
        # 运行模式
        asyncio.run(monitor.run())


if __name__ == "__main__":
    main()