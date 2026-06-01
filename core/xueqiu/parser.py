import re
import json
from typing import Dict, List, Optional, Any


class XueqiuParser:
    XUEQIU_BASE_URL = "https://xueqiu.com"

    @staticmethod
    def _build_avatar_url(photo_domain: str, profile_image_url: str) -> str:
        """拼接完整的头像URL

        雪球API返回的头像由两部分组成：
        - photo_domain: 如 "http://xavatar.imedao.com/" 或 "//xavatar.imedao.com/"
        - profile_image_url: 逗号分隔的多尺寸路径，如 "path.jpg,path!180x180.png,path!50x50.png,path!30x30.png"

        拼接规则: https + 去协议的photo_domain + 第二段路径(180x180)
        结果如: https://xavatar.imedao.com/community/20263/xxx.jpg!180x180.png
        """
        if not profile_image_url:
            return ""
        # 优先使用 photo_domain
        if photo_domain:
            # 统一为 https:// 开头
            domain = photo_domain
            if domain.startswith("http://"):
                domain = "https://" + domain[7:]
            elif domain.startswith("//"):
                domain = "https:" + domain
            elif not domain.startswith("https://"):
                domain = "https://" + domain
            # 去掉末尾斜杠避免双斜杠
            domain = domain.rstrip("/")
        else:
            domain = "https://xavatar.imedao.com"
        # 取逗号分隔的第二段（180x180尺寸），若无第二段则取第一段
        parts = [p.strip() for p in profile_image_url.split(",") if p.strip()]
        if not parts:
            return ""
        path = parts[1] if len(parts) >= 2 else parts[0]
        return f"{domain}/{path}"

    @staticmethod
    def parse_search_user(raw: dict) -> dict:
        users = raw.get("list", [])
        parsed = []
        for u in users:
            photo_domain = u.get("photo_domain", "")
            profile_image_url = u.get("profile_image_url", "")
            avatar_url = XueqiuParser._build_avatar_url(photo_domain, profile_image_url)
            parsed.append({
                "user_id": u.get("id"),
                "screen_name": u.get("screen_name", ""),
                "description": u.get("description", ""),
                "followers_count": u.get("followers_count", 0),
                "friends_count": u.get("friends_count", 0),
                "status_count": u.get("status_count", 0),
                "gender": u.get("gender", "n"),
                "province": u.get("province", ""),
                "city": u.get("city", ""),
                "verified": u.get("verified", False),
                "verified_type": u.get("verified_type", -1),
                "profile": f"{XueqiuParser.XUEQIU_BASE_URL}{u.get('profile', '')}" if u.get("profile") else "",
                "profile_image_url": avatar_url,
                "domain": u.get("domain", ""),
            })
        return {
            "list": parsed,
            "total": raw.get("count", 0),
            "page": raw.get("page", 1),
            "max_page": raw.get("maxPage", 1),
        }

    @staticmethod
    def parse_timeline(raw: dict, mp_id: str) -> List[dict]:
        statuses = raw.get("statuses", [])
        parsed = []
        for s in statuses:
            target = s.get("target", "")
            if target and not target.startswith("http"):
                target = f"{XueqiuParser.XUEQIU_BASE_URL}{target}"
            user_info = s.get("user", {})
            title = s.get("title", "")
            text = s.get("text", "")
            description = s.get("description", "")
            status_type = s.get("type", "0")

            # type=1（长文）和 type=3（专栏）在列表 API 中 text 为空，需要后续补抓详情页
            needs_content_fetch = status_type in ("1", "3")

            # 生成标题：优先使用 title 字段，否则从 text/description 提取
            content_text = text or description
            if not title and content_text:
                clean = re.sub(r'<[^>]+>', '', content_text).strip()
                title = clean[:50] + ("..." if len(clean) > 50 else "")

            created_at = s.get("created_at", 0)
            if created_at > 1e12:
                publish_time = int(created_at / 1000)
            else:
                publish_time = int(created_at)
            extinfo = {
                "source": "xueqiu",
                "user_id": str(user_info.get("id", "")),
                "screen_name": user_info.get("screen_name", ""),
                "retweet_count": s.get("retweet_count", 0),
                "reply_count": s.get("reply_count", 0),
                "like_count": s.get("like_count", 0),
                "fav_count": s.get("fav_count", 0),
                "status_type": status_type,
                "target": target,
                "stock_correlation": s.get("stockCorrelation", []),
                "source_device": s.get("source", ""),
            }
            retweeted = s.get("retweeted_status")
            if retweeted:
                extinfo["retweeted_status_id"] = retweeted.get("id", 0)
                extinfo["retweeted_user"] = retweeted.get("user", {}).get("screen_name", "")
            status_id = s.get("id")
            if not status_id:
                continue

            # 生成 description：优先使用 description 字段，否则从 text 提取纯文本前200字
            desc = description or (re.sub(r'<[^>]+>', '', text)[:200] if text else "")

            # 生成 content 和 content_html：
            # - 如果 text 有值（type=0 短动态），直接使用
            # - 如果 text 为空（type=1/3 长文/专栏），先用 description 填充，标记 has_content=0 等待补抓
            if text:
                content = text
                content_html = text
                has_content = 1
            elif description:
                content = description
                content_html = description
                has_content = 0 if needs_content_fetch else 1
            else:
                content = ""
                content_html = ""
                has_content = 0 if needs_content_fetch else 1

            parsed.append({
                "id": f"xq_{status_id}",
                "mp_id": mp_id,
                "title": title,
                "url": target,
                "pic_url": s.get("pic", ""),
                "description": desc,
                "content": content,
                "content_html": content_html,
                "publish_time": publish_time,
                "create_time": publish_time,
                "updated_at": publish_time,
                "status": 1,
                "has_content": has_content,
                "extinfo": json.dumps(extinfo, ensure_ascii=False),
            })
        return parsed

    @staticmethod
    def parse_user_to_feed(user: dict) -> dict:
        return {
            "id": f"XQ_{user.get('user_id', user.get('id', ''))}",
            "mp_name": user.get("screen_name", ""),
            "mp_cover": user.get("profile_image_url", ""),
            "mp_intro": user.get("description", ""),
            "faker_id": str(user.get("user_id", user.get("id", ""))),
            "source_type": "xueqiu",
            "extinfo": json.dumps({
                "source": "xueqiu",
                "user_id": str(user.get("user_id", user.get("id", ""))),
                "screen_name": user.get("screen_name", ""),
                "followers_count": user.get("followers_count", 0),
                "verified": user.get("verified", False),
                "domain": user.get("domain", ""),
            }, ensure_ascii=False),
        }
