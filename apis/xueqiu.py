from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from core.auth import get_current_user_or_ak
from core.db import DB
from .base import success_response, error_response
from core.models.feed import Feed
from core.models.base import DATA_STATUS
from core.xueqiu.parser import XueqiuParser
from core.xueqiu.api import XueqiuApi
from core.xueqiu.cfg import xq_cfg
from core.config import cfg
from core.res import save_avatar_locally
from core.print import print_error, print_info, print_warning
from core.queue import TaskQueue
from datetime import datetime
import time
import json

router = APIRouter(prefix="/xueqiu", tags=["雪球订阅管理"])


@router.get("/search/user", summary="搜索雪球用户")
async def search_user(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    page: int = Query(1, ge=1),
    size: int = Query(5, ge=1, le=10),
    current_user: dict = Depends(get_current_user_or_ak),
):
    try:
        api = XueqiuApi()
        result = api.search_user(q, count=size, page=page)
        if "error" in result:
            print_warning(f"[/xueqiu/search/user] 搜索雪球用户无数据: {result.get('error', '搜索失败')}")
            return error_response(code=50001, message=result.get("error", "搜索失败"))
        return success_response(result)
    except Exception as e:
        print_error(f"[/xueqiu/search/user] 搜索雪球用户异常: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(code=50001, message=f"搜索雪球用户失败: {str(e)}"),
        )


@router.get("/statuses/user_timeline", summary="获取雪球用户动态")
async def get_user_timeline(
    user_id: str = Query(..., description="雪球用户ID"),
    page: int = Query(1, ge=1),
    type: str = Query("0", description="动态类型: 0=全部, 2=长文"),
    current_user: dict = Depends(get_current_user_or_ak),
):
    try:
        api = XueqiuApi()
        mp_id = f"XQ_{user_id}"
        result = api.get_timeline(user_id, mp_id, page=page, status_type=type)
        if "error" in result:
            print_warning(f"[/xueqiu/statuses/user_timeline] 获取雪球用户动态无数据: {result.get('error', '获取动态失败')}")
            return error_response(code=50001, message=result.get("error", "获取动态失败"))
        return success_response(result)
    except Exception as e:
        print_error(f"[/xueqiu/statuses/user_timeline] 获取雪球用户动态异常: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(code=50001, message=f"获取动态失败: {str(e)}"),
        )


@router.get("", summary="获取雪球订阅列表")
async def get_xueqiu_subscriptions(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user_or_ak),
):
    session = DB.get_session()
    try:
        query = session.query(Feed).filter(Feed.source_type == "xueqiu")
        total = query.count()
        feeds = query.order_by(Feed.created_at.desc()).limit(limit).offset(offset).all()
        feeds_list = []
        for f in feeds:
            ext = {}
            if f.extinfo:
                try:
                    ext = json.loads(f.extinfo)
                except (json.JSONDecodeError, TypeError):
                    pass
            feeds_list.append({
                "id": f.id,
                "mp_name": f.mp_name,
                "mp_cover": f.mp_cover,
                "mp_intro": f.mp_intro,
                "status": f.status,
                "source_type": f.source_type,
                "extinfo": ext,
                "sync_time": f.sync_time,
                "update_time": f.update_time,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            })
        return success_response({
            "list": feeds_list,
            "page": {"limit": limit, "offset": offset, "total": total},
            "total": total,
        })
    except Exception as e:
        print_error(f"[/xueqiu GET] 获取雪球订阅列表异常: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(code=50001, message="获取雪球订阅列表失败"),
        )
    finally:
        session.close()


@router.post("", summary="添加雪球用户订阅")
async def add_xueqiu_subscription(
    user_id: str = Body(..., min_length=1),
    screen_name: str = Body(..., min_length=1),
    avatar: str = Body(None),
    description: str = Body(None),
    current_user: dict = Depends(get_current_user_or_ak),
):
    session = DB.get_session()
    try:
        feed_id = f"XQ_{user_id}"
        now = datetime.now()

        local_avatar = ""
        if avatar:
            local_avatar = f"{save_avatar_locally(avatar)}"

        extinfo = json.dumps({
            "source": "xueqiu",
            "user_id": user_id,
            "screen_name": screen_name,
        }, ensure_ascii=False)

        existing = session.query(Feed).filter(Feed.id == feed_id).first()

        if existing:
            # 已存在：更新订阅信息（对齐微信 add_mp 逻辑）
            existing.mp_name = screen_name
            if local_avatar:
                existing.mp_cover = local_avatar
            if description is not None:
                existing.mp_intro = description
            existing.extinfo = extinfo
            existing.updated_at = now
            session.commit()

            return success_response({
                "id": existing.id,
                "mp_name": existing.mp_name,
                "mp_cover": existing.mp_cover,
                "mp_intro": existing.mp_intro,
                "status": existing.status,
                "source_type": existing.source_type,
                "faker_id": existing.faker_id,
                "created_at": existing.created_at.isoformat() if existing.created_at else None,
                "updated": True,
            })

        new_feed = Feed(
            id=feed_id,
            mp_name=screen_name,
            mp_cover=local_avatar,
            mp_intro=description or "",
            status=1,
            source_type="xueqiu",
            extinfo=extinfo,
            faker_id=user_id,
            created_at=now,
            updated_at=now,
            sync_time=0,
            update_time=0,
        )
        session.add(new_feed)
        session.commit()

        # 首次采集：使用 TaskQueue 入队，复用统一的 do_job
        from jobs.mps import do_job
        # 刷新 session 获取完整的 feed 对象
        session.refresh(new_feed)
        TaskQueue.add_task(
            do_job, new_feed, None, False,
            task_name=f"[雪球]{screen_name}"
        )

        return success_response({
            "id": feed_id,
            "mp_name": screen_name,
            "mp_cover": local_avatar,
            "mp_intro": description or "",
            "status": 1,
            "source_type": "xueqiu",
            "faker_id": user_id,
            "created_at": now.isoformat(),
            "updated": False,
        })
    except Exception as e:
        session.rollback()
        print_error(f"[/xueqiu POST] 添加雪球订阅异常: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(code=50001, message=f"添加雪球订阅失败: {str(e)}"),
        )
    finally:
        session.close()


@router.delete("/{feed_id}", summary="删除雪球订阅")
async def delete_xueqiu_subscription(
    feed_id: str,
    current_user: dict = Depends(get_current_user_or_ak),
):
    session = DB.get_session()
    try:
        feed = session.query(Feed).filter(Feed.id == feed_id, Feed.source_type == "xueqiu").first()
        if not feed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_response(code=40401, message="雪球订阅不存在"),
            )
        session.delete(feed)
        session.commit()
        return success_response({"message": "删除成功", "id": feed_id})
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        print_error(f"[/xueqiu/{{feed_id}} DELETE] 删除雪球订阅异常: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(code=50001, message="删除雪球订阅失败"),
        )
    finally:
        session.close()


@router.get("/update/{feed_id}", summary="手动更新雪球用户动态")
async def update_xueqiu_articles(
    feed_id: str,
    current_user: dict = Depends(get_current_user_or_ak),
):
    session = DB.get_session()
    try:
        feed = session.query(Feed).filter(Feed.id == feed_id, Feed.source_type == "xueqiu").first()
        if not feed:
            return error_response(code=40401, message="雪球订阅不存在")

        sync_interval = int(cfg.get("sync_interval", 60))
        if feed.update_time:
            time_span = int(time.time()) - int(feed.update_time)
            if time_span < sync_interval:
                return error_response(code=40402, message="请不要频繁更新", data={"time_span": time_span})

        # faker_id 在创建时已赋值为 user_id，无需从 extinfo 解析
        user_id = feed.faker_id

        # 手动更新：使用 TaskQueue 入队，复用统一的 do_job
        from jobs.mps import do_job
        TaskQueue.add_task(
            do_job, feed, None, False,
            task_name=f"[雪球更新]{feed.mp_name}"
        )
        return success_response({"message": "更新任务已启动", "feed_id": feed_id})
    except Exception as e:
        print_error(f"[/xueqiu/update/{{feed_id}}] 更新雪球动态异常: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(code=50001, message=f"更新失败: {str(e)}"),
        )
    finally:
        session.close()


@router.get("/health", summary="雪球服务健康检查")
async def xueqiu_health(
    current_user: dict = Depends(get_current_user_or_ak),
):
    try:
        from driver.xueqiu_browser import XueqiuBrowserManager
        mgr = XueqiuBrowserManager.get_instance()
        if mgr and mgr._browser:
            health = await mgr.health_check()
        else:
            health = {
                "browser_connected": False,
                "cookie_valid": bool(xq_cfg("cookies", "")),
            }
        return success_response(health)
    except Exception as e:
        return success_response({
            "browser_connected": False,
            "cookie_valid": False,
            "error": str(e),
        })
