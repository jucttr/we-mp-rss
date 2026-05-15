from core.config import cfg


def xq_cfg(key, default=None):
    return cfg.get(f"xueqiu.{key}", default)
