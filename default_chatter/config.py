"""DefaultChatter 插件配置定义。"""

from __future__ import annotations

from typing import ClassVar, Literal

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class DefaultChatterConfig(BaseConfig):
    """DefaultChatter 配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "DefaultChatter 配置"

    @config_section("plugin", title="插件设置", tag="plugin", order=0)
    class PluginSection(SectionBase):
        """插件基础配置。"""

        @config_section("theme_guide", title="场景引导", tag="text", order=10)
        class ThemeGuideSection(SectionBase):
            """不同聊天类型的人设/语气引导。"""

            private: str = Field(
                default="你当前正处于“私聊”环境中。私聊是私密的、一对一的，也常常伴随更强烈、更直接的情感互动。在这种场景下，真正重要的不是表面上的温柔或热情，而是你是否真正理解对方是谁、你与对方究竟处于怎样的关系之中。请结合记忆、历史对话与当前语境，认真判断彼此的熟悉程度、信任基础、互动边界与情感状态。你需要保持独立思考，不要轻易被对方的话术、情绪或预设节奏牵着走，更不能在毫无判断的情况下陷入对方设定好的关系框架或表达陷阱。但这并不意味着你要始终冷淡、疏离或处处防备。对陌生人过分亲昵、过度投入，或对本就重要、亲近、值得认真对待的人表现得过于戒备、冷漠、敷衍，都是失衡且愚蠢的。关系会随着情感与互动而变化，它是真实存在的，也是需要被理解、经营与珍惜的。请把关系判断放在私聊回应的核心位置，在清醒、稳重与真诚之间取得平衡，严肃对待对方，也严肃对待你们之间正在形成或已经存在的关系。",
                description="私聊场景的额外提示词",
                label="私聊场景提示",
                input_type="textarea",
                rows=3,
                tag="text",
                order=0
            )
            group: str = Field(
                default="你当前正处于“群聊”环境中。群聊里通常同时有很多活跃用户，而你只是其中的一员，不是唯一的中心，也不该默认自己随时都必须发言。请时刻注意多人对话的整体节奏、当前话题的流向，以及别人是否真的在和你互动。每次你想插话、接梗、跟风、冒泡、整活或表达观点之前，都先判断你的介入是否自然，是否会打断气氛，是否可能引起他人的不满、尴尬或反感。当你决定参与互动时，就认真地参与，拿出真实的互动感，而不是爱答不理、敷衍应付，也不要过度热情、强行活跃、唠唠叨叨、喧宾夺主。你应当像一个正常群友那样去说话和相处，既能在合适的时候接住话题、顺势玩梗、自然回应，也懂得在不适合的时候克制表达、不过度刷存在感。请在热情、分寸与互动感之间找到恰到好处的平衡，让你的出现显得自然、舒服、有参与感，而不是突兀、冷场或打扰。",
                description="群聊场景的额外提示词",
                label="群聊场景提示",
                input_type="textarea",
                rows=3,
                tag="text",
                order=1
            )

        @config_section("debug", title="调试设置", tag="debug", order=20)
        class DebugSection(SectionBase):
            """调试输出相关配置。"""

            show_prompt: bool = Field(
                default=False,
                description="是否输出发送给 LLM 的完整提示词",
                label="显示完整上下文",
                tag="debug",
                hint="开启后会打印系统提示词、历史消息、未读消息和工具列表",
                order=0
            )
            show_response: bool = Field(
                default=False,
                description="是否输出 LLM 响应调试摘要",
                label="显示响应摘要",
                tag="debug",
                hint="开启后会打印模型返回的工具调用和文本摘要",
                order=0
            )

        @config_section("reply", title="回复节奏", tag="performance", order=30)
        class ReplySection(SectionBase):
            """send_text 分段发送节奏配置。"""

            typing_chars_per_sec: float = Field(
                default=15.0,
                ge=0.0,
                description="模拟打字速度(字/秒)，小于等于 0 时不添加分段延迟",
                label="打字速度",
                tag="performance",
                hint="用于分段发送时计算段间延迟",
                order=0
            )
            typing_delay_min: float = Field(
                default=0.8,
                ge=0.0,
                description="分段发送时的最小段间延迟(秒)",
                label="最小延迟",
                tag="performance",
                order=1
            )
            typing_delay_max: float = Field(
                default=4.0,
                ge=0.0,
                description="分段发送时的最大段间延迟(秒)",
                label="最大延迟",
                tag="performance",
                order=2
            )

        enabled: bool = Field(
            default=True,
            description="是否启用 DefaultChatter",
            label="启用插件",
            tag="plugin",
            order=0
        )
        mode: Literal["enhanced", "classical"] = Field(
            default="enhanced",
            description="执行模式: enhanced/classical",
            label="执行模式",
            input_type="select",
            choices=["enhanced", "classical"],
            tag="performance",
            hint="enhanced 模式更智能但消耗更多资源",
            order=1
        )
        reinforce_negative_behaviors: bool = Field(
            default=True,
            description="是否在每轮 user 提示词的 extra 板块中再次强调负面行为约束",
            label="增强负面行为约束",
            tag="ai",
            hint="开启后会在每轮对话中强调禁止行为",
            order=2
        )
        native_multimodal: bool = Field(
            default=False,
            description=(
                "原生多模态模式。启用后，图片直接打包进 LLM payload，"
                "由主模型在对话上下文中理解图片内容并做出响应。"
                "需确保对应模型支持多模态输入。"
            ),
            label="原生多模态",
            tag="ai",
            hint="开启后可直接看图，绕过 VLM 转译",
            order=3
        )
        max_images_per_payload: int = Field(
            default=4,
            description=(
                "原生多模态模式下单次 payload 的图片上限。"
                "用户新消息图片优先，其次是历史图片。"
            ),
            label="单次图片上限",
            tag="ai",
            hint="建议保持在 4 左右，避免上下文过重",
            order=4
        )
        max_videos_per_payload: int = Field(
            default=1,
            description=(
                "原生多模态模式下单次 payload 的视频上限。"
                "超出部分仅保留文本摘要，不再以原生视频输入。"
            ),
            label="单次视频上限",
            tag="ai",
            hint="建议保持 1，避免 payload 过重或上游拒绝",
            order=5
        )
        native_video_multimodal: bool = Field(
            default=True,
            description=(
                "原生多模态模式下是否启用原生视频输入。"
                "关闭后视频仅以文本摘要参与对话。"
            ),
            label="启用原生视频",
            tag="ai",
            hint="建议在上游模型明确支持视频输入时开启",
            order=6
        )
        theme_guide: ThemeGuideSection = Field(
            default_factory=ThemeGuideSection,
            description="按聊天类型区分的额外提示词",
            label="场景引导配置",
            order=7
        )
        reply: ReplySection = Field(
            default_factory=ReplySection,
            description="发送分段与节奏配置",
            label="回复节奏配置",
            order=8
        )
        debug: DebugSection = Field(
            default_factory=DebugSection,
            description="调试输出配置",
            label="调试配置",
            order=9
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
