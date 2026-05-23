from __future__ import annotations

import base64
import threading
import time
from typing import Any, Optional

from core.config import cfg
from core.print import print_info, print_warning, print_success, print_error


# 全局 GitHub 客户端缓存，线程安全单例模式
_github_client = None
_github_repo = None
_client_lock = threading.Lock()

# 并发控制：限制同时推送的线程数，避免连接池耗尽和 409 冲突
_push_semaphore = threading.Semaphore(3)

# 路径级锁：防止多个线程同时更新同一个文件路径
_path_locks = {}
_path_locks_lock = threading.Lock()


def _get_path_lock(path: str) -> threading.Lock:
    """获取指定路径的排他锁

    每个文件路径对应一个独立的锁，避免多个线程同时推送同一文件导致 409 冲突。
    """
    with _path_locks_lock:
        if path not in _path_locks:
            _path_locks[path] = threading.Lock()
        return _path_locks[path]


def _get_github_repo():
    """获取或初始化 GitHub 仓库对象（线程安全懒加载）

    使用双检锁模式确保多线程环境下只初始化一次 PyGithub 客户端。
    配置来源：config.yaml / .env 中的 obsidian.github.* 配置项。

    Returns:
        github.Repository.Repository 对象

    Raises:
        ImportError: 未安装 PyGithub 库
        ValueError: 缺少必需的 token 或 repo 配置
    """
    global _github_client, _github_repo

    if _github_repo is not None:
        return _github_repo

    with _client_lock:
        if _github_repo is not None:
            return _github_repo

        try:
            from github import Github
        except ImportError:
            raise ImportError("PyGithub is required: pip install PyGithub")

        token = cfg.get("obsidian.github.token", "")
        repo_name = cfg.get("obsidian.github.repo", "")
        base_url = cfg.get("obsidian.github.base_url", "")

        if not token or not repo_name:
            raise ValueError("obsidian.github.token and obsidian.github.repo are required")

        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url

        _github_client = Github(token, **kwargs)
        _github_repo = _github_client.get_repo(repo_name)
        return _github_repo


def _reset_client():
    """重置 GitHub 客户端缓存

    当认证失败（401）时调用，下次请求会重新初始化客户端。
    """
    global _github_client, _github_repo
    with _client_lock:
        _github_client = None
        _github_repo = None


def push_to_github(
    path: str,
    content: str,
    message: str = "",
) -> bool:
    """将内容推送到 GitHub 仓库（幂等：内容未变时跳过）

    采用"先检查再推送"的策略，避免不必要的 API 调用和空 commit：
    1. get_contents 检查文件是否已存在
    2. 存在且内容相同 → 跳过（无变更）
    3. 存在且内容不同 → update_file 更新
    4. 不存在（404）→ create_file 创建
    5. 对 403/401/409 等异常分别处理，避免阻塞后续推送
    6. 通过信号量和路径锁控制并发，防止连接池耗尽和冲突

    Args:
        path: 仓库内的文件路径，如 "极客公园/2026-05/标题.md"
        content: 文件内容（Markdown 字符串）
        message: Git commit message，为空时自动生成

    Returns:
        推送是否成功（内容未变跳过也算成功）
    """
    if not content or not path:
        return False

    if not message:
        message = f"feat: add {path}"

    new_bytes = content.encode("utf-8")

    # 获取路径级排他锁，防止同一文件并发更新导致 409 冲突
    path_lock = _get_path_lock(path)
    acquired = path_lock.acquire(timeout=30)
    if not acquired:
        print_warning(f"[obsidian] => path lock timeout: {path}")
        return False

    try:
        # 限制全局并发数，避免连接池耗尽
        acquired_sem = _push_semaphore.acquire(timeout=30)
        if not acquired_sem:
            print_warning(f"[obsidian] => semaphore timeout: {path}")
            return False

        try:
            repo = _get_github_repo()
        except Exception as exc:
            print_warning(f"[obsidian] => github client init failed: {exc}")
            return False

        try:
            # Step 1: 检查文件是否已存在
            try:
                existing = repo.get_contents(path)
            except Exception as exc:
                error_str = str(exc)
                status = getattr(exc, "status", None)

                # 文件不存在（404），创建新文件
                if status == 404 or "404" in error_str:
                    try:
                        repo.create_file(
                            path=path,
                            message=message,
                            content=new_bytes,
                        )
                        print_success(f"[obsidian] => pushed {path} to github")
                        return True
                    except Exception as create_exc:
                        print_error(f"[obsidian] => create file failed: {create_exc}")
                        return False

                # 速率限制（403）
                if status == 403 or "rate limit" in error_str.lower():
                    print_warning(f"[obsidian] => github rate limit exceeded, skipping: {path}")
                    return False

                # 认证失败（401），重置客户端以便下次重试
                if status == 401 or "Bad credentials" in error_str:
                    print_warning("[obsidian] => github auth failed, resetting client")
                    _reset_client()
                    return False

                print_error(f"[obsidian] => get_contents failed: {exc}")
                return False

            # Step 2: 文件已存在，比对内容
            existing_bytes = base64.b64decode(existing.content)
            if existing_bytes == new_bytes:
                print_info(f"[obsidian] => {path} unchanged, skipped")
                return True

            # Step 3: 内容有变化，更新文件
            try:
                repo.update_file(
                    path=path,
                    message=f"update: {message}",
                    content=new_bytes,
                    sha=existing.sha,
                )
                print_info(f"[obsidian] => updated {path} on github")
                return True
            except Exception as update_exc:
                # 处理 409 冲突：获取最新 SHA 后重试一次
                update_error = str(update_exc)
                update_status = getattr(update_exc, "status", None)
                if update_status == 409 or "409" in update_error:
                    try:
                        time.sleep(0.5)
                        existing_retry = repo.get_contents(path)
                        existing_retry_bytes = base64.b64decode(existing_retry.content)
                        if existing_retry_bytes == new_bytes:
                            print_info(f"[obsidian] => {path} unchanged after 409, skipped")
                            return True
                        repo.update_file(
                            path=path,
                            message=f"update: {message}",
                            content=new_bytes,
                            sha=existing_retry.sha,
                        )
                        print_info(f"[obsidian] => updated {path} on github (retry after 409)")
                        return True
                    except Exception as retry_exc:
                        print_warning(f"[obsidian] => update retry failed: {retry_exc}")
                        return False
                print_warning(f"[obsidian] => update existing file failed: {update_exc}")
                return False
        finally:
            _push_semaphore.release()
    finally:
        path_lock.release()


def _get_tags_for_mp(mp_id: str) -> list[dict]:
    """查询公众号/雪球号所属的标签信息列表

    通过遍历 Tags 表，检查 mps_id JSON 字段中是否包含该 mp_id。

    Args:
        mp_id: 公众号或雪球号的 Feed ID

    Returns:
        标签信息字典列表（包含 name 和 intro），未找到时返回空列表
    """
    if not mp_id:
        return []

    try:
        from core.db import DB
        from core.models.tags import Tags
        import json

        session = DB.get_session()
        try:
            result = []
            tags = session.query(Tags).filter(Tags.status == 1).all()
            for tag in tags:
                if not tag.mps_id:
                    continue
                try:
                    mps_data = json.loads(tag.mps_id)
                    if isinstance(mps_data, list):
                        mps_ids = [str(mp.get("id", "")) for mp in mps_data]
                        if mp_id in mps_ids:
                            result.append({
                                "name": tag.name,
                                "intro": tag.intro or ""
                            })
                except (json.JSONDecodeError, TypeError):
                    continue
            return result
        finally:
            session.close()
    except Exception as exc:
        print_warning(f"[obsidian] => query tags for {mp_id} failed: {exc}")
        return []


def sync_article_to_obsidian(
    article: Any,
    mp_name: str = "",
) -> bool:
    """将单篇文章同步到 Obsidian（GitHub 仓库）

    完整流程：
    1. 读取文章原始 HTML（Article.content）
    2. 调用 obsidian.py 转换为带 frontmatter 的 Markdown
    3. 生成文件路径（公众号名/年-月/标题.md）
    4. 通过 GitHub API 推送到仓库

    Args:
        article: Article 模型对象或兼容的字典
        mp_name: 公众号名称，用于生成路径和标签

    Returns:
        同步是否成功
    """
    enabled = cfg.get("obsidian.enabled", False)
    if not enabled:
        return False

    raw_html = getattr(article, "content", "") or ""
    if not raw_html.strip():
        return False

    title = getattr(article, "title", "") or "untitled"
    url = getattr(article, "url", "") or ""
    description = getattr(article, "description", "") or ""
    publish_time = getattr(article, "publish_time", None)
    pic_url = getattr(article, "pic_url", "") or ""
    mp_id = getattr(article, "mp_id", "") or ""

    from tools.mdtools.obsidian import article_to_obsidian_markdown, build_obsidian_path

    tags = []
    tag_intro = ""

    # 查询该公众号/雪球号所属的标签，将标签名称和简介加入 frontmatter
    tag_infos = _get_tags_for_mp(mp_id)
    for tag_info in tag_infos:
        tag_name = tag_info.get("name", "")
        if tag_name and tag_name not in tags:
            tags.append(tag_name)
        # 取第一个标签的简介作为 tag_intro
        if not tag_intro:
            tag_intro = tag_info.get("intro", "")

    # 如果没有关联标签，则默认使用公众号/雪球号名称作为 tag
    if not tags and mp_name:
        tags.append(mp_name)

    md_content = article_to_obsidian_markdown(
        html_content=raw_html,
        title=title,
        url=url,
        mp_name=mp_name,
        description=description,
        publish_time=publish_time,
        tags=tags if tags else None,
        cover=pic_url,
        tag_intro=tag_intro,
    )

    if not md_content:
        return False

    file_path = build_obsidian_path(
        title=title,
        mp_name=mp_name,
        publish_time=publish_time,
    )

    return push_to_github(
        path=file_path,
        content=md_content,
        message=f"feat: {title}",
    )


def sync_article_to_obsidian_async(
    article: Any,
    mp_name: str = "",
) -> None:
    """异步将单篇文章同步到 Obsidian

    在后台线程中执行 sync_article_to_obsidian，避免阻塞主流程
    （如内容补抓队列、文章采集流程等）。

    Args:
        article: Article 模型对象或兼容的字典
        mp_name: 公众号名称
    """
    enabled = cfg.get("obsidian.enabled", False)
    if not enabled:
        return

    raw_html = getattr(article, "content", "") or ""
    if not raw_html.strip():
        return

    def _worker():
        try:
            sync_article_to_obsidian(article, mp_name=mp_name)
        except Exception as exc:
            print_warning(f"[obsidian] => async sync failed: {exc}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
