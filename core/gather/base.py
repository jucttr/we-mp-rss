import time
from core.print import print_error, print_info, print_warning, print_success
from core.rss import RSS


class BaseGather:
    """采集器抽象基类

    封装所有平台采集器共享的核心逻辑：
    - 文章去重（aids 列表 + HasGathered/RecordAid）
    - 文章收集（articles 列表 + FillBack）
    - 采集生命周期（Start → get_Articles → Over）
    - RSS 缓存清理
    - Feed 同步时间更新

    子类只需实现 get_Articles() 方法即可接入新平台。
    """

    def __init__(self, is_add: bool = False):
        self.articles = []
        self.aids = []
        self.is_add = is_add
        self.start_time = None

    def all_count(self):
        return len(self.articles) if self.articles else 0

    def RecordAid(self, aid: str):
        self.aids.append(aid)

    def HasGathered(self, aid: str) -> bool:
        if aid in self.aids:
            return True
        self.RecordAid(aid)
        return False

    def FillBack(self, CallBack=None, data=None, Ext_Data=None):
        if CallBack is not None and data is not None:
            if CallBack(data):
                data["ext"] = Ext_Data
                self.articles.append(data)

    def Start(self, mp_id=None):
        self.articles = []
        self.aids = []
        self.start_time = time.time()
        self._update_feed_sync_time(mp_id)

    def Over(self, CallBack=None):
        end_time = time.time()
        if self.start_time:
            elapsed = end_time - self.start_time
            if elapsed < 60:
                print_info(f"{self._source_label()}采集完成, 耗时: {elapsed:.2f}秒, 共{len(self.articles)}条")
            else:
                print_info(f"{self._source_label()}采集完成, 耗时: {int(elapsed // 60)}分{elapsed % 60:.2f}秒, 共{len(self.articles)}条")
        if self.articles:
            rss = RSS()
            mp_id = ""
            try:
                mp_id = self.articles[0].get("mp_id", "")
            except (IndexError, KeyError):
                pass
            rss.clear_cache(mp_id=mp_id)
        if CallBack is not None:
            CallBack(self.articles)

    def Error(self, error: str, code=None):
        self.Over()
        print_error(error)

    def get_Articles(self, faker_id: str = None, Mps_id: str = None,
                     Mps_title: str = "", CallBack=None,
                     start_page: int = 1, MaxPage: int = 1,
                     interval: int = 10, Gather_Content: bool = False,
                     Item_Over_CallBack=None, Over_CallBack=None):
        raise NotImplementedError("子类必须实现 get_Articles 方法")

    def _source_label(self) -> str:
        return getattr(self, '_label', '')

    def _update_feed_sync_time(self, mp_id: str):
        if not mp_id:
            return
        try:
            from datetime import datetime
            from core.db import DB
            from core.models.feed import Feed
            session = DB.get_session()
            feed = session.query(Feed).filter(Feed.id == mp_id).first()
            if feed:
                feed.sync_time = int(time.time())
                feed.updated_at = datetime.now()
                session.commit()
        except Exception as e:
            print_error(f"更新订阅源同步时间失败: {e}")
