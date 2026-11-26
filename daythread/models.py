"""Model gateway: providers, routing, caching, budgets, and usage."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from . import __version__, obs
from .db import canonical, get_setting, json_load, utc_now
from .errors import BudgetExceededError, ProviderError, ValidationError

DEFAULTS = {
    "model.provider": "local.echo",
    "model.model": "echo-1",
    "model.fallback": "",
    "model.temperature": "0.2",
    "model.max_tokens": "1024",
    "model.max_input_chars": "32000",
    "model.daily_token_budget": "0",
    "model.cache_ttl_hours": "24",
    "model.base_url": "https://api.openai.com/v1",
    "model.api_key_ref": "OPENAI_API_KEY",
    "model.timeout_seconds": "30",
}


@dataclass
class ModelResponse:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
        }


class EchoProvider:
    name = "local.echo"

    def complete(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> ModelResponse:
        last = messages[-1]["content"] if messages else ""
        text = "echo: {}".format(last)
        return ModelResponse(
            text=text,
            provider=self.name,
            model=model,
            prompt_tokens=_estimate_tokens(last),
            completion_tokens=_estimate_tokens(text),
            raw={"local": True},
        )


class OpenAICompatibleProvider:
    name = "openai.compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        opener: Optional[Callable[[urllib.request.Request], Any]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.opener = opener

    def complete(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> ModelResponse:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            "{}/chat/completions".format(self.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(self.api_key),
                "User-Agent": "daythread/{}".format(__version__),
            },
            method="POST",
        )
        try:
            if self.opener is not None:
                raw = self.opener(request)
            else:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
            if hasattr(raw, "read"):
                raw = raw.read()
            data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except urllib.error.HTTPError as exc:
            raise ProviderError("provider HTTP {}".format(exc.code))
        except Exception as exc:  # noqa: BLE001 - normalized to provider error
            raise ProviderError("provider request failed: {}".format(exc))
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("provider returned an unexpected payload")
        usage = data.get("usage") or {}
        return ModelResponse(
            text=str(text),
            provider=self.name,
            model=str(data.get("model", model)),
            prompt_tokens=int(usage.get("prompt_tokens") or _estimate_tokens(json.dumps(messages))),
            completion_tokens=int(usage.get("completion_tokens") or _estimate_tokens(str(text))),
            raw=data,
        )


class ModelGateway:
    def __init__(self, conn, providers: Optional[Dict[str, Any]] = None) -> None:
        self.conn = conn
        self.providers: Dict[str, Any] = {"local.echo": EchoProvider()}
        if providers:
            self.providers.update(providers)

    def config(self) -> Dict[str, Any]:
        return {
            "provider": _setting(self.conn, "model.provider"),
            "model": _setting(self.conn, "model.model"),
            "fallback": _setting(self.conn, "model.fallback"),
            "temperature": float(_setting(self.conn, "model.temperature")),
            "max_tokens": int(_setting(self.conn, "model.max_tokens")),
            "max_input_chars": int(_setting(self.conn, "model.max_input_chars")),
            "daily_token_budget": int(_setting(self.conn, "model.daily_token_budget")),
            "cache_ttl_hours": int(_setting(self.conn, "model.cache_ttl_hours")),
            "base_url": _setting(self.conn, "model.base_url"),
            "api_key_ref": _setting(self.conn, "model.api_key_ref"),
            "timeout_seconds": float(_setting(self.conn, "model.timeout_seconds")),
        }

    def chat(
        self,
        messages: List[Dict[str, str]],
        task: str = "chat",
        use_cache: bool = True,
        max_tokens: Optional[int] = None,
    ) -> ModelResponse:
        normalized = _validate_messages(messages)
        config = self.config()
        input_chars = sum(len(item["content"]) for item in normalized)
        if input_chars > config["max_input_chars"]:
            raise ValidationError(
                "input is too large ({} > {} chars)".format(input_chars, config["max_input_chars"])
            )
        cache_key = _cache_key(config["provider"], config["model"], normalized, config["temperature"], max_tokens)
        if use_cache:
            cached = self._get_cache(cache_key)
            if cached is not None:
                self._record_usage(cached, task, ok=True, cached=True)
                obs.incr(self.conn, "model.cache_hits")
                return cached

        budget = config["daily_token_budget"]
        used = self.usage_today()
        if budget > 0 and used >= budget:
            raise BudgetExceededError(
                "daily token budget exhausted ({}/{}); raise model.daily_token_budget".format(used, budget)
            )

        chain = [config["provider"]]
        if config["fallback"] and config["fallback"] not in chain:
            chain.append(config["fallback"])
        errors: List[str] = []
        for provider_name in chain:
            try:
                provider = self._provider(provider_name, config)
                started = time.monotonic()
                response = provider.complete(
                    config["model"],
                    normalized,
                    config["temperature"],
                    int(max_tokens or config["max_tokens"]),
                )
                response.latency_ms = (time.monotonic() - started) * 1000.0
                self._record_usage(response, task, ok=True, cached=False)
                if use_cache:
                    self._set_cache(cache_key, response, config["cache_ttl_hours"])
                obs.incr(self.conn, "model.calls")
                obs.incr(self.conn, "model.tokens", response.prompt_tokens + response.completion_tokens)
                obs.audit(
                    self.conn,
                    "model.completed",
                    actor="agent",
                    target=provider_name,
                    detail={
                        "model": response.model,
                        "task": task,
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                    },
                )
                return response
            except Exception as exc:  # noqa: BLE001 - try fallbacks
                errors.append("{}: {}".format(provider_name, exc))
                self._record_failure(provider_name, config["model"], task, str(exc))
                obs.incr(self.conn, "model.errors")
        raise ProviderError("all model providers failed: {}".format("; ".join(errors)))

    def usage_today(self) -> int:
        day = datetime.now(timezone.utc).date().isoformat()
        row = self.conn.execute(
            """
            SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total
            FROM model_usage WHERE date(ts) = ? AND ok = 1 AND cached = 0
            """,
            (day,),
        ).fetchone()
        return int(row["total"] or 0)

    def _provider(self, name: str, config: Dict[str, Any]):
        if name in self.providers:
            return self.providers[name]
        if name == "openai.compatible":
            api_key = os.environ.get(config["api_key_ref"], "")
            if not api_key:
                raise ProviderError(
                    "environment variable {} is not set".format(config["api_key_ref"])
                )
            provider = OpenAICompatibleProvider(
                config["base_url"], api_key, config["timeout_seconds"]
            )
            self.providers[name] = provider
            return provider
        raise ProviderError("unknown model provider: {}".format(name))

    def _get_cache(self, key: str) -> Optional[ModelResponse]:
        row = self.conn.execute(
            "SELECT * FROM model_cache WHERE key = ? AND expires_at > ?",
            (key, utc_now()),
        ).fetchone()
        if row is None:
            return None
        payload = json_load(row["response"])
        return ModelResponse(
            text=payload["text"],
            provider=payload["provider"],
            model=payload["model"],
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            completion_tokens=int(payload.get("completion_tokens", 0)),
            latency_ms=0.0,
            cached=True,
            raw=payload.get("raw", {}),
        )

    def _set_cache(self, key: str, response: ModelResponse, ttl_hours: int) -> None:
        expires = (datetime.now(timezone.utc) + timedelta(hours=max(ttl_hours, 0))).isoformat()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO model_cache(key, provider, model, response, created_at, expires_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                response.provider,
                response.model,
                canonical(response.to_dict() | {"raw": response.raw}),
                utc_now(),
                expires,
            ),
        )
        self.conn.commit()

    def _record_failure(self, provider: str, model: str, task: str, error: str) -> None:
        self.conn.execute(
            """
            INSERT INTO model_usage(
                id, ts, provider, model, task, prompt_tokens, completion_tokens,
                latency_ms, ok, cached, error
            ) VALUES(?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?)
            """,
            (uuid4().hex, utc_now(), provider, model, task, error),
        )
        self.conn.commit()

    def _record_usage(
        self,
        response: ModelResponse,
        task: str,
        ok: bool,
        cached: bool,
        error: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO model_usage(
                id, ts, provider, model, task, prompt_tokens, completion_tokens,
                latency_ms, ok, cached, error
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                utc_now(),
                response.provider,
                response.model,
                task,
                response.prompt_tokens,
                response.completion_tokens,
                response.latency_ms,
                1 if ok else 0,
                1 if cached else 0,
                error,
            ),
        )
        self.conn.commit()


def list_models(conn) -> Dict[str, Any]:
    gateway = ModelGateway(conn)
    config = gateway.config()
    usage = gateway.usage_today()
    providers = ["local.echo", "openai.compatible"]
    return {"config": config, "providers": providers, "usage_today": usage}


def ask(conn, text: str, task: str = "chat", use_cache: bool = True) -> ModelResponse:
    gateway = ModelGateway(conn)
    return gateway.chat([{"role": "user", "content": text}], task=task, use_cache=use_cache)


def _setting(conn, key: str) -> str:
    return str(get_setting(conn, key, DEFAULTS[key]))


def _validate_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not isinstance(messages, list) or not messages:
        raise ValidationError("messages must be a non-empty list")
    normalized = []
    for item in messages:
        if not isinstance(item, dict):
            raise ValidationError("each message must be an object")
        role = str(item.get("role", "")).strip()
        content = item.get("content")
        if role not in ("system", "user", "assistant", "tool"):
            raise ValidationError("invalid message role: {}".format(role))
        if not isinstance(content, str):
            raise ValidationError("message content must be a string")
        normalized.append({"role": role, "content": content})
    return normalized


def _cache_key(
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
) -> str:
    return sha256(
        canonical(
            {
                "provider": provider,
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")
    ).hexdigest()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0
