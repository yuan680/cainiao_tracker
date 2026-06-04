"""
菜鸟国际物流批量查询 - 主程序

完整自动化流程：
1. 连接 WPS 多维表格（DrissionPage + JSSDK）
2. 读取所有物流单号
3. 通过 DrissionPage 模拟网页批量查询菜鸟国际物流
4. 批量写回查询结果

使用方式：
    python main.py              # 仅查询状态为空或异常的记录
    python main.py --all        # 强制查询所有记录
    python main.py --dry-run    # 仅读取，不查询不写入
    python main.py --reset      # 重置进度，开始新一轮
"""

import os
import sys
import time
import json
import random
import logging
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from wps_table import WPSTable
from cainiao_query import CainiaoTracker


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


# ==================== 进度管理 ====================
class ProgressTracker:
    """断点续查 - 记录已完成的查询进度"""

    def __init__(self):
        self.progress_file = os.path.join(
            config.APP_DATA_DIR,
            config.PROGRESS_FILE
        )
        self.data = self._load()

    def _load(self):
        """加载进度文件"""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {
            'last_run': None,
            'completed_records': [],
            'total_queried': 0,
            'total_written': 0,
        }

    def save(self):
        """保存进度"""
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_completed(self, record_id):
        """检查记录是否已在本轮完成"""
        return record_id in self.data['completed_records']

    def mark_completed(self, record_ids):
        """批量标记记录完成"""
        for rid in record_ids:
            if rid not in self.data['completed_records']:
                self.data['completed_records'].append(rid)

    def reset(self):
        """重置进度（新一轮查询）"""
        self.data = {
            'last_run': datetime.now().isoformat(),
            'completed_records': [],
            'total_queried': 0,
            'total_written': 0,
        }
        self.save()


# ==================== 主流程 ====================
def should_query(tracking_number, current_status, force_all=False):
    """
    判断是否需要查询该记录

    查询条件：
    - 单号不为空
    - 物流状态为空 或 物流状态不是终态

    终态（跳过）：
    - 以"妥投"开头的（如"妥投|用户已签收"）
    - "退回|退件签收成功"
    """
    if not tracking_number or not tracking_number.strip():
        return False

    if force_all:
        return True

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


def run(force_all=False, dry_run=False):
    """
    主运行流程

    参数:
        force_all: 是否强制查询所有记录（包括已有状态的）
        dry_run: 仅读取表格，不查询不写入
    """
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("菜鸟国际物流批量查询 启动")
    logger.info(f"模式: {'强制全量' if force_all else '增量更新'} | {'干跑(不写入)' if dry_run else '正常写入'}")
    logger.info("=" * 60)

    # 1. 连接 WPS 表格
    table = WPSTable()
    tracker = None
    progress = ProgressTracker()

    try:
        table.connect()

        # 2. 读取所有数据
        logger.info("正在读取表格数据...")
        all_rows = table.read_all_tracking_numbers()
        logger.info(f"读取到 {len(all_rows)} 行数据")

        if not all_rows:
            logger.warning("表格为空，退出")
            return

        # 3. 筛选需要查询的记录
        to_query = []
        skip_empty_tn = 0
        skip_toutou = 0
        skip_tuihui = 0
        skip_completed = 0
        empty_status_count = 0

        for record_id, tracking_number, current_status in all_rows:
            if not current_status or current_status.strip() == '':
                empty_status_count += 1
            if progress.is_completed(record_id):
                skip_completed += 1
                continue
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
                    f"退回签收={skip_tuihui}, 空单号={skip_empty_tn}, "
                    f"已完成={skip_completed}")
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

            # 标记已查询
            progress.mark_completed(record_ids)

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

            # 保存进度
            progress.data['total_queried'] = total_queried
            progress.data['total_written'] = total_written
            progress.save()

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
        logger.info("\n用户中断，保存进度...")
        progress.save()
    except Exception as e:
        logger.error(f"运行异常: {e}", exc_info=True)
        progress.save()
    finally:
        # 关闭资源
        table.close()
        if tracker:
            tracker.close()


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='菜鸟国际物流批量查询')
    parser.add_argument('--all', action='store_true', help='强制查询所有记录（包括已有状态的）')
    parser.add_argument('--dry-run', action='store_true', help='仅读取表格，不查询不写入')
    parser.add_argument('--reset', action='store_true', help='重置进度记录（开始新一轮）')
    args = parser.parse_args()

    setup_logging()

    if args.reset:
        progress = ProgressTracker()
        progress.reset()
        logging.info("进度已重置")

    run(force_all=args.all, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
