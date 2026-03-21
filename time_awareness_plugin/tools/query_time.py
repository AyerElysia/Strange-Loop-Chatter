"""Query Time Tool - 查询时间工具。

让爱莉希雅能够主动查询当前时间。
"""

from datetime import datetime
from typing import Annotated

from src.core.components.base.tool import BaseTool


class QueryTimeTool(BaseTool):
    """查询时间工具。

    当爱莉希雅需要知道当前时间时调用此工具。

    Attributes:
        tool_name: 工具名称
        tool_description: 工具功能描述
        chatter_allow: 支持使用该工具的 Chatter 列表
    """

    tool_name = "query_time"
    tool_description = "查询当前时间。当你需要知道现在是几点、什么时辰时调用此工具。"

    chatter_allow: list[str] = ["default_chatter", "kokoro_flow_chatter"]

    async def go_activate(self) -> bool:
        """检查工具是否应该激活。

        当配置文件中 enabled = false 时，工具不会被激活。

        Returns:
            bool: 是否激活
        """
        config = getattr(self.plugin, "config", None)
        if config is None:
            return True
        return getattr(config.settings, "enabled", True)

    async def execute(
        self
    ) -> tuple[bool, dict]:
        """执行时间查询。

        返回当前时间的中式描述。

        Returns:
            tuple[bool, dict]: (成功标志，结果字典)
                - 成功标志恒为 True
                - 结果字典包含时间描述
        """
        time_str = build_chinese_datetime(datetime.now())
        return True, {
            "current_time": time_str,
            "reminder": "时间已查询。现在你可以根据当前时间给出合适的问候或回应了。"
        }


def build_chinese_datetime(dt: datetime) -> str:
    """生成中式时间描述。

    Args:
        dt: 当前时间

    Returns:
        中式时间描述字符串
    """
    # 时辰：子丑寅卯辰巳午未申酉戌亥
    hour = dt.hour
    shichen_map = {
        (23, 1): ("子时", "深夜"),
        (1, 3): ("丑时", "凌晨"),
        (3, 5): ("寅时", "黎明"),
        (5, 7): ("卯时", "清晨"),
        (7, 9): ("辰时", "上午"),
        (9, 11): ("巳时", "上午"),
        (11, 13): ("午时", "中午"),
        (13, 15): ("未时", "下午"),
        (15, 17): ("申时", "下午"),
        (17, 19): ("酉时", "傍晚"),
        (19, 21): ("戌时", "晚上"),
        (21, 23): ("亥时", "深夜"),
    }

    shichen_name = "未知"
    shichen_period = "未知"
    for (start, end), (name, period) in shichen_map.items():
        if start == 23:
            if hour >= 23 or hour < 1:
                shichen_name = name
                shichen_period = period
        elif start <= hour < end:
            shichen_name = name
            shichen_period = period
            break

    # 刻：一小时 4 刻，每刻 15 分钟
    ke = (dt.minute // 15) + 1

    # 生肖
    zodiac_map = {
        0: "猴", 1: "鸡", 2: "狗", 3: "猪", 4: "鼠", 5: "牛",
        6: "虎", 7: "兔", 8: "龙", 9: "蛇", 10: "马", 11: "羊",
    }
    zodiac = zodiac_map[dt.year % 12]

    # 星期
    weekday_map = {
        0: "周一", 1: "周二", 2: "周三", 3: "周四",
        4: "周五", 5: "周六", 6: "周日",
    }
    weekday = weekday_map[dt.weekday()]

    return (
        f"{dt.year}年{dt.month}月{dt.day}日 ({weekday}) "
        f"{shichen_name} ({shichen_period}，{dt.hour}点{dt.minute}分，{ke}刻)，"
        f"{zodiac}年"
    )
