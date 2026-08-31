"""Deterministic hook headlines and retention openings used by chat/video copy."""


def attention_headline(what_happened: str, logistics_question: str = "", source_title: str = "") -> str:
    text = str(what_happened or source_title or "").strip()
    if not text:
        return "物流现场变化"
    compact = "".join(text.split())
    return compact[:24]


def retention_opening(what_happened: str, question: str = "") -> str:
    fact = str(what_happened or "").strip()
    q = str(question or "").strip()
    if fact and q:
        line = f"{fact.rstrip('。！？')}。{q}"
    else:
        line = fact or q
    if not line:
        return ""
    if line.endswith(("。", "？", "！")):
        return line
    return line + ("？" if q and line.endswith(q) else "。")
