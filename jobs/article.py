
from core.print import print_warning
import core.wx as wx 
import core.db as db
from core.config import DEBUG,cfg
from core.models.article import Article

DB=db.Db(tag="文章采集API")

def UpdateArticle(art:dict,check_exist=True):
    mps_count=0
    if DEBUG:
        # DB.delete_article(art)
        pass
    if  DB.add_article(art,check_exist=check_exist):
        mps_count=mps_count+1
        _try_obsidian_sync_for_new_article(art)
        return True
    return False

def _try_obsidian_sync_for_new_article(art: dict) -> None:
    if not cfg.get("obsidian.enabled", False):
        return
    content = art.get("content", "") or ""
    if not content.strip():
        return
    try:
        from tools.mdtools.github_sync import sync_article_to_obsidian_async
        from core.models.feed import Feed
        mp_id = art.get("mp_id", "") or ""
        mp_name = ""
        if mp_id:
            try:
                session = DB.get_session()
                feed = session.query(Feed).filter(Feed.id == mp_id).first()
                if feed:
                    mp_name = getattr(feed, "mp_name", "") or ""
            except Exception:
                pass
        article_obj = Article(**art)
        sync_article_to_obsidian_async(article_obj, mp_name=mp_name)
    except Exception:
        pass

def Update_Over(data=None):
    print("更新完成")
    pass