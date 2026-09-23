"""Jev（TypeSafe System One）决策客户端。

Jev 不生成文本：给一段应用状态加若干「定型问题」，返回带概率的定型决策。
OpenRouter 因此把它放在独立的 **Decisions** 端点（``/api/alpha/decisions``），
不是 ``/chat/completions``，也不出现在 ``GET /api/v1/models`` 列表里。

请求体：``{model, state, questions}``。``state`` 按 OpenRouter 参考文档是
字符串，本模块把 dict 序列化为 JSON 字符串后再发（若上游改为接受对象，
只需调整 ``_encode_state``）。
响应体：``{answers, model, provider, usage, id}``；``answers`` 里每个问题一条
定型答案。失败时为 ``{"error": {"code": ..., "message": ...}}``。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pulse.http_clients import outbound_client

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api"
DEFAULT_MODEL = "typesafe/jev-1.13"
DECISIONS_PATH = "/alpha/decisions"


class JevError(RuntimeError):
    """Jev 调用失败：网络、非 2xx、或响应结构不可解析。"""


@dataclass(frozen=True)
class JevAnswer:
    """单个问题的定型答案（不同 question type 的字段形状不同）。"""

    name: str
    raw: Any

    @property
    def choice(self) -> str | None:
        """``choice`` 问题选中的选项。"""
        if isinstance(self.raw, dict):
            value = self.raw.get("choice")
            if isinstance(value, str):
                return value
        return None

    @property
    def probability(self) -> float | None:
        """``noul`` 问题「是」的概率。

        上游可能直接返回数值，也可能包在 ``probability`` 或
        ``probabilities``（true/false）里，三种形状都认。
        """
        raw = self.raw
        if isinstance(raw, bool):
            return 1.0 if raw else 0.0
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, dict):
            for key in ("probability", "p", "noul"):
                value = raw.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
            probs = raw.get("probabilities")
            if isinstance(probs, dict):
                for key in ("true", "True", "yes"):
                    value = probs.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        return float(value)
        return None

    @property
    def confidence(self) -> float | None:
        if isinstance(self.raw, dict):
            value = self.raw.get("confidence")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return None

    @property
    def probabilities(self) -> dict[str, float]:
        """各选项的概率字典；形状不可识别时返回空字典。"""
        if isinstance(self.raw, dict):
            probs = self.raw.get("probabilities")
            if isinstance(probs, dict):
                return {
                    str(k): float(v)
                    for k, v in probs.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                }
        return {}


@dataclass
class JevDecision:
    """一次 Decisions 请求的定型答案集合（按问题名索引）。"""

    answers: dict[str, JevAnswer] = field(default_factory=dict)
    model: str | None = None
    provider: str | None = None
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def answer(self, name: str) -> JevAnswer | None:
        """按问题名取答案；未作答返回 None。"""
        return self.answers.get(name)


def _encode_state(state: Any) -> str:
    """OpenRouter 参考文档把 state 标为 string；dict/list 统一序列化。"""
    if isinstance(state, str):
        return state
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def build_choice_question(instructions: str, criteria: dict[str, str]) -> dict:
    """``choice``：从 criteria 的键里选一个。"""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def build_noul_question(instructions: str, *, yes: str, no: str) -> dict:
    """``noul``：是/否概率。

    OpenRouter 要求 criteria 同时给出 true / false 两侧描述（TypeSafe 自家
    API 允许省略，这里保持两侧都给，避免 400）。
    """
    return {
        "type": "noul",
        "instructions": instructions,
        "criteria": {"true": yes, "false": no},
    }


class JevClient:
    """OpenRouter Decisions 端点客户端。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 3.0,
        app_title: str | None = "Cursor Pulse",
        http_referer: str | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.app_title = app_title
        self.http_referer = http_referer

    @property
    def decisions_url(self) -> str:
        return f"{self.base_url}{DECISIONS_PATH}"

    def decide(
        self,
        *,
        state: Any,
        questions: dict[str, dict],
        session_id: str | None = None,
    ) -> JevDecision:
        """提交一次 Decisions 请求；任何失败都抛 :class:`JevError`。"""
        if not questions:
            raise JevError("decisions request needs at least one question")
        payload: dict[str, Any] = {
            "model": self.model,
            "state": _encode_state(state),
            "questions": questions,
        }
        if session_id:
            payload["session_id"] = session_id
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.app_title:
            headers["X-OpenRouter-Title"] = self.app_title
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer

        try:
            with outbound_client(timeout=self.timeout_seconds) as client:
                response = client.post(self.decisions_url, headers=headers, json=payload)
        except Exception as exc:  # 网络 / 超时
            raise JevError(f"jev request failed: {exc}") from exc

        if response.status_code != 200:
            raise JevError(f"jev HTTP {response.status_code}: {_error_message(response)}")
        try:
            data = response.json()
        except Exception as exc:
            raise JevError(f"jev response not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise JevError(f"jev response not an object: {data!r}")
        if isinstance(data.get("error"), dict):
            raise JevError(f"jev error: {_error_message(response, data)}")
        answers_raw = data.get("answers")
        if not isinstance(answers_raw, dict) or not answers_raw:
            raise JevError(f"jev response has no answers: {data!r}")

        return JevDecision(
            answers={name: JevAnswer(name=name, raw=raw) for name, raw in answers_raw.items()},
            model=data.get("model"),
            provider=data.get("provider"),
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
            raw=data,
        )


def _error_message(response, data: dict | None = None) -> str:
    payload = data
    if payload is None:
        try:
            payload = response.json()
        except Exception:
            payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
    return (response.text or "")[:200]


def build_jev_client(config) -> JevClient | None:
    """从 AppConfig 构造客户端；未启用或缺 key 时返回 None。"""
    jev = getattr(config, "jev", None)
    if jev is None or not jev.enabled or not (jev.api_key or "").strip():
        return None
    return JevClient(
        api_key=jev.api_key.strip(),
        model=jev.model,
        base_url=jev.base_url,
        timeout_seconds=jev.timeout_seconds,
    )
