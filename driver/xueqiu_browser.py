import os
import sys
import re
import asyncio
import time
import threading
import json
import subprocess
import signal
import concurrent.futures
from typing import Dict, List, Optional, Any, Tuple

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from core.print import print_error, print_info, print_warning, print_success
from core.config import cfg
from core.xueqiu.cfg import xq_cfg
from driver.anti_crawler_config import AntiCrawlerConfig
from driver.xueqiu_store import XueqiuStore


class XueqiuBrowserManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls):
        return cls._instance

    @classmethod
    def ensure_started(cls, timeout: float = 35) -> "XueqiuBrowserManager":
        """获取浏览器管理器实例并确保浏览器已启动

        封装"获取实例 → 判空 → 创建 → 启动"的完整流程，
        调用方无需关心浏览器启动细节。

        使用专用后台事件循环线程启动浏览器，确保所有 Playwright
        操作都在同一个事件循环中执行，避免跨事件循环卡死。

        Args:
            timeout: 浏览器启动超时时间（秒）

        Returns:
            已启动的 XueqiuBrowserManager 实例
        """
        mgr = cls.get_instance()
        if mgr is None:
            mgr = cls()
        if not mgr.is_ready:
            mgr._ensure_loop_and_start(timeout=timeout)
        return mgr

    def _ensure_loop_and_start(self, timeout: float = 35):
        """确保后台事件循环已运行，并在其中启动浏览器

        Playwright 对象绑定创建时的事件循环，后续所有操作必须在同一循环中执行。
        因此创建一个专用后台线程来运行持久事件循环，浏览器启动和所有
        fetch_via_browser 调用都在此循环中执行。
        """
        if self._event_loop is None or not self._event_loop.is_running():
            self._start_background_loop()

        future = asyncio.run_coroutine_threadsafe(self.start(), self._event_loop)
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print_error(f"雪球浏览器启动超时({timeout}s)")
        except Exception as e:
            print_error(f"雪球浏览器启动失败: {e}")

    def _start_background_loop(self):
        """启动后台事件循环线程

        创建一个守护线程运行 asyncio 事件循环，该循环在应用程序
        整个生命周期内持续运行，所有 Playwright 操作都在此循环中执行。
        """
        self._event_loop = asyncio.new_event_loop()

        def _run_loop(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._loop_thread = threading.Thread(
            target=_run_loop, args=(self._event_loop,),
            name="xueqiu-browser-loop", daemon=True
        )
        self._loop_thread.start()

    def __init__(self, headless: bool = None, proxy_url: str = ""):
        if self._initialized:
            return
        self._initialized = True
        self._playwright = None
        self._browser = None
        # headless 优先级：构造函数参数 → 配置文件 → 环境变量 → 默认 True
        # 雪球无需显示二维码登录，Docker 环境中应始终使用 headless 模式
        if headless is not None:
            self.headless = headless
        else:
            cfg_headless = xq_cfg("browser.headless", None)
            if cfg_headless is not None:
                self.headless = str(cfg_headless).lower() == "true"
            else:
                self.headless = os.environ.get("HEADLESS", "true").lower() == "true"
        self.proxy_url = proxy_url or xq_cfg("proxy.http_url", "")
        self._cookie_cache: Optional[str] = None
        self._cookie_acquired_at: float = 0
        self._cookie_max_age: int = int(xq_cfg("cookie.maxAge", 3600000)) / 1000
        self._cookie_refresh_interval: int = int(xq_cfg("cookie.refreshInterval", 1800000)) / 1000
        self._refresh_task: Optional[asyncio.Task] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._anti_crawler_config = AntiCrawlerConfig()
        self._user_agent = self._anti_crawler_config._ua_generator.get_realistic_user_agent(False)
        # 启动时清理可能残留的旧浏览器进程
        self._cleanup_orphan_processes()
        # 启动时尝试从持久化存储恢复 Cookie
        self._restore_cookies_from_store()

    def __del__(self):
        """析构时取消未完成的 Task，防止 'Task was destroyed but it is pending!' 警告"""
        if hasattr(self, '_refresh_task') and self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

    def _cleanup_orphan_processes(self):
        """清理可能残留的旧 Playwright 浏览器进程

        当 main.py 重启后，旧的 Playwright 子进程（chromium/webkit）可能仍在运行。
        这些进程的父进程已经是 PID 1（init），不再受任何 Python 进程管理，
        会导致资源泄漏和新实例启动时连接到僵死的旧实例。

        清理策略：
        1. 找到属于当前用户的 playwright/chromium/webkit 进程
        2. 检查其父进程是否仍存在（如果 PPID=1 说明是孤儿）
        3. 安全地终止孤儿进程
        """
        try:
            current_pid = os.getpid()
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True, text=True, timeout=5
            )

            orphan_pids = []
            for line in result.stdout.split('\n'):
                line_lower = line.lower()
                # 匹配 Playwright 启动的浏览器进程
                if not any(kw in line_lower for kw in ['chromium_headless_shell', 'webkit', 'minibrowser', 'playwright/driver']):
                    continue
                # 排除当前进程本身和 grep
                if str(current_pid) in line or 'grep' in line_lower:
                    continue

                parts = line.split()
                if len(parts) < 2:
                    continue

                try:
                    pid = int(parts[1])
                    ppid = int(parts[2]) if len(parts) > 2 else 0
                except (ValueError, IndexError):
                    continue

                # 检查是否是孤儿进程（PPID=1 或父进程不存在）
                if ppid <= 1:
                    orphan_pids.append(pid)
                    continue

                # 检查父进程是否仍存在
                try:
                    os.kill(ppid, 0)  # 不发送信号，只检查进程是否存在
                except OSError:
                    # 父进程不存在，这是孤儿
                    orphan_pids.append(pid)

            if orphan_pids:
                print_warning(f"发现 {len(orphan_pids)} 个旧的 Playwright 浏览器孤儿进程: {orphan_pids}")
                for pid in orphan_pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        print_info(f"已发送 SIGTERM 给孤儿进程 {pid}")
                    except OSError:
                        pass  # 进程可能已经退出

                # 等待进程退出
                time.sleep(2)

                # 检查是否仍在运行，强制杀
                for pid in orphan_pids:
                    try:
                        os.kill(pid, 0)  # 检查是否还活着
                        os.kill(pid, signal.SIGKILL)
                        print_warning(f"孤儿进程 {pid} 未响应 SIGTERM，已发送 SIGKILL")
                    except OSError:
                        pass  # 进程已退出

                print_success("旧的 Playwright 浏览器孤儿进程清理完成")
            else:
                pass  # 没有孤儿进程，无需输出

        except Exception as e:
            print_warning(f"清理旧浏览器进程时出错（不影响正常启动）: {e}")

    def _restore_cookies_from_store(self):
        """从持久化存储（Redis/文件）恢复 Cookie 到内存缓存"""
        try:
            stored_cookies = XueqiuStore.load()
            if stored_cookies and XueqiuStore.has_auth_cookies(stored_cookies):
                # 将 Playwright Cookie 列表转为字符串缓存
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in stored_cookies)
                self._cookie_cache = cookie_str
                self._cookie_acquired_at = time.time()
                print_success(f"雪球Cookie从持久化存储恢复成功, 共{len(stored_cookies)}个Cookie")
            else:
                print_info("雪球持久化存储中无有效Cookie, 启动后将走WAF预热获取")
        except Exception as e:
            print_warning(f"雪球Cookie从持久化存储恢复失败: {e}")

    async def start(self):
        if self._browser and self._browser.is_connected():
            print_success("雪球浏览器已在运行，无需重复启动")
            return
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()

            launch_options = {
                "headless": self.headless,
            }

            # 显式传递 DISPLAY 环境变量（用于 Xvfb）
            display_env = os.environ.get("DISPLAY", ":99")
            launch_options["env"] = {"DISPLAY": display_env}

            print_info(f"启动雪球浏览器 (headless={self.headless}, {launch_options}")

            if self.proxy_url:
                launch_options["proxy"] = {"server": self.proxy_url}
            self._browser = await self._playwright.webkit.launch(**launch_options)
            print_success(f"雪球浏览器启动成功 (browser=webkit, headless={self.headless})")

            # 判断是否需要走完整 WAF 预热
            if self._cookie_cache and self.cookie_valid:
                # 内存中已有有效 Cookie（来自持久化恢复），验证是否仍然可用
                need_warmup = not await self._validate_cached_cookies()
                if need_warmup:
                    print_info("雪球恢复的Cookie已失效, 执行WAF预热...")
                    await self._warmup()
                else:
                    print_success("雪球恢复的Cookie验证通过, 跳过WAF预热")
            else:
                # 无有效 Cookie，走完整 WAF 预热
                await self._warmup()

            self._start_cookie_refresh_loop()
        except Exception as e:
            print_error(f"雪球浏览器启动失败: {e}")
            raise

    async def _validate_cached_cookies(self) -> bool:
        """验证内存缓存的 Cookie 是否仍可访问雪球 API

        通过创建一个临时上下文，注入已有 Cookie，尝试访问搜索接口来验证。
        如果搜索接口返回正常则说明 Cookie 有效，可以跳过 WAF 预热。

        Returns:
            bool: Cookie 是否有效
        """
        context, page = None, None
        try:
            context, page = await self._create_context()
            # 用已有 Cookie 访问雪球首页，检查是否被 WAF 拦截
            response = await page.goto(
                "https://xueqiu.com/",
                wait_until="domcontentloaded",
                timeout=15000
            )
            await asyncio.sleep(1)
            title = await page.title()
            # 检查是否被 WAF 拦截（标题中含"验证"或重定向到挑战页）
            if "验证" in title:
                return False
            # 如果页面正常加载，更新 Cookie
            cookies = await context.cookies()
            auth_names = {"xq_a_token", "xqat", "xq_r_token"}
            cookie_names = {c.get("name", "") for c in cookies}
            if auth_names & cookie_names:
                self._update_cookie_cache(cookies)
                return True
            return False
        except Exception as e:
            print_warning(f"雪球Cookie验证失败: {e}")
            return False
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    async def _warmup(self):
        """WAF 预热：访问雪球首页，完成 JS 挑战获取认证 Cookie

        使用 domcontentloaded 替代 networkidle，因为 WAF 挑战页面
        会持续发起网络请求导致 networkidle 永远无法达到。
        预热后等待一定时间确保 WAF 挑战完成并获取 Cookie。
        """
        print_info("雪球WAF预热中...")
        context, page = None, None
        try:
            context, page = await self._create_context()
            # 第一步：加载首页，触发 WAF 挑战
            await page.goto("https://xueqiu.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # 第二步：检测并等待 WAF 挑战完成
            content = await page.content()
            if self._is_waf_challenge(content):
                print_info("雪球WAF预热: 检测到WAF挑战页面, 等待自动完成...")
                try:
                    # 等待 WAF JS 执行完毕并重定向（最多 30s）
                    await asyncio.wait_for(
                        page.wait_for_url(
                            lambda u: "md5__1038" in u or "xueqiu.com/" == u[-12:] or "xueqiu.com" == u.split("?")[0].split("/")[-1],
                            timeout=25000
                        ),
                        timeout=30
                    )
                    await asyncio.sleep(1)
                except asyncio.TimeoutError:
                    print_warning("雪球WAF预热: 挑战等待超时, 尝试继续...")

            # 第三步：检查是否获取到认证 Cookie
            title = await page.title()
            if "验证" in title:
                print_warning("雪球WAF验证码弹出, 需要人工处理")
            else:
                cookies = await context.cookies()
                self._update_cookie_cache(cookies)
                auth_names = {"xq_a_token", "xqat", "xq_r_token"}
                cookie_names = {c.get("name", "") for c in cookies}
                if auth_names & cookie_names:
                    print_success(f"雪球WAF预热完成, 获取到{len(cookies)}个Cookie(含认证Cookie)")
                else:
                    print_warning(f"雪球WAF预热完成, 获取到{len(cookies)}个Cookie, 但缺少认证Cookie")
        except Exception as e:
            print_warning(f"雪球WAF预热失败: {e}")
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    async def _create_context(self) -> Tuple[Any, Any]:
        """创建一个新的 BrowserContext 和 Page

        每次调用创建独立的上下文，调用方负责在使用完毕后关闭 context。

        Returns:
            (context, page) 元组
        """
        anti_config = self._anti_crawler_config.get_anti_crawler_config(False)
        context_options = {
            "user_agent": self._user_agent,
            "viewport": anti_config.get("viewport", {"width": 1920, "height": 1080}),
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if "extra_http_headers" in anti_config:
            context_options["extra_http_headers"] = anti_config["extra_http_headers"]
        for key in ["ignore_https_errors", "bypass_csp"]:
            if key in anti_config:
                context_options[key] = anti_config[key]
        context = await self._browser.new_context(**context_options)
        page = await context.new_page()
        init_script = AntiCrawlerConfig.get_init_script()
        await context.add_init_script(init_script)

        # 注入 Cookie：优先使用 Playwright 格式的持久化 Cookie，其次使用字符串缓存
        cookie_list = self._get_cookies_for_injection()
        if cookie_list:
            await context.add_cookies(cookie_list)

        return context, page

    def _get_cookies_for_injection(self) -> List[Dict]:
        """获取用于注入浏览器上下文的 Cookie 列表

        优先使用 Playwright 格式（保留 domain/path 等完整信息），
        因为持久化存储的 Cookie 保留了完整的 Playwright 格式字段。

        Returns:
            Playwright Cookie 列表
        """
        # 先尝试从持久化存储获取完整格式的 Cookie
        try:
            stored = XueqiuStore.load()
            if stored:
                return stored
        except Exception:
            pass

        # 回退到内存字符串缓存
        if self._cookie_cache:
            return self._parse_cookie_string(self._cookie_cache)

        return []

    async def fetch_via_browser(self, url: str, owner: str = None) -> Optional[Dict]:
        """通过浏览器请求雪球内容

        统一使用 Playwright 浏览器环境发送请求。
        雪球 WAF 会检查 TLS 指纹等浏览器特征，requests 库无法绕过，
        必须通过浏览器环境发起请求。

        请求策略：
        1. 创建浏览器上下文 + 注入已有 Cookie
        2. page.goto() 导航到目标 URL
        3. 通过 response 事件捕获 JSON 响应
        4. 如果被 WAF 拦截，等待挑战完成后重新导航
        """
        is_json_api = ".json" in url.split("?")[0]

        context, page = None, None
        try:
            context, page = await self._create_context()
            json_response = None

            async def _capture_response(response):
                nonlocal json_response
                if response.url.startswith(url.split('?')[0]):
                    try:
                        json_response = await response.json()
                    except Exception:
                        text = await response.text()
                        if text:
                            try:
                                json_response = json.loads(text)
                            except Exception:
                                pass

            page.on("response", _capture_response)

            # 首次导航
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                print_warning(f"雪球浏览器首次加载超时: {url}, 尝试等待页面就绪...")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    print_error(f"雪球浏览器页面加载彻底超时: {url}")
                    return None

            # 等待 API 响应
            # JSON API 响应通常在 goto 完成后即已捕获，只需短暂等待
            # 网页 URL 可能需要等待 AJAX 请求完成
            if json_response is None:
                initial_wait = 1 if is_json_api else 3
                poll_interval = 0.5 if is_json_api else 2
                max_polls = 4 if is_json_api else 5
                await asyncio.sleep(initial_wait)
                for _ in range(max_polls):
                    if json_response is not None:
                        break
                    await asyncio.sleep(poll_interval)

            # 检测是否被 WAF 拦截
            content = await page.content()
            is_waf = self._is_waf_challenge(content)

            if is_waf:
                print_info(f"雪球检测到WAF挑战页面, 等待挑战自动完成...")
                try:
                    await page.wait_for_url(
                        lambda u: "md5__1038" in u or u == url or "/v4/statuses/" in u or "/query/" in u,
                        timeout=25000
                    )
                    print_info("雪球WAF挑战已通过, 重新加载目标页面...")
                    json_response = None
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    for _ in range(5):
                        if json_response is not None:
                            break
                        await asyncio.sleep(2)
                except Exception as e:
                    print_warning(f"雪球WAF挑战等待异常: {e}")
                    content = await page.content()
                    if self._is_waf_challenge(content):
                        print_error(f"雪球WAF挑战未通过, 请求失败: {url}")
                        return None

            # 检查验证码
            title = await page.title()
            if "验证" in title:
                print_warning("雪球检测到验证码, 停止请求")
                return {"error": "验证码弹出", "error_code": "CAPTCHA"}

            # 更新 Cookie 缓存
            try:
                cookies = await context.cookies()
                self._update_cookie_cache(cookies)
            except Exception:
                pass

            # 返回捕获的 JSON 响应
            if json_response is not None:
                return json_response

            # 最终回退：解析页面内容为 JSON
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"error": "响应不是有效JSON", "raw": content[:500]}

        except Exception as e:
            print_error(f"雪球浏览器请求失败: {e}")
            return None
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    def fetch_sync(self, url: str, owner: str = None, timeout: float = 60) -> Optional[Dict]:
        """从同步上下文安全地调用 fetch_via_browser

        核心问题：run_async() 会在新线程中创建新事件循环，
        但 Playwright 浏览器对象绑定在创建它的事件循环上，
        跨事件循环操作会卡死（60s 超时）。

        解决方案：使用 asyncio.run_coroutine_threadsafe() 将协程
        提交到浏览器所在的专用事件循环（后台守护线程）执行。

        Args:
            url: 目标 URL
            owner: 调用方标识
            timeout: 超时时间（秒）

        Returns:
            fetch_via_browser 的返回值
        """
        if not self._event_loop or not self._event_loop.is_running():
            print_error("雪球浏览器事件循环未运行，无法发起请求")
            return None

        coro = self.fetch_via_browser(url, owner=owner)
        future = asyncio.run_coroutine_threadsafe(coro, self._event_loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print_error(f"雪球浏览器请求超时({timeout}s): {url}")
            future.cancel()
            return None
        except Exception as e:
            print_error(f"雪球浏览器请求异常: {e}")
            return None

    def _is_waf_challenge(self, content: str) -> bool:
        """检测页面内容是否为 WAF 挑战页面

        雪球使用的阿里云 WAF 挑战页面特征：
        - 包含 acw_tc 相关 JavaScript 代码
        - 包含 arg1/arg2 等挑战参数
        - 包含 md5__1038 相关重定向逻辑
        - 页面主体为空或只有脚本标签

        Args:
            content: 页面 HTML 内容

        Returns:
            bool: 是否为 WAF 挑战页面
        """
        if not content:
            return False

        # WAF 挑战页面的典型特征
        waf_signatures = [
            "acw_tc",
            "md5__1038",
            "arg1=",
            "arg2=",
            "acwSDK",
        ]

        # 如果页面包含多个 WAF 特征，基本确定是 WAF 挑战页
        match_count = sum(1 for sig in waf_signatures if sig in content)
        if match_count >= 2:
            return True

        # 如果页面几乎为空且包含脚本标签，也可能是 WAF 挑战
        # WAF 挑战页面通常 <body> 内只有 <script> 标签
        text_content = content.lower()
        if len(content) < 5000 and "<script" in text_content and "<body" in text_content:
            body_match = re.search(r'<body[^>]*>(.*?)</body>', text_content, re.DOTALL)
            if body_match:
                body_content = body_match.group(1).strip()
                # 如果 body 内容很短且主要是 script 标签
                if len(body_content) < 2000 and "<script" in body_content:
                    non_script = re.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=re.DOTALL).strip()
                    if not non_script or len(non_script) < 50:
                        return True

        return False

    def _update_cookie_cache(self, cookies: List[Dict]):
        """更新内存 Cookie 缓存，并同步持久化到 Redis/文件"""
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        if cookie_str:
            self._cookie_cache = cookie_str
            self._cookie_acquired_at = time.time()
            # 持久化到 Redis + 文件
            try:
                XueqiuStore.save(cookies)
            except Exception as e:
                print_warning(f"雪球Cookie持久化失败: {e}")

    def _parse_cookie_string(self, cookie_str: str) -> List[Dict]:
        cookies = []
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                name, value = item.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".xueqiu.com",
                    "path": "/",
                })
        return cookies

    def get_cookies_str(self) -> str:
        """获取Cookie字符串（三级降级：内存缓存 → 持久化存储 → 配置默认值）

        统一Cookie获取入口，调用方无需关心Cookie来源细节。
        """
        if self._cookie_cache:
            return self._cookie_cache
        try:
            stored = XueqiuStore.load()
            if stored:
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in stored)
                if cookie_str:
                    self._cookie_cache = cookie_str
                    self._cookie_acquired_at = time.time()
                    return cookie_str
        except Exception:
            pass
        return xq_cfg("cookies", "")

    @property
    def cookie_valid(self) -> bool:
        if not self._cookie_cache:
            return False
        return (time.time() - self._cookie_acquired_at) < self._cookie_max_age

    @property
    def is_ready(self) -> bool:
        """浏览器是否已启动且连接正常"""
        return self._browser is not None and self._browser.is_connected()

    def refresh_cookies_sync(self) -> Optional[str]:
        """同步方式刷新雪球Cookie

        通过 run_coroutine_threadsafe 将刷新协程提交到浏览器所在的后台
        事件循环执行，避免跨事件循环操作 Playwright 导致卡死。
        """
        if self.cookie_valid:
            return self._cookie_cache

        if not self._event_loop or not self._event_loop.is_running():
            print_error("雪球浏览器事件循环未运行，无法刷新Cookie")
            return self._cookie_cache

        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(self._refresh_cookies(), timeout=30),
            self._event_loop
        )
        try:
            future.result(timeout=35)
        except concurrent.futures.TimeoutError:
            print_error("雪球Cookie同步刷新超时")
        except Exception as e:
            print_error(f"雪球Cookie同步刷新失败: {e}")

        return self._cookie_cache

    async def _refresh_cookies(self):
        if not self._browser or not self._browser.is_connected():
            await self.start()
            return
        context, page = None, None
        try:
            context, page = await self._create_context()
            await page.goto("https://xueqiu.com/", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            # 检测并等待 WAF 挑战完成
            content = await page.content()
            if self._is_waf_challenge(content):
                print_info("雪球Cookie刷新: 检测到WAF挑战, 等待自动完成...")
                try:
                    await asyncio.wait_for(
                        page.wait_for_url(
                            lambda u: "md5__1038" in u or "xueqiu.com/" == u[-12:],
                            timeout=20000
                        ),
                        timeout=25
                    )
                    await asyncio.sleep(1)
                except asyncio.TimeoutError:
                    print_warning("雪球Cookie刷新: WAF挑战等待超时")
            cookies = await context.cookies()
            self._update_cookie_cache(cookies)
            print_success(f"雪球Cookie刷新成功, 获取到{len(cookies)}个Cookie")
        except Exception as e:
            print_error(f"雪球Cookie刷新失败: {e}")
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    def _start_cookie_refresh_loop(self):
        """启动基于 asyncio 的 Cookie 定时刷新循环

        替代原先的 threading.Timer 方案，确保刷新操作与 Playwright
        在同一个事件循环中执行，避免跨事件循环操作导致的死锁。

        刷新间隔结束后在当前事件循环中直接 await _refresh_cookies()，
        然后通过 asyncio.create_task 注册下一轮。
        """
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(self._cookie_refresh_loop())

    async def _cookie_refresh_loop(self):
        """Cookie 定时刷新的异步循环

        每次等待 _cookie_refresh_interval 秒后刷新一次 Cookie，
        如果浏览器已关闭则自动退出循环。
        """
        while self._browser and self._browser.is_connected():
            try:
                await asyncio.sleep(self._cookie_refresh_interval)
            except asyncio.CancelledError:
                # close() 取消时正常退出
                break
            if not self._browser or not self._browser.is_connected():
                break
            try:
                await self._refresh_cookies()
            except Exception as e:
                print_error(f"雪球Cookie定时刷新失败: {e}")

    async def health_check(self) -> Dict:
        browser_ok = self.is_ready
        # 检查持久化存储中是否有 Cookie
        store_has_cookies = XueqiuStore.has_auth_cookies()
        return {
            "browser_connected": browser_ok,
            "cookie_valid": self.cookie_valid,
            "cookie_age_seconds": int(time.time() - self._cookie_acquired_at) if self._cookie_acquired_at else 0,
            "cookie_persisted": store_has_cookies,
        }

    async def close(self):
        """关闭浏览器管理器，释放所有资源

        必须在浏览器所在的事件循环（后台守护线程）中调用。
        """
        # 关闭前持久化当前 Cookie（优先使用 Playwright 完整格式）
        try:
            stored = XueqiuStore.load()
            if not stored and self._cookie_cache:
                # 没有已有的完整格式缓存，才用字符串缓存兜底
                XueqiuStore.save(self._cookie_cache)
        except Exception:
            pass
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        self._refresh_task = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        # 确保所有 Playwright 子进程被清理
        self._cleanup_orphan_processes()

        # 停止后台事件循环
        if self._event_loop and self._event_loop.is_running():
            self._event_loop.call_soon_threadsafe(self._event_loop.stop)

        # 重置单例，允许后续重新创建实例
        XueqiuBrowserManager._instance = None

        print_info("雪球浏览器管理器已关闭")
