import time
import random
import re
import json
import requests
from typing import Optional, Dict, Any
from core.print import print_error, print_info, print_warning
from core.config import cfg
from .cfg import xq_cfg
from .parser import XueqiuParser

_UA_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"macOS"',
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"macOS"',
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
    },
]

XUEQIU_SEARCH_URL = "https://xueqiu.com/query/v1/search/user.json"
XUEQIU_TIMELINE_URL = "https://xueqiu.com/v4/statuses/user_timeline.json"


class XueqiuApi:
    def __init__(self):
        """初始化雪球API客户端

        创建HTTP会话、设置超时、加载雪球配置（Cookie/代理/重试参数）
        """
        self.session = requests.Session()
        self.session.timeout = (5, 15)
        self._load_config()

    def _load_config(self):
        """加载雪球配置参数

        从 xq_cfg 配置中读取 Cookie、代理、请求延迟、超时、重试次数等参数，
        其中 delay/timeout 以毫秒为单位，转换为秒。
        """
        cfg.reload()
        self.cookies = xq_cfg("cookies", "")
        self.proxy_enabled = xq_cfg("proxy.enabled", False)
        self.http_proxy_url = xq_cfg("proxy.http_url", "")
        self.batch_delay = xq_cfg("request.batch_delay", 1000) / 1000.0
        self.timeout = xq_cfg("request.timeout", 15000) / 1000.0
        self.max_retries = xq_cfg("request.max_retries", 2)

    def _get_cookies(self) -> str:
        """获取雪球Cookie字符串

        统一通过 XueqiuBrowserManager.get_cookies_str() 获取，
        内部自动完成三级降级：内存缓存 → 持久化存储 → 配置默认值
        """
        try:
            from driver.xueqiu_browser import XueqiuBrowserManager
            mgr = XueqiuBrowserManager.get_instance()
            if mgr:
                return mgr.get_cookies_str()
        except Exception:
            pass

        return self.cookies

    def _build_headers(self) -> Dict[str, str]:
        """构建雪球API请求头

        随机选取一组UA配置，包含 Cookie、User-Agent、Sec-CH-UA 等，
        附带雪球站点的 Origin/Referer/Sec-Fetch 等反爬必需字段。
        """
        profile = random.choice(_UA_PROFILES)
        return {
            "Cookie": self._get_cookies(),
            "User-Agent": profile["ua"],
            "Referer": "https://xueqiu.com/",
            "Origin": "https://xueqiu.com",
            "sec-ch-ua": profile["sec_ch_ua"],
            "sec-ch-ua-mobile": profile["sec_ch_ua_mobile"],
            "sec-ch-ua-platform": profile["sec_ch_ua_platform"],
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _get_proxies(self) -> Optional[Dict]:
        """获取HTTP代理配置"""
        if not self.proxy_enabled or not self.http_proxy_url:
            return None
        return {"http": self.http_proxy_url, "https": self.http_proxy_url}

    def _request(self, url: str, params: Dict = None) -> Optional[Dict]:
        """统一请求入口：HTTP请求失败时自动降级到浏览器

        先尝试HTTP请求，当返回 WAF 拦截（401/403/HTML非JSON）时，
        自动降级到浏览器环境发起请求，突破雪球的反爬机制。
        """
        http_result = self._request_via_http(url, params)
        if http_result is not None:
            if isinstance(http_result, dict) and "error" in http_result:
                error_code = http_result.get("error_code", "")
                if error_code in (401, 403, "401", "403") or "WAF" in str(error_code):
                    print_warning(f"[{url}] HTTP请求被拦截，降级到浏览器...")
                else:
                    return http_result
            else:
                return http_result

        browser_result = self._request_via_browser(url, params)
        if browser_result is not None:
            if isinstance(browser_result, dict) and "error" in browser_result:
                error_msg = browser_result.get("error", "")
                if "验证码" in str(error_msg):
                    print_warning(f"[{url}] 雪球浏览器请求遇到验证码: {error_msg}")
            return browser_result
        return None

    def _request_via_http(self, url: str, params: Dict = None) -> Optional[Dict]:
        """纯HTTP方式请求雪球API

        带重试机制：
        - 401/403 → 尝试刷新Cookie后重试
        - 429 → 指数退避等待后重试
        - WAF拦截（HTML/空响应/非JSON）→ 返回None，由外层降级到浏览器
        - 业务认证失败（code=400016/17/18）→ 尝试刷新Cookie后重试
        """
        for attempt in range(self.max_retries + 1):
            try:
                req_headers = self._build_headers()
                resp = self.session.get(
                    url,
                    params=params,
                    headers=req_headers,
                    proxies=self._get_proxies(),
                    timeout=self.timeout,
                )

                if resp.status_code == 401 or resp.status_code == 403:
                    print_warning(f"[{url}] HTTP请求认证失败({resp.status_code})")
                    if attempt < self.max_retries:
                        if self._try_refresh_cookies():
                            continue
                        time.sleep(1)
                    return {"error": f"认证失败({resp.status_code})", "error_code": resp.status_code}

                if resp.status_code == 429:
                    wait = (attempt + 1) * 2
                    print_warning(f"[{url}] HTTP请求频率限制, 等待{wait}秒")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()

                content_type = resp.headers.get("Content-Type", "")

                if not resp.text or resp.text.strip() == "":
                    print_warning(f"[{url}] HTTP请求返回空响应(WAF拦截)")
                    return None

                if "text/html" in content_type and "application/json" not in content_type:
                    print_warning(f"[{url}] HTTP请求返回HTML(WAF拦截), 需浏览器环境")
                    return None

                try:
                    data = resp.json()
                except (ValueError, Exception):
                    print_warning(f"[{url}] HTTP请求响应非JSON(WAF拦截), 需浏览器环境")
                    return None

                if isinstance(data, dict) and data.get("code") in (400016, 400017, 400018):
                    print_warning(f"[{url}] HTTP请求业务认证失败(code={data.get('code')})")
                    if attempt < self.max_retries and self._try_refresh_cookies():
                        continue
                    return {"error": f"雪球认证失败(code={data.get('code')})", "error_code": data.get("code")}

                return data

            except requests.exceptions.Timeout:
                print_error(f"[{url}] HTTP请求超时(第{attempt+1}次)")
            except requests.exceptions.RequestException as e:
                print_error(f"[{url}] HTTP请求异常: {e}")

            if attempt < self.max_retries:
                time.sleep(1)

        return None

    def _try_refresh_cookies(self) -> bool:
        """尝试刷新雪球Cookie

        通过 ensure_started 确保浏览器可用，然后从浏览器获取最新Cookie。
        """
        try:
            from driver.xueqiu_browser import XueqiuBrowserManager
            mgr = XueqiuBrowserManager.ensure_started(timeout=35)
            if mgr:
                self.cookies = mgr.get_cookies_str()
                return True
            new_cookies = mgr.refresh_cookies_sync() if mgr else None
            if new_cookies:
                self.cookies = new_cookies
                return True
        except Exception as e:
            print_error(f"雪球Cookie刷新失败: {e}")
        return False

    def _request_via_browser(self, url: str, params: Dict = None) -> Optional[Dict]:
        """通过浏览器环境请求雪球API

        当HTTP请求被WAF拦截时，通过 Playwright 浏览器环境发起请求，
        利用已预热的浏览器上下文携带完整Cookie和客户端指纹，突破反爬验证。

        使用 fetch_sync 替代 run_async，避免跨事件循环操作 Playwright
        导致卡死（run_async 会创建新线程+新事件循环，而 Playwright
        浏览器对象绑定在原始事件循环上）。
        """
        try:
            from driver.xueqiu_browser import XueqiuBrowserManager
            mgr = XueqiuBrowserManager.ensure_started(timeout=35)

            if not mgr.is_ready:
                if not self._try_refresh_cookies():
                    print_warning("[_request_via_browser] 浏览器启动失败，跳过浏览器请求")
                    return None

            if params:
                from urllib.parse import urlencode
                full_url = f"{url}?{urlencode(params)}"
            else:
                full_url = url

            return mgr.fetch_sync(full_url, owner="api_fallback", timeout=60)

        except Exception as e:
            print_error(f"[_request_via_browser] 浏览器回退失败: {e}")
            return None

    def search_user(self, kw: str, count: int = 5, page: int = 1) -> Dict:
        """搜索雪球用户

        通过用户名关键词搜索雪球用户，返回用户名、ID、头像、粉丝数等信息。
        走HTTP直连，不启用浏览器降级。
        """
        params = {"q": kw, "page": page, "size": count}
        raw = self._request_via_http(XUEQIU_SEARCH_URL, params)
        if raw is None:
            print_warning(f"[search_user] 雪球搜索用户无数据: kw={kw}, page={page}")
            return {"list": [], "total": 0, "page": page, "max_page": 1}
        if "error" in raw:
            print_warning(f"[search_user] 雪球搜索用户返回异常: kw={kw}, error={raw.get('error')}")
            return raw
        return XueqiuParser.parse_search_user(raw)

    def get_timeline(self, user_id: str, mp_id: str, page: int = 1, status_type: str = "0") -> Dict:
        """获取雪球用户单页动态时间线

        请求雪球用户个人主页的动态列表（帖子），HTTP失败时自动降级到浏览器。
        status_type: "0"=全部, "1"=原创, "2"=转发, 不传=全部
        """
        params = {"user_id": user_id, "page": page, "type": status_type}
        raw = self._request(XUEQIU_TIMELINE_URL, params)
        if raw is None:
            print_warning(f"[get_timeline] 雪球用户动态无数据: user_id={user_id}, page={page}")
            return {"statuses": [], "total": 0, "page": page, "max_page": 1}
        if "error" in raw:
            print_warning(f"[get_timeline] 雪球用户动态返回异常: user_id={user_id}, error={raw.get('error')}")
            return raw
        articles = XueqiuParser.parse_timeline(raw, mp_id)
        return {
            "statuses": articles,
            "total": raw.get("total", 0),
            "page": raw.get("page", page),
            "max_page": raw.get("maxPage", 1),
        }

    def get_timeline_pages(self, user_id: str, mp_id: str, max_page: int = 1, status_type: str = "0") -> list:
        """获取雪球用户多页动态时间线

        循环调用 get_timeline 翻页采集，直到无数据或达到 max_page，
        页面间有随机延迟。默认采集全部动态，60秒总超时自动中断。
        返回去重后的文章列表。
        """
        all_articles = []
        start_time = time.time()
        max_total_time = 60
        for page in range(1, max_page + 1):
            if time.time() - start_time > max_total_time:
                print_warning(f"[get_timeline_pages] 雪球采集总超时({max_total_time}s)，已获取{len(all_articles)}条，中断")
                break
            result = self.get_timeline(user_id, mp_id, page=page, status_type=status_type)
            articles = result.get("statuses", [])
            if not articles:
                break
            all_articles.extend(articles)
            if page < max_page:
                time.sleep(self.batch_delay + random.uniform(0.5, 1.5))
        return all_articles

    @staticmethod
    def _extract_snoman_status(html: str) -> Optional[Dict]:
        """从 HTML 中提取 window.SNOWMAN_STATUS JSON 数据

        雪球文章详情页会将完整文章数据内嵌在 <script> 标签中的 window.SNOWMAN_STATUS 变量里。
        """
        if not html:
            return None

        pattern = r'window\.SNOWMAN_STATUS\s*=\s*(\{.*?\});\s*</script>'
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            pattern2 = r'window\.SNOWMAN_STATUS\s*=\s*(\{.*?\});'
            match = re.search(pattern2, html, re.DOTALL)

        if not match:
            return None

        json_str = match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_content_from_html(html: str) -> Optional[str]:
        """从 HTML 中提取正文内容

        优先从 window.SNOWMAN_STATUS 中获取 text 字段，
        如果失败则尝试解析 .article__bd__detail 的 DOM。
        """
        status = XueqiuApi._extract_snoman_status(html)
        if status:
            text = status.get("text", "")
            if text:
                return text.strip()

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            detail = soup.select_one(".article__bd__detail")
            if detail:
                return str(detail)
        except Exception:
            pass

        return None

    def _request_article_page(self, url: str) -> Optional[str]:
        """获取雪球文章详情页 HTML 内容

        复用现有的 _request() 方法（HTTP + 浏览器降级），
        返回页面 HTML 字符串而非 JSON。
        """
        # _request() 返回的可能是 dict（JSON 响应）或包含 HTML 的 dict
        result = self._request(url)
        if not result:
            return None

        if isinstance(result, dict):
            # 如果 _request 通过浏览器返回了 JSON 格式的页面数据
            if "error" in result:
                return None
            # 尝试提取 text 字段（某些 API 直接返回文章数据）
            text = result.get("text", "")
            if text:
                return text.strip()
            # 可能是包装了 raw HTML
            raw = result.get("raw", "")
            if raw and isinstance(raw, str):
                return raw
            return None

        if isinstance(result, str):
            return result

        return None

    def get_article_content(self, url: str) -> Dict[str, Any]:
        """获取雪球文章详情页完整内容（统一入口）

        先通过 _request() 获取页面（HTTP + 浏览器降级），
        然后从 HTML 中提取 window.SNOWMAN_STATUS 的 text 字段作为正文。

        Returns:
            {
                "content": 正文 HTML 字符串,
                "title": 标题,
                "fetch_error": 错误信息（如有）,
            }
        """
        info = {
            "content": "",
            "title": "",
            "fetch_error": "",
        }

        html = self._request_article_page(url)
        if not html:
            info["fetch_error"] = "无法获取雪球文章详情页"
            return info

        content = self._extract_content_from_html(html)
        if content:
            info["content"] = content
            return info

        info["fetch_error"] = "无法从详情页提取正文内容"
        return info
