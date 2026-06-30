from __future__ import annotations

import time
from typing import Any, Callable

import requests


class FigmaApiError(RuntimeError):
    """Raised when the Figma REST API returns an unsuccessful response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        rate_limit_type: str | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rate_limit_type = rate_limit_type
        self.response_body = response_body


class FigmaClient:
    """Small wrapper around the Figma REST API used by the exporter."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.figma.com/v1",
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_rate_limit_retries: int = 8,
    ) -> None:
        if not token:
            raise ValueError("Figma token must not be empty")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.sleep = sleep
        self.max_rate_limit_retries = max_rate_limit_retries

    def get_file(self, file_key: str) -> dict[str, Any]:
        return self.get_json(f"/files/{file_key}")

    def get_rendered_image_urls(
        self,
        file_key: str,
        node_ids: list[str],
        *,
        image_format: str,
        scale: int | float | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "ids": ",".join(node_ids),
            "format": image_format,
        }
        if scale is not None:
            params["scale"] = scale
        return self.get_json(f"/images/{file_key}", params=params)

    def get_image_fills(self, file_key: str) -> dict[str, Any]:
        return self.get_json(f"/files/{file_key}/images")

    def get_styles(self, file_key: str) -> dict[str, Any]:
        return self.get_json(f"/files/{file_key}/styles")

    def get_variables(self, file_key: str) -> dict[str, Any]:
        return self.get_json(f"/files/{file_key}/variables/local")

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._get_api_response(path, params=params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise FigmaApiError(
                f"GET {path} returned invalid JSON",
                status_code=response.status_code,
                response_body=getattr(response, "text", None),
            ) from exc
        if not isinstance(payload, dict):
            raise FigmaApiError(
                f"GET {path} returned a non-object JSON payload",
                status_code=response.status_code,
                response_body=getattr(response, "text", None),
            )
        return payload

    def download(self, url: str) -> bytes:
        response = self.session.get(url)
        if response.status_code >= 400:
            raise FigmaApiError(
                f"Download failed with HTTP {response.status_code}: {url}",
                status_code=response.status_code,
                response_body=getattr(response, "text", None),
            )
        return response.content

    def _get_api_response(
        self,
        path: str,
        *,
        params: dict[str, Any] | None,
    ) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"X-Figma-Token": self.token}

        for attempt in range(self.max_rate_limit_retries + 1):
            response = self.session.get(url, headers=headers, params=params)
            if response.status_code != 429:
                break
            if attempt >= self.max_rate_limit_retries:
                self._raise_api_error(path, response)
            self.sleep(self._retry_delay(response))
        else:
            raise AssertionError("unreachable retry state")

        if response.status_code >= 400:
            self._raise_api_error(path, response)
        return response

    def _retry_delay(self, response: requests.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after is not None else 1.0
        except ValueError:
            delay = 1.0
        return max(0.0, min(delay, 60.0))

    def _raise_api_error(self, path: str, response: requests.Response) -> None:
        body = getattr(response, "text", None)
        detail = self._response_error_detail(response)
        rate_limit_type = response.headers.get("X-Figma-Rate-Limit-Type")
        message = f"GET {path} failed with HTTP {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        raise FigmaApiError(
            message,
            status_code=response.status_code,
            rate_limit_type=rate_limit_type,
            response_body=body,
        )

    def _response_error_detail(self, response: requests.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return getattr(response, "text", None) or None
        if isinstance(payload, dict):
            err = payload.get("err") or payload.get("message")
            if err:
                return str(err)
        return getattr(response, "text", None) or None
