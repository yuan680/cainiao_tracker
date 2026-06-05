"""
菜鸟国际物流批量查询 - 配置文件

方案：DrissionPage全自动
- 自动打开WPS网页版多维表格读取物流单号
- 自动查询菜鸟国际物流
- 自动将结果写回WPS多维表格

一键运行，无需切换平台
"""

import os
import sys
import platform

# ==================== 路径适配 ====================
def _get_app_data_dir():
    """获取应用数据目录（跨平台）"""
    if platform.system() == 'Darwin':
        # macOS: ~/Library/Application Support/CainiaoTracker/
        home = os.path.expanduser('~')
        return os.path.join(home, 'Library', 'Application Support', 'CainiaoTracker')
    else:
        # Windows / Linux: 使用程序所在目录
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后
            return os.path.dirname(sys.executable)
        else:
            return os.path.dirname(os.path.abspath(__file__))

APP_DATA_DIR = _get_app_data_dir()
os.makedirs(APP_DATA_DIR, exist_ok=True)

# ==================== WPS 多维表格配置 ====================
# 你的多维表格分享URL
WPS_TABLE_URL = "https://www.kdocs.cn/l/chCxXjbgCm86"

# 列配置（与表格实际列名对应，字段ID在wps_table.py中维护）
COLUMN_TRACKING = "物流单号"        # col 0, 字段ID: B
COLUMN_STATUS = "物流状态"          # col 7, 字段ID: e - 格式如"妥投|用户已签收"
COLUMN_TIME = "更新时间"            # col 8, 字段ID: f
COLUMN_SIGN_DATE = "签收日期"       # col 11, 字段ID: -
COLUMN_STATUS_DESC = "物流状态描述"  # col 14, 字段ID: BM - 取"|"前面
COLUMN_AFTER_SALE = "售后处理状态"   # col 15, 字段ID: BN

# ==================== 菜鸟国际物流配置 ====================
CAINIAO_API_URL = "https://global.cainiao.com/global/detail.json"

# ==================== 查询策略配置 ====================
# 每次查询间隔（秒）- 随机范围
QUERY_INTERVAL_MIN = 0.2
QUERY_INTERVAL_MAX = 0.8

# 每批查询数量（菜鸟网页最多支持100个单号一次查询）
BATCH_SIZE = 100

# 批次间等待时间（秒）- 随机范围
BATCH_INTERVAL_MIN = 2
BATCH_INTERVAL_MAX = 3

# 连续失败多少次后暂停
PAUSE_AFTER_FAILURES = 10

# 暂停时间（秒）
PAUSE_DURATION = 60

# ==================== 浏览器配置 ====================
# 首次运行设为False（需要扫码登录）
# 登录成功后改为True（无窗口运行）
HEADLESS = False

# 浏览器路径（留空自动检测）
# macOS 上 DrissionPage 会自动查找 /Applications/Google Chrome.app
BROWSER_PATH = ""

# 用户数据目录（保存登录Cookie，避免重复登录）
USER_DATA_DIR = os.path.join(APP_DATA_DIR, 'browser_data')

# ==================== 日志配置 ====================
LOG_FILE = "cainiao_tracker.log"
