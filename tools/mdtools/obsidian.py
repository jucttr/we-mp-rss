from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from tools.mdtools.html2doc import html_to_markdown


def _sanitize_filename(title: str) -> str:
    """清理标题中的非法文件名字符，生成安全的文件名

    将文件系统不允许的字符替换为连字符，并限制长度不超过100个字符。

    Args:
        title: 文章原始标题

    Returns:
        清理后的安全文件名
    """
    if not title:
        return "untitled"
    sanitized = re.sub(r'[\\/:*?"<>|]', "-", title)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = sanitized[:100]
    return sanitized or "untitled"


def _format_publish_time(publish_time: Any) -> Optional[str]:
    """将 Unix 时间戳格式化为 ISO 8601 格式的日期字符串

    用于生成 Obsidian frontmatter 中的 date 字段。

    Args:
        publish_time: Unix 时间戳（秒级）

    Returns:
        ISO 8601 格式字符串，如 2026-05-16T08:30:00+0000；解析失败返回 None
    """
    if not publish_time:
        return None
    try:
        ts = int(publish_time)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    except (ValueError, TypeError, OSError):
        return None


def _build_frontmatter(
    title: str,
    url: str = "",
    mp_name: str = "",
    author: str = "",
    description: str = "",
    publish_time: Any = None,
    tags: Optional[list[str]] = None,
    cover: str = "",
    tag_intro: str = "",
) -> str:
    """构建 Obsidian YAML frontmatter

    根据文章元数据生成标准的 YAML frontmatter 块，Obsidian 会自动识别并索引这些字段。

    Args:
        title: 文章标题
        url: 文章原始链接
        mp_name: 公众号名称，会写入 source 字段并作为 tag
        author: 文章作者
        description: 文章摘要，超过300字符自动截断
        publish_time: 发布时间（Unix 时间戳）
        tags: 额外标签列表
        cover: 封面图片 URL
        tag_intro: 标签简介，用于描述文章所属分类

    Returns:
        YAML frontmatter 字符串，包含 --- 包围的元数据块
    """
    lines = ["---"]

    if title:
        escaped = title.replace('"', '\\"')
        lines.append(f'title: "{escaped}"')

    dt_str = _format_publish_time(publish_time)
    if dt_str:
        lines.append(f"date: {dt_str}")

    if tags:
        lines.append("tags:")
        for tag in tags:
            tag_clean = tag.replace('"', "").strip()
            if tag_clean:
                lines.append(f"  - {tag_clean}")

    if tag_intro:
        intro_clean = tag_intro.replace('"', '\\"').replace("\n", " ").strip()
        lines.append(f'tag_intro: "{intro_clean}"')

    if mp_name:
        mp_clean = mp_name.replace('"', '\\"')
        lines.append(f'source: "{mp_clean}"')

    if author:
        author_clean = author.replace('"', '\\"')
        lines.append(f'author: "{author_clean}"')

    if url:
        lines.append(f"url: {url}")

    if description:
        desc = description.replace('"', '\\"').replace("\n", " ").strip()
        if len(desc) > 300:
            desc = desc[:300] + "..."
        lines.append(f'description: "{desc}"')

    if cover:
        lines.append(f"cover: \"{cover}\"")

    lines.append("---")
    return "\n".join(lines)


def article_to_obsidian_markdown(
    html_content: str,
    title: str = "",
    url: str = "",
    mp_name: str = "",
    author: str = "",
    description: str = "",
    publish_time: Any = None,
    tags: Optional[list[str]] = None,
    cover: str = "",
    tag_intro: str = "",
    add_title_heading: bool = True,
) -> str:
    """将文章 HTML 内容转换为 Obsidian 兼容的 Markdown

    转换流程：HTML -> markdownify -> 添加 YAML frontmatter -> 可选标题 -> 最终 Markdown

    Args:
        html_content: 文章原始 HTML 内容（来自 Article.content 字段）
        title: 文章标题
        url: 文章原始链接
        mp_name: 公众号名称
        author: 文章作者
        description: 文章摘要
        publish_time: 发布时间（Unix 时间戳）
        tags: 额外标签列表
        cover: 封面图片 URL
        tag_intro: 标签简介，用于描述文章所属分类
        add_title_heading: 是否在正文开头添加一级标题

    Returns:
        完整的 Obsidian 兼容 Markdown 字符串，包含 frontmatter 和正文
    """
    if not html_content:
        return ""

    md_body = html_to_markdown(html_content)
    if not md_body:
        return ""

    frontmatter = _build_frontmatter(
        title=title,
        url=url,
        mp_name=mp_name,
        author=author,
        description=description,
        publish_time=publish_time,
        tags=tags,
        cover=cover,
        tag_intro=tag_intro,
    )

    parts = [frontmatter]

    if add_title_heading and title:
        parts.append(f"\n# {title}\n")

    parts.append(f"\n{md_body}")

    return "\n".join(parts)


def build_obsidian_path(
    title: str,
    mp_name: str = "",
    publish_time: Any = None,
    source_type: str = "wechat",
) -> str:
    """构建 Obsidian 仓库中的文件路径

    路径结构：<平台>/公众号名/年-月/日_标题.md
    例如：
      微信/极客公园/2026-05/01_AI又进化了.md
      雪球/雪球号/2026-05/01_xxx.md

    Args:
        title: 文章标题，会被清理为安全文件名
        mp_name: 公众号或雪球号名称，作为二级目录
        publish_time: 发布时间（Unix 时间戳），用于生成年月子目录和文件名日期前缀
        source_type: 来源类型，wechat 或 xueqiu，决定顶层目录前缀

    Returns:
        相对文件路径字符串
    """
    safe_title = _sanitize_filename(title)

    if publish_time:
        try:
            ts = int(publish_time)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            date_prefix = dt.strftime("%Y-%m")
            day_prefix = dt.strftime("%d")
        except (ValueError, TypeError, OSError):
            date_prefix = "unknown-date"
            day_prefix = "00"
    else:
        date_prefix = "unknown-date"
        day_prefix = "00"

    filename = f"{day_prefix}_{safe_title}.md"

    platform_prefix = "雪球" if source_type == "xueqiu" else "微信"

    if mp_name:
        safe_mp = _sanitize_filename(mp_name)
        return f"{platform_prefix}/{safe_mp}/{date_prefix}/{filename}"

    return f"{platform_prefix}/{date_prefix}/{filename}"
