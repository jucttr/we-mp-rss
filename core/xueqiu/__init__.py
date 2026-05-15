from .base import XueqiuGather
from .api import XueqiuApi


def search_user(kw, count=5, page=1):
    api = XueqiuApi()
    return api.search_user(kw, count=count, page=page)
