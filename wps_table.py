"""
WPS 多维表格读写模块

通过 DrissionPage 操控浏览器，使用 JSSDK 的 RecordRange 实现数据读写。

读取原理（逆向工程确认）：
1. 通过 getDbSheetRecords().getIdArray() 获取全部 recordId（不受 gridCells 缓存限制）
2. 通过 RecordRange.Value getter 分批异步读取数据
3. 能读取全部记录（解决了 getCellString 只能读缓存中 ~384 行的问题）

写入原理（逆向工程确认）：
1. 通过 WPSOpenApi.DBApplication().ActiveSheet 获取 sheet proxy
2. 从 sheet.__proxy_ref__ 的 RecordRange getter 获取 RecordRange 实例
3. 设置 rr._records = [recordId1, ...]  和  rr._fields = [fieldId1, ...]
4. 调用 rr._setValues(values_2d_array, false, false)  → Promise
5. 数据通过 WebSocket 发送到服务器并持久化
"""

import os
import sys
import time
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from DrissionPage import ChromiumPage, ChromiumOptions
import config

logger = logging.getLogger(__name__)


class WPSTable:
    """WPS多维表格操作类"""

    # 写入用字段ID（通过逆向工程 + GetFields API 确认）
    # 这些ID是固定的，不随列顺序变化
    WRITE_FIELD_IDS = {
        '物流状态': 'e',
        '更新时间': 'f',
        '签收日期': '-',
        '物流状态描述': 'BM',
        '售后处理状态': 'BN',
    }

    def __init__(self):
        self.page = None
        self._ready = False
        # 动态解析的列索引（通过 GetFields 获取）
        self._col_indices = {}

    def connect(self):
        """连接浏览器并打开WPS表格"""
        logger.info("正在启动浏览器...")
        opts = ChromiumOptions()
        data_dir = os.path.abspath(config.USER_DATA_DIR)
        os.makedirs(data_dir, exist_ok=True)
        opts.set_user_data_path(data_dir)

        if config.BROWSER_PATH:
            opts.set_browser_path(config.BROWSER_PATH)

        self.page = ChromiumPage(opts)
        self.page.get(config.WPS_TABLE_URL)
        logger.info("等待页面加载...")
        time.sleep(8)
        # 强制刷新确保获取服务器最新数据（不使用缓存）
        logger.info("强制刷新页面获取最新数据...")
        self.page.refresh(ignore_cache=True)
        time.sleep(12)

        # 验证页面加载成功
        if not self._check_ready():
            raise RuntimeError("WPS表格加载失败，请检查网络或登录状态")

        # 动态解析字段列索引
        self._resolve_field_indices()

        self._ready = True
        logger.info("WPS表格连接成功")

    def _check_ready(self):
        """检查页面是否加载完成"""
        r = self.page.run_js("""
            try {
                var sd = window.APP.workbook._worksheets._sheets[0].sheetData;
                var val = sd.getCellString(0, 0);
                return val ? 'ok' : 'empty';
            } catch(e) {
                return 'error: ' + e.message;
            }
        """)
        return r == 'ok' or r == 'empty'

    def _resolve_field_indices(self):
        """
        通过 GetFields() API 动态解析字段名称对应的列索引
        GetFields() 返回字段数组，数组下标即为 getCellString 的列索引
        避免硬编码列号，适应列顺序变化
        """
        r = self.page.run_js("""
            return new Promise(function(resolve) {
                try {
                    var dbApp = window.WPSOpenApi.DBApplication();
                    dbApp.ActiveSheet.then(function(sheet) {
                        sheet.GetFields().then(function(fields) {
                            var result = [];
                            for (var i = 0; i < fields.length; i++) {
                                result.push({
                                    col: i,
                                    id: fields[i].id,
                                    name: fields[i].name
                                });
                            }
                            resolve(JSON.stringify(result));
                        }).catch(function(e) {
                            resolve(JSON.stringify({error: 'GetFields: ' + e.message}));
                        });
                    }).catch(function(e) {
                        resolve(JSON.stringify({error: 'ActiveSheet: ' + e.message}));
                    });
                } catch(e) {
                    resolve(JSON.stringify({error: 'exception: ' + e.message}));
                }
            });
        """, timeout=15)

        try:
            data = json.loads(r)
            if isinstance(data, dict) and 'error' in data:
                raise RuntimeError(f"解析字段索引失败: {data['error']}")

            # 保存所有字段的索引映射（字段名 -> 列号）
            self._col_indices = {}
            field_id_map = {}
            for item in data:
                self._col_indices[item['name']] = item['col']
                field_id_map[item['name']] = item['id']

            # 验证必须的字段存在
            required = ['物流单号', '物流状态']
            for field_name in required:
                if field_name not in self._col_indices:
                    raise RuntimeError(f"表格中未找到必需字段: {field_name}")

            # 同时更新写入字段ID（以防字段ID变化）
            for name, fid in field_id_map.items():
                if name in self.WRITE_FIELD_IDS:
                    if self.WRITE_FIELD_IDS[name] != fid:
                        logger.warning(
                            f"字段 '{name}' ID 已变化: "
                            f"{self.WRITE_FIELD_IDS[name]} -> {fid}"
                        )
                        self.WRITE_FIELD_IDS[name] = fid

            logger.info(
                f"字段索引解析成功 (共{len(data)}列): "
                f"物流单号=col{self._col_indices['物流单号']}, "
                f"物流状态=col{self._col_indices['物流状态']}"
            )

        except (json.JSONDecodeError, TypeError) as e:
            raise RuntimeError(f"解析字段索引JSON失败: {e}, 原始数据: {r}")

    def get_total_rows(self):
        """获取表格总行数 - 通过遍历Idx2Id计数"""
        r = self.page.run_js("""
            try {
                var sd = window.APP.workbook._worksheets._sheets[0].sheetData;
                var dbRec = sd.getDbSheetRecords();
                var count = 0;
                for (var i = 0; i < 10000; i++) {
                    var id = dbRec.Idx2Id(i);
                    if (!id || id === '' || id === undefined) break;
                    count++;
                }
                return JSON.stringify({count: count});
            } catch(e) {
                return JSON.stringify({error: e.message});
            }
        """)
        try:
            data = json.loads(r)
            if 'error' in data:
                logger.error(f"获取行数失败: {data['error']}")
                return 0
            return data.get('count', 0)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"获取行数解析失败: {r}")
            return 0

    def read_all_tracking_numbers(self):
        """
        读取所有物流单号及其recordId
        使用 RecordRange.Value 通过字段ID直接读取，不受 gridCells 缓存限制
        返回: [(recordId, tracking_number, current_status), ...]
        """
        if not self._ready:
            raise RuntimeError("未连接WPS表格")

        # 获取物流单号和物流状态的字段ID
        field_tracking = self.WRITE_FIELD_IDS.get('物流单号') or 'B'
        field_status = self.WRITE_FIELD_IDS.get('物流状态') or 'e'

        # 1. 获取全部 recordId
        r = self.page.run_js("""
            try {
                var sd = window.APP.workbook._worksheets._sheets[0].sheetData;
                var dbRec = sd.getDbSheetRecords();
                var ids = dbRec.getIdArray();
                return JSON.stringify(ids);
            } catch(e) {
                return JSON.stringify({error: e.message});
            }
        """, timeout=10)

        try:
            all_ids = json.loads(r)
            if isinstance(all_ids, dict) and 'error' in all_ids:
                raise RuntimeError(f"获取recordId失败: {all_ids['error']}")
        except (json.JSONDecodeError, TypeError) as e:
            raise RuntimeError(f"解析recordId失败: {e}")

        total = len(all_ids)
        if total == 0:
            logger.warning("表格为空")
            return []

        logger.info(f"表格共 {total} 行数据，通过 RecordRange.Value 读取字段 {field_tracking}(物流单号) 和 {field_status}(物流状态)")

        # 2. 分批通过 RecordRange.Value 读取数据
        all_rows = []
        batch_size = 200  # RecordRange.Value 支持更大批次

        for start in range(0, total, batch_size):
            batch_ids = all_ids[start:start + batch_size]
            batch_ids_json = json.dumps(batch_ids)

            r = self.page.run_js(f"""
                return new Promise(function(resolve) {{
                    try {{
                        var dbApp = window.WPSOpenApi.DBApplication();
                        dbApp.ActiveSheet.then(function(sheet) {{
                            var sRef = sheet.__proxy_ref__;
                            var rrGetter = Object.getOwnPropertyDescriptor(
                                Object.getPrototypeOf(sRef), 'RecordRange'
                            );
                            var rr = rrGetter.get.call(sRef);
                            rr._records = {batch_ids_json};
                            rr._fields = ['{field_tracking}', '{field_status}'];

                            var valueDesc = Object.getOwnPropertyDescriptor(
                                Object.getPrototypeOf(rr), 'Value'
                            );
                            var p = valueDesc.get.call(rr);
                            if (p && p.then) {{
                                p.then(function(vals) {{
                                    resolve(JSON.stringify(vals));
                                }}).catch(function(e) {{
                                    resolve(JSON.stringify({{error: e.message}}));
                                }});
                            }} else {{
                                resolve(JSON.stringify(p));
                            }}
                        }}).catch(function(e) {{
                            resolve(JSON.stringify({{error: 'sheet: ' + e.message}}));
                        }});
                    }} catch(e) {{
                        resolve(JSON.stringify({{error: 'exception: ' + e.message}}));
                    }}
                }});
            """, timeout=30)

            try:
                data = json.loads(r)
                if isinstance(data, dict) and 'error' in data:
                    logger.error(f"读取行 {start}-{start+len(batch_ids)} 失败: {data['error']}")
                    continue

                # data 是二维数组 [[tracking, status], ...]
                for i, row_vals in enumerate(data):
                    record_id = batch_ids[i]
                    tracking = row_vals[0] if len(row_vals) > 0 else ''
                    status = row_vals[1] if len(row_vals) > 1 else ''
                    all_rows.append((record_id, tracking or '', status or ''))

            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"解析行 {start}-{start+len(batch_ids)} 数据失败: {e}")
                continue

        logger.info(f"成功读取 {len(all_rows)} 行数据")
        return all_rows

    def write_batch(self, updates):
        """
        批量写入物流数据

        参数:
            updates: list of dict, 每个包含:
                - record_id: str, 记录ID
                - logistics: str, 最新物流信息
                - time: str, 物流时间
                - status: str, 物流状态

        返回: bool, 是否全部成功
        """
        if not self._ready:
            raise RuntimeError("未连接WPS表格")

        if not updates:
            return True

        # 分批写入，每批最多50行（避免单次数据量过大）
        batch_size = 50
        success = True

        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            if not self._write_chunk(batch):
                success = False
                logger.error(f"写入批次 {i//batch_size + 1} 失败")

            # 批次间短暂等待
            if i + batch_size < len(updates):
                time.sleep(1)

        return success

    def _write_chunk(self, updates):
        """
        写入一个chunk的数据

        使用 RecordRange._setValues 批量写入
        写入5个字段：物流状态、更新时间、签收日期、物流状态描述、售后处理状态
        字段ID通过 WRITE_FIELD_IDS 映射获取（连接时已通过 GetFields 验证）
        """
        record_ids = [u['record_id'] for u in updates]
        # 按固定顺序获取写入字段ID
        field_ids = [
            self.WRITE_FIELD_IDS['物流状态'],
            self.WRITE_FIELD_IDS['更新时间'],
            self.WRITE_FIELD_IDS['签收日期'],
            self.WRITE_FIELD_IDS['物流状态描述'],
            self.WRITE_FIELD_IDS['售后处理状态'],
        ]

        # 构建二维数组 [[row0_f0, row0_f1, ...], [row1_f0, ...], ...]
        values = []
        for u in updates:
            values.append([
                u.get('status', ''),
                u.get('time', ''),
                u.get('sign_date', ''),
                u.get('status_desc', ''),
                u.get('after_sale', ''),
            ])

        # 序列化参数
        record_ids_json = json.dumps(record_ids)
        field_ids_json = json.dumps(field_ids)
        values_json = json.dumps(values, ensure_ascii=False)

        js_code = f"""
            return new Promise(function(resolve) {{
                try {{
                    var dbApp = window.WPSOpenApi.DBApplication();
                    dbApp.ActiveSheet.then(function(sheet) {{
                        var sRef = sheet.__proxy_ref__;
                        var rrGetter = Object.getOwnPropertyDescriptor(
                            Object.getPrototypeOf(sRef), 'RecordRange'
                        );
                        var rr = rrGetter.get.call(sRef);

                        rr._records = {record_ids_json};
                        rr._fields = {field_ids_json};

                        var p = rr._setValues({values_json}, false, false);
                        if (p && p.then) {{
                            p.then(function() {{
                                resolve('ok');
                            }}).catch(function(e) {{
                                resolve('error:' + (e.message || String(e)));
                            }});
                        }} else {{
                            resolve('ok_sync');
                        }}
                    }}).catch(function(e) {{
                        resolve('sheet_error:' + e.message);
                    }});
                }} catch(e) {{
                    resolve('exception:' + e.message);
                }}
            }});
        """

        try:
            result = self.page.run_js(js_code, timeout=30)
            if result and result.startswith('ok'):
                logger.debug(f"写入 {len(updates)} 行成功")
                return True
            else:
                logger.error(f"写入失败: {result}")
                return False
        except Exception as e:
            logger.error(f"JS执行异常: {e}")
            return False

    def write_single(self, record_id, logistics='', time_str='', status=''):
        """
        写入单行数据

        参数:
            record_id: str, 记录ID
            logistics: str, 最新物流
            time_str: str, 物流时间
            status: str, 物流状态
        """
        return self.write_batch([{
            'record_id': record_id,
            'logistics': logistics,
            'time': time_str,
            'status': status,
        }])

    def close(self):
        """关闭浏览器"""
        if self.page:
            try:
                self.page.quit()
            except Exception:
                pass
            self.page = None
            self._ready = False
            logger.info("浏览器已关闭")


def test():
    """快速测试读写功能"""
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')

    table = WPSTable()
    try:
        table.connect()

        # 测试读取
        rows = table.read_all_tracking_numbers()
        print(f"\n=== 读取到 {len(rows)} 行 ===")
        for i, (rid, tn, st) in enumerate(rows[:5]):
            print(f"  [{i}] recordId={rid}, 单号={tn}, 状态={st}")

        # 测试写入第一行
        if rows:
            rid = rows[0][0]
            print(f"\n=== 测试写入 recordId={rid} ===")
            ok = table.write_single(
                rid,
                logistics='[测试] 包裹已到达转运中心',
                time_str='2024-01-01 12:00:00',
                status='运输中'
            )
            print(f"  写入结果: {'成功' if ok else '失败'}")

            # 验证
            time.sleep(2)
            r = table.page.run_js(f"""
                try {{
                    var sd = window.APP.workbook._worksheets._sheets[0].sheetData;
                    return JSON.stringify({{
                        col3: sd.getCellString(0, 3),
                        col6: sd.getCellString(0, 6),
                        col7: sd.getCellString(0, 7)
                    }});
                }} catch(e) {{
                    return 'ERROR: ' + e.message;
                }}
            """)
            print(f"  验证: {r}")

            # 清理
            print("\n=== 清理测试数据 ===")
            table.write_single(rid, logistics='', time_str='', status='')
            print("  已清理")

    finally:
        table.close()


if __name__ == "__main__":
    test()
