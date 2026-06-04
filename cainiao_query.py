"""
菜鸟国际物流查询模块 (网页模拟方案 v6 - listen API拦截版)

核心方案：
1. 独立浏览器实例（auto_port + 专用数据目录）
2. page.get() 导航到菜鸟首页 + 自动处理cookie弹窗（CDP协议，不需窗口焦点）
3. CodeMirror.setValue() 输入单号
4. DrissionPage.listen 监听 detail.json 网络响应（CDP协议层，不受页面刷新影响）
5. 点击 Track 按钮 → 页面跳转到结果页 → listen 捕获 API 数据
6. 解析 API 响应中的 module[] 数组获取所有包裹物流

已验证（2026-06-03）：
- page.listen('global/detail.json') 可成功捕获响应 ✅
- API响应结构: { module: [{mailNo, status, statusDesc, latestTrace, detailList}], success: true }
- latestTrace: { timeStr: "2026-05-16 19:43:30", desc: "Package delivered", actionCode: "GTMS_SIGNED" }
- detailList: 完整物流节点数组（最新在前）
"""

import time
import json
import random
import logging
import os
from typing import List, Optional

from DrissionPage import ChromiumPage, ChromiumOptions

from config import (
    QUERY_INTERVAL_MIN,
    QUERY_INTERVAL_MAX,
    BROWSER_PATH,
    APP_DATA_DIR,
)

MAX_RETRIES = 3
CAINIAO_HOME_URL = "https://global.cainiao.com/?locale=zh-CN&lang=zh-CN"
BATCH_QUERY_SIZE = 100

# 菜鸟查询专用浏览器数据目录（使用统一的应用数据目录）
CAINIAO_BROWSER_DATA = os.path.join(APP_DATA_DIR, 'cainiao_browser_data')

logger = logging.getLogger(__name__)


# ============ 结果页全局数据提取 JS ============
EXTRACT_GLOBAL_DATA_JS = """
return (function() {
    var result = {found: false, source: '', packages: []};
    
    // 方法1: __INITIAL_DATA__ (常见SSR框架)
    if (window.__INITIAL_DATA__) {
        result.source = '__INITIAL_DATA__';
        result.raw = JSON.stringify(window.__INITIAL_DATA__).substring(0, 50000);
        result.found = true;
        return JSON.stringify(result);
    }
    
    // 方法2: __NEXT_DATA__ (Next.js)
    if (window.__NEXT_DATA__) {
        result.source = '__NEXT_DATA__';
        result.raw = JSON.stringify(window.__NEXT_DATA__).substring(0, 50000);
        result.found = true;
        return JSON.stringify(result);
    }
    
    // 方法3: 遍历 window 上所有可能的数据对象
    var candidates = ['__data__', '__APP_DATA__', 'g_config', 'pageData', 
                      '__pageData', 'GLOBAL_DATA', 'initData', '__initData__'];
    for (var i = 0; i < candidates.length; i++) {
        if (window[candidates[i]]) {
            result.source = candidates[i];
            result.raw = JSON.stringify(window[candidates[i]]).substring(0, 50000);
            result.found = true;
            return JSON.stringify(result);
        }
    }
    
    // 方法4: 查找含有 mailNo 的 script[type="application/json"] 标签
    var scripts = document.querySelectorAll('script[type="application/json"]');
    for (var i = 0; i < scripts.length; i++) {
        var text = scripts[i].textContent;
        if (text && text.indexOf('mailNo') > -1) {
            result.source = 'script_json_tag';
            result.raw = text.substring(0, 50000);
            result.found = true;
            return JSON.stringify(result);
        }
    }
    
    // 方法5: 查找含 mailNo 的内联 script
    var allScripts = document.querySelectorAll('script:not([src])');
    for (var i = 0; i < allScripts.length; i++) {
        var text = allScripts[i].textContent;
        if (text && text.indexOf('mailNo') > -1 && text.indexOf('{') > -1) {
            // 尝试提取JSON
            var match = text.match(/\\{[^{}]*mailNo[^{}]*\\}/);
            if (match) {
                result.source = 'inline_script';
                result.raw = text.substring(0, 50000);
                result.found = true;
                return JSON.stringify(result);
            }
        }
    }
    
    // 方法6: React Fiber Tree - 从DOM节点获取React内部数据
    var rootEl = document.getElementById('root') || document.getElementById('app') || document.getElementById('__next');
    if (rootEl) {
        var fiberKey = Object.keys(rootEl).find(function(k) { 
            return k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'); 
        });
        if (fiberKey) {
            try {
                var fiber = rootEl[fiberKey];
                // 遍历 fiber tree 寻找含有物流数据的 state/props
                var queue = [fiber];
                var visited = 0;
                while (queue.length > 0 && visited < 200) {
                    var node = queue.shift();
                    visited++;
                    if (!node) continue;
                    
                    // 检查 memoizedState 和 memoizedProps
                    var stateStr = '';
                    try { stateStr = JSON.stringify(node.memoizedState); } catch(e) {}
                    if (stateStr && stateStr.indexOf('mailNo') > -1 && stateStr.length > 100) {
                        result.source = 'react_fiber_state';
                        result.raw = stateStr.substring(0, 50000);
                        result.found = true;
                        return JSON.stringify(result);
                    }
                    
                    var propsStr = '';
                    try { propsStr = JSON.stringify(node.memoizedProps); } catch(e) {}
                    if (propsStr && propsStr.indexOf('mailNo') > -1 && propsStr.length > 100) {
                        result.source = 'react_fiber_props';
                        result.raw = propsStr.substring(0, 50000);
                        result.found = true;
                        return JSON.stringify(result);
                    }
                    
                    if (node.child) queue.push(node.child);
                    if (node.sibling) queue.push(node.sibling);
                }
            } catch(e) {
                result.error = 'fiber_error: ' + e.message;
            }
        }
    }
    
    // 方法7: 搜索所有 window 属性中含 mailNo 的大对象
    var keys = Object.keys(window);
    for (var i = 0; i < keys.length; i++) {
        try {
            var val = window[keys[i]];
            if (val && typeof val === 'object' && val !== window && val !== document) {
                var s = JSON.stringify(val);
                if (s && s.indexOf('mailNo') > -1 && s.length > 200) {
                    result.source = 'window.' + keys[i];
                    result.raw = s.substring(0, 50000);
                    result.found = true;
                    return JSON.stringify(result);
                }
            }
        } catch(e) {}
    }
    
    result.source = 'none_found';
    result.found = false;
    return JSON.stringify(result);
})();
"""


class CainiaoTracker:
    """菜鸟国际物流查询器 (page.get()导航 + listen API拦截方案)"""

    def __init__(self, page=None):
        """初始化查询器，始终使用独立浏览器"""
        self.page = None
        self.success_count = 0
        self.fail_count = 0
        self.consecutive_failures = 0
        self._on_homepage = False  # 是否已在首页准备就绪
        self._cookie_accepted = False  # Cookie弹窗是否已处理

        self._init_browser()

    def _init_browser(self):
        """初始化独立浏览器"""
        co = ChromiumOptions()
        os.makedirs(CAINIAO_BROWSER_DATA, exist_ok=True)
        co.set_user_data_path(CAINIAO_BROWSER_DATA)
        co.auto_port()

        if BROWSER_PATH:
            co.set_browser_path(BROWSER_PATH)

        try:
            self.page = ChromiumPage(co)
            self.page.set.window.max()
            time.sleep(0.5)
            logger.info("查询浏览器初始化成功")
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            raise RuntimeError(f"无法启动浏览器: {e}")

    def _navigate_to_homepage(self):
        """
        使用 page.get() 导航到菜鸟首页（CDP协议，不需要窗口焦点）。
        自动处理 Cookie 弹窗（无需手动干预）。
        """
        logger.info("导航到菜鸟首页...")

        try:
            self.page.get(CAINIAO_HOME_URL)
        except Exception as e:
            logger.warning(f"page.get() 异常: {e}")
            # 可能是超时但页面已部分加载
            time.sleep(1)

        # 等待页面加载
        time.sleep(0.5)
        logger.info(f"当前URL: {self.page.url}")

        # 检查是否导航成功
        if 'cainiao' not in self.page.url:
            logger.warning("导航可能失败，当前URL不含cainiao")
            return False

        # 自动处理Cookie弹窗（仅首次需要，后续跳过）
        if not self._cookie_accepted:
            self._auto_accept_cookie()

        return True

    def _auto_accept_cookie(self):
        """
        自动点击 Cookie 接受按钮。
        菜鸟网站的 cookie 弹窗通常有 "ACCEPT ALL" 按钮。
        通过 CDP 操作，完全不需要窗口在前台。
        """
        try:
            # 尝试多种选择器匹配 cookie 接受按钮
            selectors = [
                '@@tag()=button@@text():ACCEPT ALL',
                '@@tag()=button@@text():Accept All',
                '@@tag()=button@@text():accept all',
                '@@tag()=button@@text():Accept',
                '@@tag()=button@@text():同意',
                '@@tag()=button@@text():全部接受',
                'css:.cookie-accept-btn',
                'css:[data-testid="cookie-accept"]',
            ]

            for selector in selectors:
                try:
                    btn = self.page.ele(selector, timeout=0.5)
                    if btn:
                        btn.click()
                        logger.info(f"已自动接受Cookie (按钮: {btn.text})")
                        self._cookie_accepted = True
                        time.sleep(random.uniform(0.3, 0.8))
                        return True
                except Exception:
                    continue

            # 备用：用 JS 查找并点击含 "accept" 的按钮
            clicked = self.page.run_js("""
                return (function() {
                    var buttons = document.querySelectorAll('button');
                    for (var i = 0; i < buttons.length; i++) {
                        var text = buttons[i].textContent.toLowerCase().trim();
                        if (text.indexOf('accept') > -1 || text.indexOf('同意') > -1) {
                            buttons[i].click();
                            return 'clicked:' + buttons[i].textContent.trim();
                        }
                    }
                    return 'no_cookie_btn';
                })();
            """)

            if clicked and clicked.startswith('clicked:'):
                logger.info(f"JS方式接受Cookie: {clicked}")
                self._cookie_accepted = True
                time.sleep(random.uniform(0.3, 0.8))
                return True

            logger.debug("未检测到Cookie弹窗（可能已接受过）")
        except Exception as e:
            logger.debug(f"Cookie处理异常: {e}")

        return False

    def _ensure_homepage_ready(self):
        """确保已在首页且CodeMirror可用"""
        if self._on_homepage:
            # 验证还在首页
            if 'cainiao.com' in self.page.url and 'newDetail' not in self.page.url:
                cm = self.page.run_js("return document.querySelector('.CodeMirror') ? 'yes' : 'no';")
                if cm == 'yes':
                    return True
            # 不在首页了，需要重新导航
            self._on_homepage = False

        # 优先尝试浏览器后退（利用bfcache，比完整重新加载快很多）
        if 'cainiao.com' in self.page.url and 'newDetail' in self.page.url:
            logger.info("从结果页后退到首页...")
            self.page.back()
            time.sleep(random.uniform(1.5, 2.5))
            # 检查后退是否成功
            if 'cainiao.com' in self.page.url and 'newDetail' not in self.page.url:
                cm = self.page.run_js("return document.querySelector('.CodeMirror') ? 'yes' : 'no';")
                if cm == 'yes':
                    self._on_homepage = True
                    logger.info("后退成功，首页就绪")
                    return True
            # 后退失败，等一下再检查
            for i in range(6):
                time.sleep(0.5)
                if 'newDetail' not in self.page.url:
                    cm = self.page.run_js("return document.querySelector('.CodeMirror') ? 'yes' : 'no';")
                    if cm == 'yes':
                        self._on_homepage = True
                        logger.info("后退成功，首页就绪")
                        return True
            logger.info("后退未恢复首页，改为完整导航...")

        # 完整导航到首页
        if not self._navigate_to_homepage():
            return False

        # 等待CodeMirror加载（缩短间隔加快检测）
        for i in range(50):
            cm = self.page.run_js("return document.querySelector('.CodeMirror') ? 'yes' : 'no';")
            if cm == 'yes':
                self._on_homepage = True
                logger.info("首页就绪，CodeMirror可用")
                return True
            time.sleep(0.3)

        # 可能有验证滑块
        logger.warning("CodeMirror未出现，可能有验证滑块。请在浏览器中手动完成（最多120秒）...")
        for i in range(24):
            time.sleep(3)
            cm = self.page.run_js("return document.querySelector('.CodeMirror') ? 'yes' : 'no';")
            if cm == 'yes':
                self._on_homepage = True
                logger.info("验证完成，首页就绪")
                return True

        logger.error("首页加载超时")
        return False

    def _switch_to_chinese(self):
        """切换语言为中文"""
        try:
            page_text = self.page.run_js("return document.body.innerText.substring(0, 100);")
            if page_text and ('查询' in page_text or '追踪' in page_text):
                return True  # 已是中文

            lang_btn = self.page.ele('css:[class*="Language--language"][aria-haspopup="true"]', timeout=2)
            if lang_btn:
                lang_btn.click()
                time.sleep(random.uniform(0.3, 0.8))
                expanded = lang_btn.attr('aria-expanded')
                if expanded == 'true':
                    self.page.run_js("""
                        var items = document.querySelectorAll('[class*="Language--languageItem"]');
                        for (var i = 0; i < items.length; i++) {
                            if (items[i].textContent.trim() === '简体中文') {
                                items[i].click();
                                break;
                            }
                        }
                    """)
                    time.sleep(random.uniform(0.5, 1))
                    logger.info("已切换为中文")
                    return True
        except Exception as e:
            logger.debug(f"语言切换跳过: {e}")
        return False

    def _input_tracking_numbers(self, tracking_numbers: List[str]) -> bool:
        """使用CodeMirror.setValue()输入单号"""
        # JS字符串中用 \\n 表示换行（JS解释为\n换行符）
        # 注意：不能对反斜杠做额外转义，否则JS会收到 \\n（字面两个字符）
        numbers_text = '\\n'.join(tracking_numbers)
        # 只转义单引号（防止破坏JS字符串边界），不转义反斜杠
        safe_text = numbers_text.replace("'", "\\'")

        js_code = """
            return (function() {
                var cm = document.querySelector('.CodeMirror');
                if (!cm || !cm.CodeMirror) return 'no_cm';
                cm.CodeMirror.setValue('""" + safe_text + """');
                var val = cm.CodeMirror.getValue();
                return 'ok:' + val.split('\\n').length + ' lines';
            })();
        """

        result = self.page.run_js(js_code)
        if result and 'ok:' in str(result):
            logger.info(f"已输入 {len(tracking_numbers)} 个单号")
            return True
        else:
            logger.error(f"CodeMirror设值失败: {result}")
            return False

    def _click_track_button(self) -> bool:
        """点击Track/查询按钮"""
        track_btn = self.page.ele('@@tag()=button@@text():查询', timeout=2)
        if not track_btn:
            track_btn = self.page.ele('css:button.track-btn', timeout=2)
        if not track_btn:
            track_btn = self.page.ele('@@tag()=button@@text():Track', timeout=2)

        if track_btn:
            track_btn.click()
            logger.info(f"已点击查询按钮: {track_btn.text}")
            return True
        else:
            # 备用：通过JS查找并点击按钮
            logger.warning("未找到Track按钮，尝试JS查找...")
            clicked = self.page.run_js("""
                return (function() {
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        var text = btns[i].textContent.trim().toLowerCase();
                        if (text === 'track' || text === '查询' || text === '追踪') {
                            btns[i].click();
                            return 'clicked:' + btns[i].textContent.trim();
                        }
                    }
                    // 最后尝试提交表单
                    var form = document.querySelector('form');
                    if (form) { form.submit(); return 'form_submitted'; }
                    return 'not_found';
                })();
            """)
            logger.info(f"JS点击结果: {clicked}")
            return clicked != 'not_found'

    def _start_network_listening(self):
        """
        使用 DrissionPage 的 listen 功能监听网络请求。
        在点击Track前开始监听，捕获 detail.json 响应。
        """
        try:
            self.page.listen.start('global/detail.json')
            logger.debug("网络监听已启动 (target: detail.json)")
            return True
        except Exception as e:
            logger.debug(f"网络监听启动失败: {e}")
            return False

    def _get_listened_data(self, timeout=30) -> Optional[dict]:
        """获取监听到的 detail.json 响应数据"""
        try:
            packet = self.page.listen.wait(timeout=timeout)
            if packet:
                # packet.response.body 是响应体
                body = packet.response.body
                if isinstance(body, str):
                    return json.loads(body)
                elif isinstance(body, dict):
                    return body
                elif isinstance(body, bytes):
                    return json.loads(body.decode('utf-8'))
            logger.warning("监听超时，未捕获到 detail.json")
        except Exception as e:
            logger.debug(f"监听获取数据异常: {e}")
        return None

    def _extract_global_data(self) -> Optional[dict]:
        """从结果页全局变量/React state中提取物流数据"""
        try:
            raw_result = self.page.run_js(EXTRACT_GLOBAL_DATA_JS)
            if not raw_result:
                return None

            result = json.loads(raw_result)
            if result.get('found') and result.get('raw'):
                logger.info(f"全局数据提取成功，来源: {result['source']}")
                try:
                    data = json.loads(result['raw'])
                    return data
                except json.JSONDecodeError:
                    # raw 可能被截断，尝试部分解析
                    logger.warning(f"全局数据JSON解析失败，来源: {result['source']}")
                    return None
            else:
                logger.debug(f"未找到全局数据: {result.get('source', 'unknown')}")
        except Exception as e:
            logger.debug(f"全局数据提取异常: {e}")
        return None

    def _wait_for_results_page(self, timeout=30) -> bool:
        """等待结果页加载完成"""
        start = time.time()
        while time.time() - start < timeout:
            url = self.page.url
            if 'newDetail' in url:
                # 等待页面内容加载
                time.sleep(random.uniform(0.5, 1))
                # 检查是否有物流卡片或TransitCard出现
                has_content = self.page.run_js("""
                    return document.querySelector('[class*="TransitCard"]') || 
                           document.querySelector('[mailno]') ? 'yes' : 'no';
                """)
                if has_content == 'yes':
                    return True
                time.sleep(random.uniform(0.3, 0.8))
            else:
                time.sleep(0.5)

        logger.warning(f"结果页加载超时, 当前URL: {self.page.url}")
        return False

    # ============ 公共接口 ============

    def query_batch(self, tracking_numbers: List[str]) -> List[dict]:
        """批量查询物流信息"""
        if not tracking_numbers:
            return []

        all_results = []
        for i in range(0, len(tracking_numbers), BATCH_QUERY_SIZE):
            batch = tracking_numbers[i:i + BATCH_QUERY_SIZE]
            results = self._query_one_batch(batch)
            all_results.extend(results)

            if i + BATCH_QUERY_SIZE < len(tracking_numbers):
                time.sleep(random.uniform(0.3, 1))

        return all_results

    def _query_one_batch(self, tracking_numbers: List[str]) -> List[dict]:
        """
        查询一批单号的完整流程：
        1. 确保在首页
        2. 输入单号
        3. 启动网络监听
        4. 点击Track
        5. 获取数据（优先listen，备用全局变量，最后DOM）
        6. 解析返回结果
        """
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"查询 {len(tracking_numbers)} 个单号 (尝试 {attempt+1}/{MAX_RETRIES})...")

                # 1. 确保在首页
                if not self._ensure_homepage_ready():
                    logger.error("无法到达首页")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(random.uniform(0.5, 1))
                    continue

                # 2. 切换中文（首次尝试时）
                if attempt == 0:
                    self._switch_to_chinese()

                # 3. 输入单号
                if not self._input_tracking_numbers(tracking_numbers):
                    logger.error("输入单号失败")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(random.uniform(0.5, 1))
                    continue

                time.sleep(random.uniform(0.2, 0.5))

                # 4. 启动网络监听（在点击Track前）
                listen_ok = self._start_network_listening()

                # 5. 点击Track按钮
                self._click_track_button()
                self._on_homepage = False

                # 6. 获取数据
                api_data = None

                # 方案A: 通过 DrissionPage listen 获取网络响应
                if listen_ok:
                    api_data = self._get_listened_data(timeout=25)

                # 方案B: 等待结果页加载，从全局变量提取
                if not api_data:
                    logger.info("listen未获取数据，尝试从结果页全局变量提取...")
                    if self._wait_for_results_page(timeout=20):
                        api_data = self._extract_global_data()

                # 方案C: DOM提取（最后备用）
                if not api_data:
                    logger.info("全局变量提取失败，尝试DOM备用方案...")
                    if 'newDetail' in self.page.url:
                        results = self._fallback_extract_from_dom(tracking_numbers)
                        if any(r['success'] for r in results):
                            self._update_stats(results)
                            return results

                    if attempt < MAX_RETRIES - 1:
                        time.sleep(random.uniform(0.5, 1))
                    continue

                # 7. 停止监听
                try:
                    self.page.listen.stop()
                except Exception:
                    pass

                # 8. 解析API数据
                results = self._parse_api_response(api_data, tracking_numbers)

                success_count = sum(1 for r in results if r['success'])
                if success_count == 0:
                    logger.warning(f"解析出0条成功结果")
                    self._save_debug_data(api_data)
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(random.uniform(0.5, 1))
                        continue

                self._update_stats(results)
                return results

            except Exception as e:
                logger.warning(f"批量查询异常 (尝试 {attempt+1}/{MAX_RETRIES}): {e}")
                self._on_homepage = False
                try:
                    self.page.listen.stop()
                except Exception:
                    pass
                if attempt < MAX_RETRIES - 1:
                    time.sleep(random.uniform(0.5, 1))

        # 所有重试失败
        self.consecutive_failures += len(tracking_numbers)
        return [self._empty_result(tn) for tn in tracking_numbers]

    def _parse_api_response(self, api_data, tracking_numbers: List[str]) -> List[dict]:
        """
        解析 API 响应数据，提取所有包裹的物流信息。
        兼容多种数据结构。
        """
        results = []

        if isinstance(api_data, str):
            try:
                api_data = json.loads(api_data)
            except json.JSONDecodeError:
                logger.error("API数据不是有效JSON")
                return [self._empty_result(tn) for tn in tracking_numbers]

        # 尝试找到包裹列表
        packages = self._find_packages_in_data(api_data)
        logger.info(f"数据中找到 {len(packages)} 个包裹")

        # 建立单号→包裹数据映射
        pkg_map = {}
        for pkg in packages:
            if isinstance(pkg, dict):
                mail_no = (pkg.get('mailNo', '') or pkg.get('mailno', '') or 
                          pkg.get('trackingNumber', '') or pkg.get('cpCode', ''))
                if mail_no:
                    pkg_map[mail_no] = pkg

        # 为每个查询的单号提取结果
        for tn in tracking_numbers:
            pkg = pkg_map.get(tn)
            if pkg:
                result = self._extract_package_info(tn, pkg)
                results.append(result)
            else:
                results.append(self._empty_result(tn))

        return results

    def _find_packages_in_data(self, data, depth=0) -> list:
        """在数据结构中寻找包裹列表"""
        if depth > 6:
            return []

        if isinstance(data, list):
            # 检查列表元素是否像包裹对象
            has_mail = any(
                isinstance(item, dict) and ('mailNo' in item or 'mailno' in item or 'trackingNumber' in item)
                for item in data[:5]  # 只检查前几个
            )
            if has_mail:
                return data
            # 递归搜索
            for item in data:
                found = self._find_packages_in_data(item, depth + 1)
                if found:
                    return found

        elif isinstance(data, dict):
            # 常见的包裹列表字段名
            list_keys = ['module', 'data', 'detailList', 'list', 'result', 
                        'trackingList', 'packages', 'items', 'traceList']
            for key in list_keys:
                if key in data:
                    found = self._find_packages_in_data(data[key], depth + 1)
                    if found:
                        return found

            # 如果当前对象本身像包裹
            if 'mailNo' in data or 'mailno' in data:
                return [data]

            # 遍历所有值
            for key, val in data.items():
                if isinstance(val, (list, dict)):
                    found = self._find_packages_in_data(val, depth + 1)
                    if found:
                        return found

        return []

    def _extract_package_info(self, tracking_number: str, pkg: dict) -> dict:
        """
        从单个包裹数据提取物流信息。
        
        已验证字段（2026-06-03，中文locale下）：
        - statusDesc: "清关"/"妥投"/"运输中" (中文主状态，可直接使用)
        - status: "CLEAR_CUSTOMS"/"DELIVERED" (英文枚举)
        - latestTrace.standerdDesc: "清关交出成功"/"用户已签收" (中文标准描述)
        - latestTrace.desc: "Leaving customs" (英文描述)
        - latestTrace.group.nodeDesc: "清关" (分组中文)
        - detailList: 物流节点数组（最新在前）
        
        返回的 status 格式: "中文主状态|中文最新物流描述"
        例如: "妥投|用户已签收", "清关|清关交出成功", "运输中|包裹到达干线注入港"
        """
        latest_detail_cn = ''
        latest_time = ''

        # API顶层状态描述（中文locale下直接返回中文如"清关"/"妥投"）
        main_status = pkg.get('statusDesc', '') or ''

        # 最新物流轨迹：latestTrace > globalCombinedLogisticsTraceDTO > detailList[0]
        # 优先取 standerdDesc（中文标准描述），其次 desc（英文）
        latest_trace = pkg.get('latestTrace') or pkg.get('globalCombinedLogisticsTraceDTO') or {}
        if isinstance(latest_trace, dict) and latest_trace:
            latest_detail_cn = latest_trace.get('standerdDesc', '') or latest_trace.get('desc', '')
            latest_time = latest_trace.get('timeStr', '') or latest_trace.get('time', '')

        # 如果 latestTrace 为空，从 detailList 取第一个
        if not latest_detail_cn:
            detail_list = pkg.get('detailList', [])
            if isinstance(detail_list, list) and detail_list:
                first = detail_list[0] if isinstance(detail_list[0], dict) else {}
                latest_detail_cn = first.get('standerdDesc', '') or first.get('desc', '')
                if not latest_time:
                    latest_time = first.get('timeStr', '') or first.get('time', '')

        # 如果 statusDesc 为空或英文，使用翻译兜底
        if not main_status:
            status_enum = pkg.get('status', '')
            main_status = self._translate_status(status_enum, latest_detail_cn)
        elif main_status.isascii():
            # statusDesc是英文（未成功切换locale），翻译为中文
            main_status = self._translate_status(main_status, latest_detail_cn)

        # 组合格式: "中文主状态|中文最新物流描述"
        status = f"{main_status}|{latest_detail_cn}" if latest_detail_cn else main_status

        success = bool(latest_detail_cn)
        return {
            'tracking_number': tracking_number,
            'status': status,
            'latest_time': latest_time,
            'latest_detail': latest_detail_cn,
            'success': success,
        }

    def _fallback_extract_from_dom(self, tracking_numbers: List[str]) -> List[dict]:
        """备用方案：从结果页DOM中提取数据"""
        logger.info("DOM备用提取...")
        time.sleep(random.uniform(0.3, 0.8))

        results = []
        for tn in tracking_numbers:
            try:
                card = self.page.ele(f'[mailno="{tn}"]', timeout=2)
                if card:
                    card.click()
                    time.sleep(random.uniform(0.3, 0.8))

                    detail_data = self.page.run_js("""
                        return (function() {
                            var cards = document.querySelectorAll('[class*="TransitCard--wrapper"], [class*="TransitCard"]');
                            if (cards.length === 0) return null;
                            var first = cards[0];
                            var text = first.innerText.trim();
                            var lines = text.split('\\n').filter(function(l) { return l.trim(); });
                            var detail = '', timeStr = '';
                            for (var j = 0; j < lines.length; j++) {
                                var line = lines[j].trim();
                                if (line.match(/\\d{4}-\\d{2}-\\d{2}/) && !timeStr) timeStr = line;
                                else if (!detail && line.length > 5) detail = line;
                            }
                            if (!detail && lines.length > 0) detail = lines[0];
                            return JSON.stringify({detail: detail, time: timeStr});
                        })();
                    """)

                    if detail_data:
                        info = json.loads(detail_data)
                        if info.get('detail'):
                            main_status = self._translate_status('', info['detail'])
                            full_status = f"{main_status}|{info['detail']}"
                            results.append({
                                'tracking_number': tn,
                                'status': full_status,
                                'latest_time': info.get('time', ''),
                                'latest_detail': info['detail'],
                                'success': True,
                            })
                            continue
            except Exception as e:
                logger.debug(f"DOM提取异常 {tn}: {e}")

            results.append(self._empty_result(tn))

        return results

    def _translate_status(self, status_desc_en: str, latest_detail: str = '') -> str:
        """
        将菜鸟API的英文statusDesc翻译为中文主状态
        
        菜鸟API的statusDesc/status枚举值:
        - DELIVERED / Delivered → 妥投
        - RETURN / Returning → 退回 (需进一步判断是否退件签收成功)
        - IN_TRANSIT / In transit → 运输中
        - CUSTOMS / Customs clearance → 清关
        - PICKED_UP / Picked up → 已揽收
        - DELIVERING / Out for delivery → 派送中
        - EXCEPTION → 异常
        - WAIT_SELLER_SEND / Waiting → 待发货
        """
        text = status_desc_en.lower() if status_desc_en else ''
        detail_lower = latest_detail.lower() if latest_detail else ''
        
        if 'deliver' in text and 'out' not in text and 'return' not in text:
            # "Delivered" - 妥投
            return '妥投'
        elif 'return' in text:
            # "Returning" - 退回，需要判断是否已签收
            if 'signed' in detail_lower or 'sender' in detail_lower or '签收' in detail_lower:
                return '退回'  # 退件签收成功
            return '退回'
        elif 'transit' in text or 'in_transit' in text:
            return '运输中'
        elif 'customs' in text or 'clearance' in text:
            return '清关'
        elif 'pick' in text or 'collected' in text:
            return '已揽收'
        elif 'delivering' in text or 'out for' in text or 'delivery' in text:
            return '派送中'
        elif 'exception' in text or 'abnormal' in text:
            return '异常'
        elif 'wait' in text:
            return '待发货'
        
        # 兜底：从最新物流详情推断
        if detail_lower:
            if 'delivered' in detail_lower or 'package delivered' in detail_lower:
                return '妥投'
            elif 'return' in detail_lower and ('signed' in detail_lower or 'sender' in detail_lower):
                return '退回'
            elif 'return' in detail_lower:
                return '退回'
            elif 'customs' in detail_lower or 'clearance' in detail_lower or '清关' in detail_lower:
                return '清关'
            elif 'transit' in detail_lower or 'depart' in detail_lower or 'arriv' in detail_lower:
                return '运输中'
            elif 'delivery' in detail_lower or 'dispatch' in detail_lower:
                return '派送中'
        
        return '运输中'

    def _update_stats(self, results: List[dict]):
        """更新统计数据"""
        success_count = sum(1 for r in results if r['success'])
        self.success_count += success_count
        self.fail_count += len(results) - success_count
        if success_count > 0:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += len(results)
        logger.info(f"本批结果: {success_count}/{len(results)} 成功")

    def _save_debug_data(self, api_data):
        """保存API原始数据用于调试"""
        try:
            debug_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_api_response.json')
            with open(debug_file, 'w', encoding='utf-8') as f:
                json.dump(api_data, f, ensure_ascii=False, indent=2)
            logger.info(f"调试数据已保存: debug_api_response.json")
        except Exception as e:
            logger.debug(f"保存调试数据失败: {e}")

    def query_single(self, tracking_number: str) -> dict:
        """查询单条"""
        results = self.query_batch([tracking_number])
        return results[0] if results else self._empty_result(tracking_number)

    def _empty_result(self, tracking_number: str) -> dict:
        """空结果"""
        return {
            'tracking_number': tracking_number,
            'status': '',
            'latest_time': '',
            'latest_detail': '',
            'success': False,
        }

    def close(self):
        """关闭浏览器"""
        if self.page:
            try:
                self.page.listen.stop()
            except Exception:
                pass
            try:
                self.page.quit()
                logger.info("查询浏览器已关闭")
            except Exception:
                pass
            self.page = None

    def get_stats(self) -> dict:
        """获取查询统计"""
        return {
            "success": self.success_count,
            "failed": self.fail_count,
            "consecutive_failures": self.consecutive_failures,
            "total": self.success_count + self.fail_count,
        }

    def __del__(self):
        self.close()
