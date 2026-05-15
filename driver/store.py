import json
from core.file import FileCrypto
from core.config import cfg
from core.redis_client import redis_client
from core.print import print_warning


class BaseCookieStore:
    """Cookie 持久化存储基类

    封装 Redis 优先 + 加密文件回退的双层存储模式。
    子类只需定义 key_file、redis_key 和过滤逻辑即可。
    """

    key_file: str = ""
    redis_key: str = ""

    def __init__(self):
        self.store = FileCrypto(cfg.get("safe.lic_key", "store.csol.store.werss"))

    def save(self, cookies):
        items = self._normalize_cookies(cookies)
        text = json.dumps(items)

        if redis_client.is_connected:
            try:
                redis_client._client.set(self.redis_key, text)
            except Exception as e:
                print_warning(f"Cookie保存到Redis失败: {e}")

        try:
            self.store.encrypt_to_file(self.key_file, text.encode("utf-8"))
        except Exception as e:
            print_warning(f"Cookie保存到文件失败: {e}")

    def load(self):
        if redis_client.is_connected:
            try:
                data = redis_client._client.get(self.redis_key)
                if data:
                    items = json.loads(data)
                    if isinstance(items, list) and len(items) > 0:
                        return self._filter_items(items)
            except Exception as e:
                print_warning(f"Cookie从Redis加载失败: {e}")

        try:
            text = self.store.decrypt_from_file(self.key_file).decode("utf-8")
            items = json.loads(text)
            if isinstance(items, list) and len(items) > 0:
                return self._filter_items(items)
        except FileNotFoundError:
            pass
        except Exception as e:
            print_warning(f"Cookie从文件加载失败: {e}")

        return []

    def clear(self):
        if redis_client.is_connected:
            try:
                redis_client._client.delete(self.redis_key)
            except Exception:
                pass
        try:
            import os
            if os.path.exists(self.key_file):
                os.remove(self.key_file)
        except Exception:
            pass

    def _normalize_cookies(self, cookies):
        items = []
        if isinstance(cookies, str):
            for item in cookies.split(";"):
                item = item.strip()
                if "=" in item:
                    name, value = item.split("=", 1)
                    items.append({
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": self._default_domain(),
                        "path": "/",
                    })
        elif isinstance(cookies, list):
            items = cookies
        return items

    def _filter_items(self, items):
        return items

    def _default_domain(self) -> str:
        return ""
