from datetime import datetime, timedelta
from core.models.article import Article
from .article import UpdateArticle, Update_Over
import core.db as db
from core.log import logger
from core.task import TaskScheduler
from core.models.feed import Feed
from core.models.base import DATA_STATUS
from core.models.message_task import MessageTask
from core.config import cfg, DEBUG
from core.print import print_info, print_success, print_error
from core.redis_client import clear_env_exception
from core.queue import TaskQueue
from core.db import DB

wx_db = db.Db(tag="任务调度")


# ── Gather 工厂：按 source_type 获取对应的采集器 ────────────────────


def _create_gather(source_type: str):
    """根据 source_type 创建采集器实例

    Args:
        source_type: "wechat" 或 "xueqiu"

    Returns:
        采集器实例（WxGather 或 XueqiuGather）
    """
    if source_type == "xueqiu":
        from core.xueqiu import XueqiuGather

        return XueqiuGather()
    else:
        from core.wx import WxGather

        return WxGather().Model()


# ── 任务执行追踪器（通用，微信/雪球共用） ──────────────────────────

import threading


class MessageTaskTracker:
    """消息任务执行追踪器（单例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tasks = {}
                    cls._instance._task_lock = threading.Lock()
        return cls._instance

    def start_task(self, task_id: str, total_feeds: int) -> None:
        """开始追踪一个消息任务"""
        with self._task_lock:
            self._tasks[task_id] = {
                "total": total_feeds,
                "completed": 0,
                "failed": 0,
                "start_time": datetime.now().isoformat(),
                "feed_results": [],
            }

    def record_feed_result(
        self,
        task_id: str,
        feed_name: str,
        success: bool,
        article_count: int = 0,
        error: str = None,
    ) -> None:
        """记录单个订阅源的执行结果"""
        with self._task_lock:
            if task_id not in self._tasks:
                return
            task_info = self._tasks[task_id]
            if success:
                task_info["completed"] += 1
            else:
                task_info["failed"] += 1
            task_info["feed_results"].append(
                {
                    "feed_name": feed_name,
                    "success": success,
                    "article_count": article_count,
                    "error": error,
                    "time": datetime.now().isoformat(),
                }
            )
            progress = task_info["completed"] + task_info["failed"]
            print_info(
                f"任务进度 [{task_id}]: {progress}/{task_info['total']}"
                f" (成功:{task_info['completed']}, 失败:{task_info['failed']})"
            )
            if progress >= task_info["total"]:
                self._finish_task(task_id)

    def _finish_task(self, task_id: str) -> None:
        """任务完成"""
        if task_id not in self._tasks:
            return
        t = self._tasks[task_id]
        print_success(f"\n{'=' * 50}")
        print_success(f"消息任务 [{task_id}] 执行完成!")
        print_success(f"总计: {t['total']} 个订阅源")
        print_success(f"成功: {t['completed']} 个")
        print_error(f"失败: {t['failed']} 个")
        print_success(f"{'=' * 50}\n")

    def get_task_status(self, task_id: str) -> dict:
        """获取任务状态"""
        with self._task_lock:
            return self._tasks.get(task_id, {})


tracker = MessageTaskTracker()


# ── 核心采集逻辑（统一入口，微信/雪球共用） ────────────────────────

interval = int(cfg.get("interval", 60))  # 每隔多少秒执行一次


def do_job(feed=None, task: MessageTask = None, isTest=False):
    """执行单个订阅源的采集任务（微信/雪球通用）

    流程：采集 → Webhook 通知 → 清异常 → 级联上报 → 进度追踪
    """
    source = feed.source_type if feed else "wechat"
    source_label = "雪球" if source == "xueqiu" else ""
    prefix = f"[{source_label}]" if source_label else ""

    # 初始化变量，确保在所有分支中都有定义
    count = 0
    all_count = 0
    mock_articles = []
    success = False
    error_msg = None

    try:
        if isTest:
            # 测试模式使用模拟数据
            mock_articles = [
                {
                    "id": f"test-{source}-article-001",
                    "mp_id": feed.id,
                    "title": (
                        f"{source_label}测试文章标题"
                        if source_label
                        else "测试文章标题"
                    ),
                    "pic_url": "https://via.placeholder.com/300x200",
                    "url": f"https://{'xueqiu.com' if source == 'xueqiu' else 'example.com'}/test-article",
                    "description": f"这是{source_label}测试文章的描述内容，用于测试webhook功能是否正常。",
                    "publish_time": (datetime.now() - timedelta(minutes=30)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "content": "<p>这是测试文章的正文内容。</p>",
                }
            ]
            count = 1
            success = True
        else:
            gather = _create_gather(source)
            try:
                # 统一参数：两个 Gather 的 get_Articles 签名已对齐
                gather.get_Articles(
                    faker_id=feed.faker_id,
                    CallBack=UpdateArticle,
                    Mps_id=feed.id,
                    Mps_title=feed.mp_name,
                    MaxPage=1,
                    Over_CallBack=Update_Over,
                    interval=interval,
                )
                success = True
            except Exception as e:
                print_error(f"获取文章失败 {prefix}[{feed.mp_name}]: {e}")
                error_msg = str(e)
            finally:
                count = gather.all_count() if gather else 0
                mock_articles = gather.articles if gather else []
                all_count += count

        # 执行 webhook 通知
        try:
            from jobs.webhook import MessageWebHook, web_hook

            if task:
                tms = MessageWebHook(task=task, feed=feed, articles=mock_articles)
                web_hook(tms, is_test=isTest)
                print_success(
                    f"任务({task.id}){prefix}[{feed.mp_name}]执行成功,{count}成功条数"
                )

                # 采集成功，清除该订阅源的环境异常记录
                if not isTest and success and count > 0:
                    try:
                        clear_env_exception(mp_id=feed.id)
                    except Exception as e:
                        print_error(f"清除环境异常记录失败: {e}")

        except Exception as e:
            print_error(f"Webhook执行失败 {prefix}[{feed.mp_name}]: {e}")
            if not error_msg:
                error_msg = f"Webhook: {str(e)}"

        # 级联节点：上报任务执行结果到父节点
        from jobs.cascade_sync import cascade_sync_service

        if not isTest and task and mock_articles:
            import asyncio

            try:
                result_data = [
                    {
                        "mp_id": feed.id,
                        "mp_name": feed.mp_name,
                        "article_count": len(mock_articles) if not isTest else 1,
                        "success_count": count if not isTest else 1,
                        "timestamp": datetime.now().isoformat(),
                    }
                ]
                # 异步上报，不阻塞主流程
                asyncio.create_task(
                    cascade_sync_service.report_task_result(task.id, result_data)
                )
            except Exception as e:
                print_error(f"上报任务结果失败: {str(e)}")

    except Exception as e:
        error_msg = str(e)
        print_error(f"任务执行异常 {prefix}[{feed.mp_name}]: {e}")
        raise  # 重新抛出，让队列的重试机制处理

    finally:
        # 记录执行结果到追踪器
        if task and not isTest:
            tracker.record_feed_result(
                task_id=task.id,
                feed_name=feed.mp_name,
                success=success and count > 0,
                article_count=count,
                error=error_msg,
            )


# ── Feed 获取（统一获取所有启用的 Feed） ───────────────────────────


def get_feeds(task: MessageTask = None):
    """获取 Feed 列表

    Args:
        task: 消息任务，如果不为 None 则从 task.mps_id 解析 Feed 列表

    Returns:
        所有启用的 Feed 列表（包含微信和雪球）
    """
    if task is not None:
        import json

        try:
            mps = json.loads(task.mps_id)
        except (json.JSONDecodeError, TypeError):
            mps = []

        ids = ",".join([item["id"] for item in mps])

        if ids:
            mps_list = wx_db.get_mps_list(ids)
            if mps_list:
                return mps_list

        # 回退：查全表（所有启用的 Feed）
        return wx_db.get_all_mps()

    # 无 task 时，查全表（所有启用的 Feed）
    return wx_db.get_all_mps()


# ── 任务调度 ────────────────────────────────────────────────────────


def add_job(feeds: list[Feed] = None, task: MessageTask = None, isTest=False):
    """将采集任务加入队列

    Args:
        feeds: 指定的 Feed 列表，如果为 None 则动态获取
        task: 消息任务
        isTest: 是否测试模式
    """
    if isTest:
        TaskQueue.clear_queue()

    # 动态获取 Feed 列表：如果 feeds 为 None 且 task 不为 None，则动态获取
    if feeds is None and task is not None:
        feeds = get_feeds(task)
    elif feeds is None:
        feeds = get_feeds()

    if not feeds:
        print_info("没有启用的订阅源")
        return

    # 初始化任务追踪
    if task and not isTest and feeds:
        tracker.start_task(task.id, len(feeds))

    for feed in feeds:
        # 任务名称前缀
        label = "[雪球]" if feed.source_type == "xueqiu" else ""
        task_display = f"{label}{feed.mp_name}" if label else feed.mp_name
        # 使用订阅源名称作为任务显示名称
        TaskQueue.add_task(do_job, feed, task, isTest, task_name=task_display)
        if isTest:
            print(f"测试任务，{feed.mp_name}，加入队列成功")
            break
        print(f"{feed.mp_name}，加入队列成功")
    print_success(TaskQueue.get_queue_info())


# ── 定时任务管理 ────────────────────────────────────────────────────

scheduler = TaskScheduler()


def reload_job():
    """重载所有定时任务"""
    print_success("重载任务")
    scheduler.clear_all_jobs()
    TaskQueue.clear_queue()
    start_job()


def run(job_id: str = None, isTest=False):
    """立即执行任务"""
    from .taskmsg import get_message_task

    tasks = get_message_task(job_id)
    if not tasks:
        print("没有任务")
        return None
    for task in tasks:
        from core.print import print_warning

        print_warning(f"{task.name} 添加到队列运行")
        add_job(task=task, isTest=isTest)
    return tasks


def start_job(job_id: str = None):
    """启动定时采集任务（统一调度所有启用的 Feed）"""
    from .taskmsg import get_message_task

    tasks = get_message_task(job_id)
    if not tasks:
        print("没有任务")
        return
    else:
        print_success(f"找到 {len(tasks)} 个任务，正在启动...")

    tag = "定时采集"
    for task in tasks:
        cron_exp = task.cron_exp
        if not cron_exp:
            print_error(f"任务[{task.id}]没有设置cron表达式")
            continue

        scheduler.add_cron_job(
            add_job,
            cron_expr=cron_exp,
            kwargs={"task": task},
            job_id=str(task.id),
            tag=tag,
        )
        print(f"已添加任务: {task.id}")
    scheduler.start()
    print_success("启动任务")


# ── 其他定时任务 ────────────────────────────────────────────────────


def start_fix_article():
    """开启自动同步未同步文章任务"""
    from jobs.fetch_no_article import start_sync_content

    start_sync_content()


def start_article_stats_refresh():
    """启动文章统计定时刷新任务"""
    from core.article_lax import refresh_article_info
    from core.config import cfg

    # 获取刷新间隔,默认5分钟
    refresh_interval = int(cfg.get("server.article_stats_refresh_interval", 3600))

    # 添加定时任务,每隔指定时间刷新一次文章统计
    scheduler.add_cron_job(
        refresh_article_info,
        cron_expr=f"*/{refresh_interval // 60} * * * *",  # 每 N 分钟执行一次
        job_id="article_stats_refresh",
        tag="文章统计刷新",
    )
    print_success(f"文章统计定时刷新任务已启动,间隔: {refresh_interval}秒")


if __name__ == "__main__":
    pass
