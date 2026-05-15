import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from core.print import print_error


def run_async(coro, timeout: float = 60):
    """在同步上下文中安全运行异步协程

    自动处理事件循环的获取和创建，避免 "cannot run the event loop while another loop is running" 错误。
    使用 ThreadPoolExecutor 强制超时，防止协程死锁导致永久阻塞。

    Args:
        coro: 异步协程对象
        timeout: 最大等待时间（秒），超时返回 None

    Returns:
        协程的返回值，超时或异常返回 None
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_in_new_loop, coro)
                try:
                    return future.result(timeout=timeout)
                except FuturesTimeoutError:
                    print_error(f"异步任务执行超时({timeout}s)")
                    return None
                except Exception as e:
                    print_error(f"异步任务执行异常: {e}")
                    return None
        else:
            return loop.run_until_complete(
                asyncio.wait_for(coro, timeout=timeout)
            )
    except RuntimeError:
        return _run_in_new_loop(coro, timeout=timeout)
    except asyncio.TimeoutError:
        print_error(f"异步任务执行超时({timeout}s)")
        return None
    except Exception as e:
        print_error(f"异步任务执行异常: {e}")
        return None


def _run_in_new_loop(coro, timeout: float = None):
    """在新事件循环中运行协程"""
    loop = asyncio.new_event_loop()
    try:
        if timeout:
            return loop.run_until_complete(
                asyncio.wait_for(coro, timeout=timeout)
            )
        return loop.run_until_complete(coro)
    except asyncio.TimeoutError:
        print_error(f"异步任务执行超时({timeout}s)")
        return None
    except Exception as e:
        print_error(f"异步任务执行异常: {e}")
        return None
    finally:
        loop.close()
