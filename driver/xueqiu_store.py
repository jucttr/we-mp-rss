from driver.store import BaseCookieStore
from core.print import print_info, print_warning, print_error, print_success


class XueqiuCookieStore(BaseCookieStore):
    key_file = "data/xueqiu.lic"
    redis_key = "werss:xueqiu:cookies"

    def _default_domain(self) -> str:
        return ".xueqiu.com"

    def has_auth_cookies(self, cookies=None):
        """检查是否存在雪球认证 Cookie

        雪球 WAF 三次握手完成后会设置以下核心认证 Cookie：
        - xq_a_token: 主认证令牌
        - xqat: 同 xq_a_token
        - xq_r_token: 刷新令牌

        Args:
            cookies: Cookie 列表，为 None 时从存储加载

        Returns:
            bool: 是否存在核心认证 Cookie
        """
        if cookies is None:
            cookies = self.load()

        auth_names = {"xq_a_token", "xqat", "xq_r_token"}
        cookie_names = {c.get("name", "") for c in cookies if isinstance(c, dict)}
        return bool(auth_names & cookie_names)

    def _filter_items(self, items):
        """确保 Cookie 列表符合 Playwright 的 add_cookies 格式

        Playwright 要求每个 Cookie 至少包含 name, value, domain, path 字段
        """
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if "name" not in item or "value" not in item:
                continue
            if "domain" not in item:
                item["domain"] = ".xueqiu.com"
            if "path" not in item:
                item["path"] = "/"
            result.append(item)
        return result


XueqiuStore = XueqiuCookieStore()
