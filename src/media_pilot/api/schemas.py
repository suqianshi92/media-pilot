"""API v1 响应合同 — 统一 envelope、消息和分页元数据"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

# ---- 状态字面量 ----

ApiStatus = Literal["success", "accepted", "error"]
# 对应前端 types/api.ts 中的 ApiStatus：
#  - success: 请求已成功完成
#  - accepted: 请求已接受，后台继续处理
#  - error: 请求失败

MessageLevel = Literal["info", "success", "warning", "error"]


# ---- 消息 ----

class ApiMessage(BaseModel):
    """单个 API 响应消息，code 为英文机器码，text 为中文人类可读文本"""
    level: MessageLevel = "info"
    code: str
    text: str
    details: dict[str, Any] | None = None


# ---- 信封 ----

TData = TypeVar("TData", bound=object)


class ApiEnvelope(BaseModel, Generic[TData]):  # noqa: UP046
    """统一 API 响应信封

    所有 /api/v1 端点均返回此结构：

        {
          "status": "success",
          "data": ...,
          "messages": [
            {"level": "info", "code": "task_loaded", "text": "任务已加载"}
          ],
          "meta": {}
        }

    status 取值：
      - ``"success"``:  请求成功并已完成
      - ``"accepted"``: 请求已接受，后台继续处理
      - ``"error"``:    请求失败
    """
    status: ApiStatus
    data: TData
    messages: list[ApiMessage] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


# ---- 分页 ----

class PaginationMeta(BaseModel):
    """列表接口的分页元数据"""
    page: int = 1
    page_size: int = 50
    total: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)