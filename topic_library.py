"""South Africa Logistics Topic Library - 6 core categories."""
from models import TopicCategory

TOPIC_CATEGORIES: list[TopicCategory] = [
    TopicCategory(
        id="customs",
        name_zh="清关税务",
        name_en="Customs & Taxation",
        icon="mdi:shield-check",
        topics=[
            "PAT注册流程详解",
            "进口关税计算方法",
            "海关查验处理指南",
            "禁运物品清单更新",
            "电子清关系统操作",
            "退税申请流程",
            "临时进口许可办理",
            "海关编码归类技巧",
        ]
    ),
    TopicCategory(
        id="port_rates",
        name_zh="港口运价",
        name_en="Port Rates & Scheduling",
        icon="mdi:ship",
        topics=[
            "德班港拥堵实时预警",
            "开普敦航线时效分析",
            "集装箱运价走势预测",
            "旺季舱位预订策略",
            "Transnet罢工影响评估",
            "港口堆场利用率报告",
            "船期延误应急方案",
            "新航线开通信息速递",
        ]
    ),
    TopicCategory(
        id="last_mile",
        name_zh="末端配送",
        name_en="Last-Mile Delivery",
        icon="mdi:truck-delivery",
        topics=[
            "南非本地快递对比评测",
            "最后一公里效率优化",
            "偏远地区配送方案",
            "妥投率提升策略",
            "COD货到付款风险管理",
            "农村地区物流覆盖",
            "同城配送时效对比",
            "智能快递柜部署进展",
        ]
    ),
    TopicCategory(
        id="pitfalls",
        name_zh="避坑指南",
        name_en="Pitfall Guides",
        icon="mdi:alert-circle",
        topics=[
            "货物被扣常见原因分析",
            "包装合规要求详解",
            "保险理赔流程指南",
            "旺季爆仓应对策略",
            "汇率波动风险对冲",
            "假货举报处理流程",
            "仓储合同陷阱识别",
            "跨境支付常见问题",
        ]
    ),
    TopicCategory(
        id="case_studies",
        name_zh="成功案例",
        name_en="Success Stories",
        icon="mdi:trophy",
        topics=[
            "卖家出海实战经验分享",
            "旺季备战全流程复盘",
            "从0到1开拓南非市场",
            "客户好评与见证",
            "降本增效真实案例",
            "新品牌破局之路",
            "小卖家逆袭故事",
            "大促期间物流保障纪实",
        ]
    ),
    TopicCategory(
        id="market_insights",
        name_zh="市场洞察",
        name_en="Market Insights",
        icon="mdi:chart-line",
        topics=[
            "南非电商趋势分析",
            "热门品类数据报告",
            "消费者行为洞察",
            "政策法规变动速递",
            "竞争对手动态追踪",
            "新兴市场机会挖掘",
            "季节性需求预测",
            "行业白皮书解读",
        ]
    ),
]

TOPIC_MAP = {cat.id: cat for cat in TOPIC_CATEGORIES}

PLATFORM_PROMPTS = {
    "xiaohongshu": {
        "system": "你是一个小红书爆款文案写手，擅长写物流/跨境电商领域的种草笔记。风格要求：口语化、有亲和力、多用emoji、分段清晰、结尾带互动引导。",
        "format": "标题要吸引眼球（18-22字），正文分3-4个小段，每段一个重点，结尾用提问引导评论。带3-5个相关话题标签。",
        "max_len": 800,
    },
    "douyin": {
        "system": "你是一个抖音短视频脚本写手，擅长物流行业的15-60秒口播脚本。风格要求：开头3秒抓眼球、节奏快、信息密度高、结尾有行动号召。",
        "format": "脚本格式：【画面】+【口播】，总时长控制在30-60秒，结尾引导关注或评论。",
        "max_len": 500,
    },
    "facebook": {
        "system": "You are a professional B2B content writer for the South African logistics industry. Write engaging Facebook posts that combine industry insights with practical value.",
        "format": "Professional but approachable tone. Start with a hook, provide 2-3 key insights, end with a CTA. Include relevant emojis sparingly. 150-300 words.",
        "max_len": 1500,
    },
    "twitter": {
        "system": "You are a logistics industry thought leader on Twitter/X. Write concise, impactful tweets about South African logistics, supply chain, and trade.",
        "format": "Concise, punchy, under 280 characters. Use relevant hashtags (2-3 max). Can be a thread starter or standalone insight.",
        "max_len": 280,
    },
    "reddit": {
        "system": "You are a supply chain expert contributing valuable insights to Reddit communities like r/logistics, r/southafrica, and r/supplychain. Write informative, data-driven posts.",
        "format": "Informative, analytical tone. Include data points where possible. Structure with clear paragraphs. End with discussion questions. 200-500 words.",
        "max_len": 2500,
    },
}
