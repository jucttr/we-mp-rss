from core.gather.base import BaseGather
from core.xueqiu.api import XueqiuApi


class XueqiuGather(BaseGather):

    def __init__(self, is_add: bool = False):
        super().__init__(is_add=is_add)
        self._label = "雪球"
        self.api = XueqiuApi()

    def get_Articles(self, faker_id: str = None, Mps_id: str = None,
                     Mps_title: str = "", CallBack=None,
                     start_page: int = 1, MaxPage: int = 1,
                     interval: int = 10, Gather_Content: bool = False,
                     Item_Over_CallBack=None, Over_CallBack=None):
        self.Start(mp_id=Mps_id)
        try:
            articles = self.api.get_timeline_pages(
                user_id=faker_id,
                mp_id=Mps_id,
                max_page=MaxPage,
            )
            for art in articles:
                if self.HasGathered(art["id"]):
                    continue
                self.FillBack(CallBack=CallBack, data=art)
        except Exception as e:
            from core.print import print_error
            print_error(f"雪球采集异常: {e}")
        self.Over(CallBack=Over_CallBack)
