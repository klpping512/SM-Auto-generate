"""Pydantic models for SA-LogiFlow v3.0."""
from pydantic import BaseModel, Field
from typing import Optional
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


class AccountCredentialsRequest(BaseModel):
    credentials: dict


class LocalScanCompleteRequest(BaseModel):
    handoff_token: str
    cookies: list[dict] = Field(default_factory=list)
    error: str | None = None


# ==================== Topics ====================

class TopicCategory(BaseModel):
    id: str
    name_zh: str
    name_en: str
    icon: str
    topics: list[str]
