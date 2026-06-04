# 菜鸟国际物流批量查询工具

全自动方案：通过 DrissionPage 打开 WPS 多维表格网页版，读取物流单号，查询菜鸟国际物流 API，然后直接写回表格。无需中间服务器、无需 AirScript。

## 架构

```
WPS 多维表格（网页版）
    ↕ DrissionPage 浏览器自动化
Python 主程序
    ├── 读取物流单号（JSSDK getCellString）
    ├── 查询菜鸟物流（菜鸟 global API）
    └── 写回查询结果（JSSDK RecordRange._setValues）
```

## 核心文件

| 文件 | 说明 |
|------|------|
| `main.py` | 主程序入口（一键运行） |
| `config.py` | 配置文件（URL、批次大小、延时等） |
| `cainiao_query.py` | 菜鸟物流查询模块（DrissionPage 访问 API） |
| `wps_table.py` | WPS 多维表格读写模块（逆向 JSSDK） |
| `REVERSE_ENGINEERING.md` | WPS 多维表格逆向工程详细记录 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

依赖：DrissionPage（浏览器自动化库）

### 2. 首次登录

首次运行需要扫码登录 WPS：

```bash
python main.py --dry-run
```

浏览器会打开 WPS 网页版，扫码登录后 Cookie 自动保存到 `browser_data/` 目录。
后续运行无需再次登录。

### 3. 运行查询

```bash
# 增量更新（跳过已签收的，查询无状态或异常的记录）
python main.py

# 强制全量查询（包括已有状态的记录，仅跳过已签收）
python main.py --all

# 干跑模式（仅读取表格，显示待查数量，不查询不写入）
python main.py --dry-run

# 重置进度（清除断点续查记录，开始新一轮）
python main.py --reset
```

## 配置说明

编辑 `config.py`：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WPS_TABLE_URL` | kdocs链接 | WPS 多维表格分享URL |
| `BATCH_SIZE` | 20 | 每批查询数量（查完一批写入一次） |
| `QUERY_INTERVAL_MIN/MAX` | 3-6秒 | 查询间随机延时（避免限流） |
| `BATCH_INTERVAL` | 5秒 | 批次间等待时间 |
| `HEADLESS` | False | 是否无头模式（首次需 False 扫码） |
| `PAUSE_AFTER_FAILURES` | 10 | 连续失败N次后暂停 |

## 工作原理

1. **读取**：通过 `sheetData.getCellString(row, col)` 读取多维表格数据
2. **查询**：DrissionPage 直接访问 `global.cainiao.com/global/detail.json` API
3. **写入**：通过逆向发现的 `RecordRange._setValues()` 方法批量写入
4. **断点续查**：进度保存在 `query_progress.json`，中断后可继续

## 字段映射

| WPS 字段 | 字段ID | 用途 |
|----------|--------|------|
| 物流单号 | B | 读取 |
| 最新物流 | I | 写入物流详情 |
| 物流时间 | L | 写入最新时间 |
| 物流状态 | e | 写入状态标记 |

## 注意事项

- 首次运行必须 `HEADLESS = False`，手动扫码登录一次
- WPS 登录 Cookie 有效期约 30 天，过期需重新扫码
- 菜鸟 API 有限流，建议 `QUERY_INTERVAL` 不低于 3 秒
- 表格需保持分享状态（当前用户有写权限）
- 1600+ 条记录全量查询约需 2-3 小时（含延时）
