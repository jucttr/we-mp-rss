from driver.store import BaseCookieStore


class WxCookieStore(BaseCookieStore):
    key_file = "data/key.lic"
    redis_key = "werss:key_store:cookies"

    def _filter_items(self, items):
        new_items = []
        for item in items:
            if item['name'] == "_clck":
                continue
            if item['name'] == "token":
                continue
            new_items.append(item)
        return new_items


Store = WxCookieStore()
