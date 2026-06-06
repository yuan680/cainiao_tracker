"""
菜鸟国际物流批量查询 - 主程序

完整自动化流程：
1. 连接 WPS 多维表格（DrissionPage + JSSDK）
2. 读取所有物流单号
3. 通过 DrissionPage 模拟网页批量查询菜鸟国际物流
4. 批量写回查询结果
5. 等待2小时后自动重复

查询策略：
- 有单号 + 状态为空或非终态 → 查询
- 状态为终态（妥投/退回签收） → 跳过
- 不依赖断点进度，只看当前物流状态
- 清空状态后重新运行即可全量重查

运行模式：
- 默认为定时循环模式（每2小时自动查询一次）
- 支持开机自启（macOS LaunchAgent）

使用方式：
    python main.py              # 定时循环模式（每2小时查一次）
    python main.py --once       # 只查一次就退出
    python main.py --dry-run    # 仅读取，不查询不写入
    python main.py --install    # 安装开机自启服务
    python main.py --uninstall  # 卸载开机自启服务
"""

import os
import sys
import time
import random
import logging
import argparse
import platform
import subprocess
import shutil
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from wps_table import WPSTable
from cainiao_query import CainiaoTracker


# ==================== 定时配置 ====================
LOOP_INTERVAL_HOURS = 2  # 每2小时查询一次

# ==================== LaunchAgent 配置 ====================
LAUNCH_AGENT_LABEL = "com.cainiaotracker.autoquery"
LAUNCH_AGENT_PLIST = os.path.expanduser(
    f"~/Library/LaunchAgents/{LAUNCH_AGENT_LABEL}.plist"
)


def format_time(time_val):
    """
    格式化物流时间

    菜鸟返回的时间可能是:
    - 毫秒时间戳 (int): 1778826206000
    - 日期字符串: "2024-01-01 12:00:00"
    - 空值
    """
    if not time_val:
        return ''
    if isinstance(time_val, (int, float)):
        try:
            dt = datetime.fromtimestamp(time_val / 1000)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, OSError):
            return str(time_val)
    return str(time_val)


# ==================== 日志配置 ====================
def setup_logging():
    """配置日志（强制UTF-8输出避免GBK编码问题）"""
    log_format = '%(asctime)s [%(levelname)s] %(message)s'
    log_file = os.path.join(config.APP_DATA_DIR, config.LOG_FILE)

    # 文件handler用UTF-8
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(log_format))

    # 控制台handler强制UTF-8
    stream_handler = logging.StreamHandler(
        open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
    )
    stream_handler.setFormatter(logging.Formatter(log_format))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, stream_handler],
    )



# ==================== 主流程 ====================
def should_query(tracking_number, current_status):
    """
    判断是否需要查询该记录

    查询条件：
    - 单号不为空
    - 物流状态为空 或 物流状态不是终态

    终态（跳过）：
    - 以"妥投"开头的（如"妥投|用户已签收"）
    - "退回|退件签收成功"

    注意：不依赖断点进度，只看当前物流状态。
    如果用户清空了状态，下一轮就会重新查询。
    """
    if not tracking_number or not tracking_number.strip():
        return False

    # 终态不再查询
    if current_status:
        status_stripped = current_status.strip()
        # 以"妥投"开头的视为终态
        if status_stripped.startswith('妥投'):
            return False
        # "退回|退件签收成功" 视为终态
        if status_stripped == '退回|退件签收成功':
            return False

    # 物流状态为空或非终态都需要查询
    return True


def run(dry_run=False):
    """
    主运行流程

    参数:
        dry_run: 仅读取表格，不查询不写入
    
    查询策略：
    - 有单号 + 状态为空或非终态 → 查询
    - 状态为终态（妥投/退回签收） → 跳过
    - 不依赖断点进度，只看当前物流状态
    """
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("菜鸟国际物流批量查询 启动")
    logger.info(f"模式: {'干跑(不写入)' if dry_run else '正常写入'}")
    logger.info("=" * 60)

    # 1. 连接 WPS 表格
    table = WPSTable()
    tracker = None

    try:
        table.connect()

        # 2. 读取所有数据
        logger.info("正在读取表格数据...")
        all_rows = table.read_all_tracking_numbers()
        logger.info(f"读取到 {len(all_rows)} 行数据")

        if not all_rows:
            logger.warning("表格为空，退出")
            return

        # 3. 筛选需要查询的记录（仅看当前状态，不依赖断点进度）
        to_query = []
        skip_empty_tn = 0
        skip_toutou = 0
        skip_tuihui = 0
        empty_status_count = 0

        for record_id, tracking_number, current_status in all_rows:
            if not current_status or current_status.strip() == '':
                empty_status_count += 1
            if not tracking_number or not tracking_number.strip():
                skip_empty_tn += 1
                continue
            if current_status and current_status.strip().startswith('妥投'):
                skip_toutou += 1
                continue
            if current_status and current_status.strip() == '退回|退件签收成功':
                skip_tuihui += 1
                continue
            to_query.append((record_id, tracking_number, current_status))

        logger.info(f"状态分布: 空白={empty_status_count}, 妥投*={skip_toutou}, "
                    f"退回签收={skip_tuihui}, 空单号={skip_empty_tn}")
        logger.info(f"需要查询的记录: {len(to_query)} / {len(all_rows)}")

        if not to_query:
            logger.info("没有需要查询的记录，退出")
            return

        if dry_run:
            logger.info("[干跑模式] 以下记录将被查询:")
            for i, (rid, tn, st) in enumerate(to_query[:20]):
                logger.info(f"  [{i}] {tn} (当前状态: {st or '空'})")
            if len(to_query) > 20:
                logger.info(f"  ... 还有 {len(to_query) - 20} 条")
            return

        # 4. 初始化菜鸟查询器（独立浏览器实例，避免PageDisconnectedError）
        logger.info("正在初始化菜鸟物流查询器...")
        tracker = CainiaoTracker()

        # 5. 批量查询和写入
        batch_size = config.BATCH_SIZE
        total_queried = 0
        total_written = 0
        total_failed = 0

        for batch_start in range(0, len(to_query), batch_size):
            batch = to_query[batch_start:batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            total_batches = (len(to_query) + batch_size - 1) // batch_size

            logger.info(f"--- 批次 {batch_num}/{total_batches} ({len(batch)}条) ---")

            # 提取单号列表
            tracking_numbers = [tn for _, tn, _ in batch]
            record_ids = [rid for rid, _, _ in batch]

            # 批量查询（网页模拟）
            results = tracker.query_batch(tracking_numbers)
            total_queried += len(results)

            # 匹配结果与record_id，组装写入数据
            batch_results = []
            for i, result in enumerate(results):
                if result['success'] and result['latest_detail']:
                    status_full = result['status']  # 格式: "妥投|Package delivered"
                    update_time = format_time(result['latest_time'])

                    # 物流状态描述：取"|"前面的字（中文主状态）
                    if '|' in status_full:
                        status_desc = status_full.split('|')[0]
                    else:
                        status_desc = status_full

                    # 签收日期逻辑：
                    # 如果主状态是"妥投"或"退回"(退件签收成功)，签收日期=更新日期
                    sign_date = ''
                    if status_desc == '妥投' or status_desc == '退回':
                        sign_date = update_time

                    # 售后处理状态：妥投=已处理，否则=未处理
                    after_sale = '已处理' if status_desc == '妥投' else '未处理'

                    batch_results.append({
                        'record_id': record_ids[i],
                        'status': status_full,
                        'time': update_time,
                        'sign_date': sign_date,
                        'status_desc': status_desc,
                        'after_sale': after_sale,
                    })
                    logger.debug(f"  [OK] {tracking_numbers[i]}: {status_full}")
                else:
                    total_failed += 1
                    logger.debug(f"  [--] {tracking_numbers[i]}: 无数据")

            # 批量写入 WPS 表格
            if batch_results:
                logger.info(f"  写入 {len(batch_results)} 条结果到WPS表格...")
                ok = table.write_batch(batch_results)
                if ok:
                    total_written += len(batch_results)
                    logger.info(f"  写入成功 ({len(batch_results)}条)")
                else:
                    logger.error(f"  写入失败!")
            else:
                logger.info(f"  本批次无有效结果，跳过写入")

            # 检查连续失败 - 可能遇到验证码
            if tracker.consecutive_failures >= config.PAUSE_AFTER_FAILURES:
                logger.warning(
                    f"连续失败 {tracker.consecutive_failures} 次，"
                    f"可能遇到验证码，暂停 {config.PAUSE_DURATION} 秒等待手动处理..."
                )
                time.sleep(config.PAUSE_DURATION)
                tracker.consecutive_failures = 0

            # 批次间等待（随机2-3秒）
            if batch_start + batch_size < len(to_query):
                wait_time = random.uniform(config.BATCH_INTERVAL_MIN, config.BATCH_INTERVAL_MAX)
                logger.info(f"  批次间等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)

        # 6. 完成
        stats = tracker.get_stats()
        logger.info("=" * 60)
        logger.info("查询完成!")
        logger.info(f"  总查询: {total_queried}")
        logger.info(f"  成功写入: {total_written}")
        logger.info(f"  失败(无数据): {total_failed}")
        logger.info(f"  查询成功率: {stats['success']}/{stats['total']}")
        logger.info("=" * 60)

    except KeyboardInterrupt:
        logger.info("\n用户中断")
    except Exception as e:
        logger.error(f"运行异常: {e}", exc_info=True)
    finally:
        # 关闭资源
        table.close()
        if tracker:
            tracker.close()


# ==================== LaunchAgent 管理 ====================
def _get_executable_path():
    """获取当前可执行文件路径"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的可执行文件
        return sys.executable
    else:
        # 开发模式：python 解释器 + 脚本
        return os.path.abspath(__file__)


def _generate_plist_content():
    """生成 LaunchAgent plist 内容"""
    exe_path = _get_executable_path()

    if getattr(sys, 'frozen', False):
        # 打包后：直接运行可执行文件
        program_args = f"    <string>{exe_path}</string>"
    else:
        # 开发模式：python main.py
        python_path = sys.executable
        script_path = os.path.abspath(__file__)
        program_args = f"    <string>{python_path}</string>\n    <string>{script_path}</string>"

    log_path = os.path.join(config.APP_DATA_DIR, 'launchd_stdout.log')
    err_path = os.path.join(config.APP_DATA_DIR, 'launchd_stderr.log')

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCH_AGENT_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
{program_args}
    </array>

    <key>StartInterval</key>
    <integer>{LOOP_INTERVAL_HOURS * 3600}</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{log_path}</string>

    <key>StandardErrorPath</key>
    <string>{err_path}</string>

    <key>WorkingDirectory</key>
    <string>{config.APP_DATA_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
"""
    return plist


def install_launch_agent():
    """安装 macOS LaunchAgent（开机自启 + 定时运行）"""
    if platform.system() != 'Darwin':
        print("LaunchAgent 仅支持 macOS")
        return False

    # 确保目录存在
    os.makedirs(os.path.dirname(LAUNCH_AGENT_PLIST), exist_ok=True)

    # 先卸载已有的（如果存在）
    uninstall_launch_agent(quiet=True)

    # 写入 plist
    plist_content = _generate_plist_content()
    with open(LAUNCH_AGENT_PLIST, 'w', encoding='utf-8') as f:
        f.write(plist_content)

    # 加载服务
    result = subprocess.run(
        ['launchctl', 'load', LAUNCH_AGENT_PLIST],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        print("✓ 开机自启服务已安装")
        print(f"  服务标识: {LAUNCH_AGENT_LABEL}")
        print(f"  执行间隔: 每 {LOOP_INTERVAL_HOURS} 小时")
        print(f"  配置文件: {LAUNCH_AGENT_PLIST}")
        print(f"  日志目录: {config.APP_DATA_DIR}")
        print("")
        print("  服务将在开机时自动启动，并每2小时执行一次查询。")
        return True
    else:
        print(f"✗ 安装失败: {result.stderr}")
        return False


def uninstall_launch_agent(quiet=False):
    """卸载 macOS LaunchAgent"""
    if platform.system() != 'Darwin':
        if not quiet:
            print("LaunchAgent 仅支持 macOS")
        return False

    if os.path.exists(LAUNCH_AGENT_PLIST):
        subprocess.run(
            ['launchctl', 'unload', LAUNCH_AGENT_PLIST],
            capture_output=True, text=True
        )
        os.remove(LAUNCH_AGENT_PLIST)
        if not quiet:
            print("✓ 开机自启服务已卸载")
        return True
    else:
        if not quiet:
            print("未找到已安装的服务")
        return False


# ==================== 定时循环 ====================
def run_loop(dry_run=False):
    """
    定时循环模式：每2小时执行一次查询
    程序启动后立即查询一次，然后每隔2小时重复
    """
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("菜鸟国际物流批量查询 - 定时循环模式")
    logger.info(f"查询间隔: 每 {LOOP_INTERVAL_HOURS} 小时")
    logger.info("=" * 60)

    round_num = 0
    while True:
        round_num += 1
        logger.info(f"\n{'='*40} 第 {round_num} 轮查询 {'='*40}")
        logger.info(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            run(dry_run=dry_run)
        except Exception as e:
            logger.error(f"本轮查询异常: {e}", exc_info=True)

        next_time = datetime.now().strftime('%H:%M:%S')
        logger.info(f"本轮完成，下次查询将在 {LOOP_INTERVAL_HOURS} 小时后")
        logger.info(f"等待中... (Ctrl+C 退出)")

        try:
            time.sleep(LOOP_INTERVAL_HOURS * 3600)
        except KeyboardInterrupt:
            logger.info("\n用户中断，退出定时循环")
            break


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='菜鸟国际物流批量查询')
    parser.add_argument('--once', action='store_true', help='只查一次就退出（不进入定时循环）')
    parser.add_argument('--dry-run', action='store_true', help='仅读取表格，不查询不写入')
    parser.add_argument('--install', action='store_true', help='安装macOS开机自启服务')
    parser.add_argument('--uninstall', action='store_true', help='卸载macOS开机自启服务')
    args = parser.parse_args()

    setup_logging()

    if args.install:
        install_launch_agent()
        return

    if args.uninstall:
        uninstall_launch_agent()
        return

    if args.once or args.dry_run:
        # 单次模式
        run(dry_run=args.dry_run)
    else:
        # 默认：定时循环模式
        run_loop(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
