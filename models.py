"""Pydantic models for SA-LogiFlow v3.0."""
from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional
from enum import Enum


# ==================== Enums ====================

class Platform(str, Enum):
    # 慧媒支持的国内平台
    XIAOHONGSHU = "xiaohongshu"
    DOUYIN = "douyin"
    TIKTOK = "tiktok"
    BILIBILI = "bilibili"
    WEIBO = "weibo"
    KUAISHOU = "kuaishou"
    TOUTIAO = "toutiao"
    ZHIHU = "zhihu"
    WECHAT_CHANNELS = "wechat_channels"
    WECHAT_MP = "wechat_mp"
    BAIJIAHAO = "baijiahao"
    # 外部平台（需 API 接入）
    FACEBOOK = "facebook"
    TWITTER = "twitter"
    REDDIT = "reddit"


class UserRole(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"


class ContentStatus(str, Enum):
    """内容审批状态机: draft → pending_review → approved → queued → published"""
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    QUEUED = "queued"
    PUBLISHED = "published"
    FAILED = "failed"
    REJECTED = "rejected"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    ERROR = "error"


# ==================== Auth ====================

class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: UserRole = UserRole.EDITOR


class SignupRequest(BaseModel):
    """公开注册只接受个人资料，不允许客户端指定权限。"""
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=40)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    username: str
    display_name: str


class UserInfo(BaseModel):
    id: int
    username: str
    role: UserRole
    display_name: str
    status: str


# ==================== Content Generation ====================

class GenerateRequest(BaseModel):
    topic: str = Field(..., description="物流主题，如 '德班港拥堵'")
    instruction: str = Field(default="", description="额外的 AI 指令/需求")
    category: str = Field(default="port_rates", description="主题分类")
    platforms: list[Platform] = Field(default=[Platform.XIAOHONGSHU, Platform.FACEBOOK, Platform.TWITTER])
    tone: str = Field(default="professional", description="语气: professional/friendly/urgent")
    length: str = Field(default="medium", description="长度: short/medium/long")
    kb_category_ids: list[int] = Field(default=[], description="引用的企业知识库分类 id")


class GeneratedContent(BaseModel):
    platform: Platform
    title: str
    body: str
    hashtags: list[str] = []
    image_pages: list[dict] = []
    attachments: list[dict] = []
    duration_target: int | None = None
    voice: str | None = None
    scenes: list[dict] = []
    music_suggestion: str = ""
    selected_asset_ids: list[int] = []
    render_job_id: str | None = None
    rendered_video: dict | None = None


class GenerateResponse(BaseModel):
    topic: str
    contents: list[GeneratedContent]
    generated_at: str
    source: str = "ai"  # "ai" 或 "fallback"


# ==================== Queue ====================

class QueueCreateRequest(BaseModel):
    title: str
    body: str
    platforms: list[Platform]
    hashtags: list[str] = []
    scheduled_at: Optional[str] = None
    status: ContentStatus = ContentStatus.DRAFT
    attachments: list[dict] = []
    source_refs: list[dict] = Field(default=[], description="逐条事实证据：claim/url/source_title/publisher/excerpt")
    account_targets: dict[str, list[int]] = Field(default={}, description="平台到目标账号主键列表；同平台多账号会各建一条发布任务")


class ReviewRequest(BaseModel):
    action: str = Field(..., description="'approve' or 'reject'")
    note: str = ""


# ==================== Accounts ====================

class AccountCreateRequest(BaseModel):
    platform: Platform
    name: str
    account_id: str
    config_summary: str = ""


# ==================== AI Chat ====================

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage] = []
    context: str = ""       # 编辑器当前内容
    command: str | None = None  # 快捷指令（如 /optimize）
    tone: str = "professional"
    length: str = "medium"
    platforms: list[Platform] = [Platform.XIAOHONGSHU]
    topic: str = ""
    session_id: str = Field(default="", max_length=128)


class ChatDualLibraryVideoRequest(BaseModel):
    """Create a verified dual-library video from Hooks returned by AI chat."""
    topic: str = Field(..., min_length=1, max_length=300)
    hotspot_event_ids: list[int] = Field(..., min_length=1, max_length=2)
    platform: str = Field(default="douyin", max_length=32)
    target_duration_ms: int = Field(default=60_000, ge=50_000, le=90_000)
    session_id: str = Field(default="", max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)
    tts_provider: str = Field(default="mimo", max_length=32)
    voice: str = Field(default="mimo_default", max_length=64)


class AccountCredentialsRequest(BaseModel):
    credentials: dict


# ==================== Semantic Assets ====================

class SemanticMatchRequest(BaseModel):
    script: str = ""
    body: str = ""
    scenes: list[dict] = Field(default_factory=list)
    orientation: str | None = None


class MatchSelectionRequest(BaseModel):
    segment_id: int | None = None
    locked: bool = False
    review_confirmed: bool = False
    action: str = "selected"
    reason: str = ""


class SegmentClassificationRequest(BaseModel):
    primary_category: str
    tags: list[dict] = Field(default_factory=list)
    quality_score: float | None = None


class InspirationCreateRequest(BaseModel):
    url: str
    title: str = ""
    summary: str = ""
    primary_category: str | None = None


class InspirationBatchRequest(BaseModel):
    items: list[InspirationCreateRequest] = Field(default_factory=list, max_length=100)


class InspirationRightsRequest(BaseModel):
    rights_status: str
    license_name: str
    attribution: str
    rights_evidence_url: str


class InspirationMaterializeRequest(BaseModel):
    confirmed: bool = False


class HotspotMediaAttachRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2_000)


class HotspotMediaRightsRequest(BaseModel):
    authorization_status: str = Field(default="pending_review", pattern=r"^(authorized|pending_review|blocked)$")
    # 兼容旧客户端；新界面和接口文档只使用 authorization_status。
    rights_tier: str | None = Field(default=None, pattern=r"^(green|yellow|red)$")
    rights_note: str = Field(default="", max_length=2_000)
    license_name: str = Field(default="", max_length=300)
    attribution: str = Field(default="", max_length=500)
    rights_evidence_url: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def require_evidence_for_usable_media(self):
        legacy = {"green": "authorized", "yellow": "pending_review", "red": "blocked"}
        if self.rights_tier and self.authorization_status == "pending_review":
            self.authorization_status = legacy[self.rights_tier]
        if self.authorization_status == "authorized" or self.rights_tier in {"green", "yellow"}:
            values = (
                self.rights_note.strip(), self.license_name.strip(), self.attribution.strip(),
                self.rights_evidence_url.strip(),
            )
            if not all(values) or not self.rights_evidence_url.startswith("https://"):
                raise ValueError("标记为已授权的素材必须填写授权依据、许可证、署名和 HTTPS 证据链接")
        return self


class HotspotMediaMaterializeRequest(BaseModel):
    confirmed: bool = False


class HotspotLibraryClearRequest(BaseModel):
    """Explicit confirmation for destructive hotspot-library cleanup."""
    confirmation: str = Field(default="", max_length=50)


class BrandEvidenceCreateRequest(BaseModel):
    claim: str = Field(..., min_length=3, max_length=1_000)
    evidence_note: str = Field(..., min_length=3, max_length=2_000)
    disclosure_level: str = Field(default="public", pattern=r"^(public|internal|prohibited)$")


class BrandEvidenceConfirmRequest(BaseModel):
    status: str = Field(default="confirmed", pattern=r"^(confirmed|rejected)$")


class EvidencePackageCreateRequest(BaseModel):
    brand_evidence_ids: list[int] = Field(default_factory=list, max_length=100)


class TopicBriefCreateRequest(BaseModel):
    raw_input: str = Field(..., min_length=3, max_length=2_000)
    audience: str = Field(default="", max_length=300)
    goal: str = Field(default="", max_length=300)
    angle: str = Field(default="", max_length=1_000)
    locations: list[str] = Field(default_factory=list, max_length=10)
    logistics_nodes: list[str] = Field(default_factory=list, max_length=10)
    freshness_mode: str = Field(default="recent_or_evergreen", pattern=r"^(recent|evergreen|recent_or_evergreen)$")
    time_window_days: int = Field(default=7, ge=1, le=3650)
    platforms: list[str] = Field(default_factory=lambda: ["douyin"], min_length=1, max_length=5)
    content_form: str = Field(default="video", max_length=50)
    must_include: list[str] = Field(default_factory=list, max_length=30)
    must_avoid: list[str] = Field(default_factory=list, max_length=30)
    source_hotspot_package_id: int | None = None


class TopicBriefUpdateRequest(TopicBriefCreateRequest):
    raw_input: str = Field(default="", max_length=2_000)


class TopicEvidenceSelectionRequest(BaseModel):
    selected: bool = True
    review_status: str = Field(default="confirmed", pattern=r"^(candidate|confirmed|rejected)$")


class TopicBriefGenerateRequest(BaseModel):
    """Create one bounded, evidence-backed video project from a confirmed brief."""
    hotspot_event_id: int = Field(..., ge=1)
    approved_hook_event_ids: list[int] = Field(default_factory=list, max_length=2)
    platform: str = Field(default="douyin", max_length=32)
    target_duration_ms: int = Field(default=60_000, ge=50_000, le=90_000)


class TopicHotspotRecommendationRequest(BaseModel):
    limit: int = Field(default=3, ge=1, le=5)
    use_model: bool = True


class TopicAutoPilotRequest(BaseModel):
    platform: str = Field(default="douyin", max_length=32)
    target_duration_ms: int = Field(default=60_000, ge=50_000, le=90_000)


class ModelRouteRequest(BaseModel):
    provider: str = Field(..., min_length=2, max_length=50)
    base_url: str = Field(..., min_length=8, max_length=500)
    api_key_env: str = Field(..., pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    model: str = Field(..., min_length=1, max_length=200)
    capabilities: list[str] = Field(default_factory=list, max_length=10)
    timeout: int = Field(default=30, ge=5, le=180)
    max_tokens: int = Field(default=1200, ge=0, le=100_000)
    cost_profile: str = Field(default="low", pattern=r"^(low|medium|high)$")
    request_options: dict[str, bool | int] = Field(default_factory=dict, max_length=4)
    enabled: bool = True


# ==================== Video Generation Projects ====================

class VideoProjectCreateRequest(BaseModel):
    source_type: str = Field(default="chat", max_length=32)
    source_snapshot: dict = Field(default_factory=dict)
    title: str = Field(default="", max_length=120)
    platform: str = Field(default="douyin", max_length=32)
    target_duration_ms: int = Field(default=60000, ge=3000, le=180000)
    # Retained for older clients. The server always stores and renders 9:16.
    target_orientation: str = Field(default="portrait", pattern=r"^(portrait|landscape|square)$")
    revision: dict | None = None


class VideoProjectRevisionRequest(BaseModel):
    payload: dict = Field(default_factory=dict)


class VideoGenerationRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=128)


class VideoGenerationResumeRequest(BaseModel):
    payload: dict | None = None


class VideoGenerationManualReviewRequest(BaseModel):
    """人工验收仅适用于已通过技术检查的内部预览，不触发发布。"""

    action: Literal["accept", "reject"]
    checklist: dict[str, bool] = Field(default_factory=dict)
    note: str = Field(default="", max_length=1_000)


class VideoQualityRequest(BaseModel):
    video_source: str = Field(..., min_length=1, max_length=2_000)
    original_prompt: str = Field(default="", max_length=20_000)
    storyboard: dict | list | str = ""
    reference_images: list[str] = Field(default_factory=list, max_length=10)
    target_platform: str = Field(default="抖音", max_length=100)
    mode: str = Field(default="balanced", pattern=r"^(efficient|balanced|detailed)$")
    max_frames: int = Field(default=40, ge=1, le=100)
    auto_regenerate: bool = False


# ==================== Topics ====================

class TopicCategory(BaseModel):
    id: str
    name_zh: str
    name_en: str
    icon: str
    topics: list[str]
