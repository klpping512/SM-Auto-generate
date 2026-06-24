"""Pydantic models for SA-LogiFlow MVP."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class Platform(str, Enum):
    XIAOHONGSHU = "xiaohongshu"
    DOUYIN = "douyin"
    FACEBOOK = "facebook"
    TWITTER = "twitter"
    REDDIT = "reddit"


class TaskStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    REVIEWING = "reviewing"
    PUBLISHED = "published"
    FAILED = "failed"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    ERROR = "error"


# --- Content Generation ---
class GenerateRequest(BaseModel):
    topic: str = Field(..., description="物流主题，如 '德班港拥堵'")
    category: str = Field(default="port_rates", description="主题分类")
    platforms: list[Platform] = Field(default=[Platform.XIAOHONGSHU, Platform.FACEBOOK, Platform.TWITTER])
    tone: str = Field(default="professional", description="语气: professional/friendly/urgent")
    length: str = Field(default="medium", description="长度: short/medium/long")


class GeneratedContent(BaseModel):
    platform: Platform
    title: str
    body: str
    hashtags: list[str] = []


class GenerateResponse(BaseModel):
    topic: str
    contents: list[GeneratedContent]
    generated_at: str


# --- Queue ---
class QueueItem(BaseModel):
    id: Optional[int] = None
    title: str
    body: str
    platform: Platform
    hashtags: list[str] = []
    status: TaskStatus = TaskStatus.QUEUED
    scheduled_at: Optional[str] = None
    created_at: Optional[str] = None
    error_msg: Optional[str] = None


class QueueCreateRequest(BaseModel):
    title: str
    body: str
    platforms: list[Platform]
    hashtags: list[str] = []
    scheduled_at: Optional[str] = None


# --- Accounts ---
class Account(BaseModel):
    id: Optional[int] = None
    platform: Platform
    name: str
    account_id: str
    status: AccountStatus = AccountStatus.ACTIVE
    config_summary: str = ""
    last_sync: Optional[str] = None


class AccountCreateRequest(BaseModel):
    platform: Platform
    name: str
    account_id: str
    config_summary: str = ""


# --- Topics ---
class TopicCategory(BaseModel):
    id: str
    name_zh: str
    name_en: str
    icon: str
    topics: list[str]
