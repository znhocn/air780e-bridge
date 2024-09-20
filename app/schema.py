"""Pydantic 请求/响应模型"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)


class TokenResponse(BaseModel):
    token: str


class SendSmsRequest(BaseModel):
    to: str = Field(..., description="目标号码")
    content: str = Field(..., description="短信内容")


class MessageOut(BaseModel):
    id: int
    direction: str
    sender: str | None = None
    receiver: str | None = None
    content: str
    status: str
    created_at: str | None = None


class Page(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class ApiKeyCreated(BaseModel):
    id: int
    name: str
    key: str  # 仅创建时返回一次
    key_prefix: str
    created_at: str | None = None


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    active: int
    created_at: str | None = None


class NotifyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: str = Field(..., description="dingtalk/wecom/feishu/email/webhook")
    enabled: bool = True
    match_from: str = ""
    match_contains: str = ""
    params: dict = Field(default_factory=dict)


class NotifyOut(BaseModel):
    id: int
    name: str
    type: str
    enabled: int
    match_from: str
    match_contains: str
    params: dict
    created_at: str | None = None