"""Vercel Blob을 통한 선택적 파일 영속화 어댑터.

`BLOB_READ_WRITE_TOKEN` 환경변수가 설정되어 있을 때만 동작한다(Vercel에 Blob
Store를 연결하면 자동 주입됨). 로컬 실행이나 Render 배포처럼 이 값이 없는
환경에서는 모든 함수가 조용히 아무 일도 하지 않는다 — 로컬 디스크가 요청 간
그대로 유지되므로 별도 영속화가 필요 없기 때문이다.

호출부(`team_app.py`)는 이 모듈을 항상 "있으면 쓰고 없으면 무시되는" 보조
수단으로만 사용한다 — 실패해도 예외를 던지지 않고 조용히 넘어간다.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

BLOB_API_URL = "https://blob.vercel-storage.com"
REQUEST_TIMEOUT_SECONDS = 30


def enabled() -> bool:
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))


def push(local_path: Path, key: str) -> None:
    """`local_path`가 존재하면 그 내용을 Blob의 `key`로 업로드한다."""
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token or not local_path.is_file():
        return
    try:
        data = local_path.read_bytes()
        request = urllib.request.Request(
            f"{BLOB_API_URL}/{key}", data=data, method="PUT",
            headers={
                "authorization": f"Bearer {token}",
                "x-api-version": "7",
                "x-content-type": "application/octet-stream",
                "x-allow-overwrite": "1",
            },
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS):
            pass
    except Exception:
        # 업로드 실패는 다음 요청에서 다시 시도될 뿐이니 조용히 넘어간다.
        pass


def pull_if_missing(local_path: Path, key: str) -> None:
    """`local_path`가 없을 때만 Blob의 `key`에서 내려받아 저장한다."""
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token or local_path.exists():
        return
    try:
        request = urllib.request.Request(
            f"{BLOB_API_URL}/{key}", method="GET",
            headers={"authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            data = response.read()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
    except urllib.error.HTTPError:
        # Blob에도 없는 파일(아직 한 번도 올라간 적 없음) — 정상적인 경우다.
        pass
    except Exception:
        pass


def delete(key: str) -> None:
    """Blob에서 `key`를 베스트 에포트로 삭제한다(실패해도 무시)."""
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        return
    try:
        request = urllib.request.Request(
            f"{BLOB_API_URL}/{key}", method="DELETE",
            headers={"authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS):
            pass
    except Exception:
        pass
