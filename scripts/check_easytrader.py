"""检查 easytrader 能检测到哪些券商客户端"""
import os
import subprocess
import sys

# 将项目根目录 ai-trader 加入 sys.path，确保核心模块可被引用
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from core.logger_config import logger

logger.info("=== 检查常见路径 ===")
common_paths = [
    r"C:\Users\a2515\海软\同花顺\stock",
    r"C:\同花顺",
    r"C:\HMSOFT\hmstock",
    r"C:\THSHJ\stock",
    r"C:\Program Files\同花顺",
    r"C:\Program Files (x86)\同花顺",
    r"C:\HMSOFT\hmstock\hmstk.exe",
    r"C:\Users\a2515\海软\同花顺\stock\hmstk.exe",
    r"C:\Users\a2515\AppData\Local\HmStk\stock",
    r"C:\Users\a2515\海软\同花顺",
    r"D:\海软\同花顺\stock",
    r"D:\同花顺",
]

found_any = False
for p in common_paths:
    exists = os.path.exists(p)
    if exists:
        found_any = True
        logger.info(f"  [FOUND] {p}")
        if os.path.isdir(p):
            files = os.listdir(p)
            logger.info(f"         files: {files[:15]}")
    else:
        logger.info(f"         {p}")

if not found_any:
    logger.info("\n  ⚠️ 常见路径都没找到，可能是：")
    logger.info("    1. 同花顺装在非标准路径")
    logger.info("    2. 同花顺没安装")
    logger.info("    3. 同花顺装在D盘")

# 扫描 D 盘和 C:/Users 下的 exe
logger.info("\n=== 扫描用户目录 exe ===")
try:
    result = subprocess.run(
        ['find', '/c/Users/a2515', '-maxdepth', '4', '-name', '*.exe'],
        capture_output=True, text=True, timeout=10
    )
    exe_paths = result.stdout.strip().split('\n') if result.stdout.strip() else []
    keywords = ['th', 'hmstk', 'hmstock', 'ths', 'stock']
    matches = []
    for ep in exe_paths:
        for kw in keywords:
            if kw.lower() in ep.lower():
                matches.append(ep)
                break
    if matches:
        logger.info(f"  找到 {len(matches)} 个相关文件:")
        for m in matches[:10]:
            logger.info(f"    {m}")
    else:
        logger.info("  未找到含 th/hmkst 等关键词的 exe")
except Exception as e:
    logger.exception(f"  scan error: {e}")

logger.info("\n=== easytrader 模块状态 ===")
try:
    import easytrader
    logger.info("  ✅ easytrader installed OK")
    logger.info("  支持券商: ths(同花顺), tdx(通达信), zq(中泰), wk(五矿), gf(广发), yt(银河), hs(华泰), csc(中信), cs(长城), gy(国金), dz(招商), df(东方)")
except ImportError:
    logger.info("  ❌ easytrader NOT installed")
    sys.exit(1)

logger.info("\n=== 下一步 ===")
logger.info("  如果你装了同花顺，请告诉我安装路径")
logger.info("  如果没装，可以：")
logger.info("    1. 安装同花顺免费版（只需行情+交易）")
logger.info("    2. 先用模拟盘运行")
logger.info("    3. 用通达信替代")
