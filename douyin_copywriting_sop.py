"""Runtime copywriting guide distilled from the South Africa logistics Douyin team SOP.

The source document is a style reference, not permission to repeat any of its
sample lines.  These instructions are deliberately compact so every model path
uses the same voice while the evidence gates remain the source of truth.
"""
from __future__ import annotations


SOP_ID = "south-africa-logistics-douyin-copy-style"
SOP_VERSION = "v4"


def prompt_for_video_planner() -> str:
    """Return copy rules for the fact-bounded dual-library video planner."""
    return (
        "【南非物流抖音文案 SOP｜风格参考，不替代事实门禁】"
        "语气像一位一线物流同行在和卖家说话：口语、务实、自然；以“我们”拉近距离，"
        "只有确实是个人观察时才用“我”。"
        "优先从卖家正在遇到的现场或一个具体问题切入，每句尽量短，按‘发生什么—为什么影响物流—要核对什么—画面里能看到什么’推进，"
        "热点型视频必须完成四拍：先把事实讲清，再解释物流影响，再用 Buffalo 的可见动作承接并完成一次品牌转化，最后收束到下一步。"
        "热点是营销引子，不是 Buffalo 服务证明；品牌转化必须回答‘这个外部问题，为什么更需要看 Buffalo 的执行优势’。"
        "优势必须落到已确认资料或画面可见的具体执行，例如风险前置、动作可核对、异常可留痕、交接更稳；不能只喊‘安全、专业、服务好’。"
        "避免书面腔、空泛大词和堆砌形容词。"
        "开头仍必须遵守既定的热点事实 Hook，不能为了风格捏造痛点或紧急情况。"
        "结尾可用一句克制的互动或提醒，把前面的品牌优势落到客户下一步，不得高压促销。"
        "不得照抄任何示例或固定句式；不得把未提供证据的服务、覆盖、赔付、时效、追踪或结果写成承诺；"
        "不得使用“最、一定、绝对、保证”等极限营销词。"
        "字幕要短、好扫读；emoji 不作为旁白或字幕的必需元素，最多只可在发布文案中自然使用 0–2 个。"
    )


def prompt_for_chat_douyin() -> str:
    """Return copy rules for the chat card's Douyin caption and scene preview."""
    return (
        "【南非物流抖音文案 SOP】写给跨境卖家的同行式沟通：口语、务实、短句，"
        "先说真实问题或用户关心的节点，再给可核对的建议。可自然使用“我们”，"
        "不要硬塞口号、书面腔或高压成交话术。"
        "标题、正文和分镜都要使用短句，正文不要照搬分镜口播；优先让开头成为卖家会问的一句话。"
        "热点型分镜禁止用‘镜头转到仓内’或‘先看执行现场’充当桥接，必须说明事件与物流安全的关系，并把关系转成 Buffalo 一个有证据的执行优势。"
        "营销承接要从外部风险引到 Buffalo 的可见动作，不暗示 Buffalo 造成了事故，也不把热点结果冒充 Buffalo 业绩。"
        "不能照抄示例或套用固定话术；不使用“最、一定、绝对、保证”等极限营销词。"
        "emoji 仅可出现在面向观众的 body，0–2 个且不影响阅读；旁白和字幕不使用 emoji。"
        "上述风格不能突破事实与服务承诺门禁：没有证据时只写条件式提醒。"
    )


def metadata() -> dict[str, str]:
    """Expose the applied SOP in a project snapshot without copying source samples."""
    return {"id": SOP_ID, "version": SOP_VERSION}
