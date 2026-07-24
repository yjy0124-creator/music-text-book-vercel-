"""활동문 추천을 Claude에게 맡기는 선택적 어댑터.

`ANTHROPIC_API_KEY` 환경변수가 설정되어 있을 때만 동작한다. API 키가 없거나
호출이 실패·타임아웃하거나 응답이 정해진 JSON 스키마를 따르지 않으면
`recommend_activities`는 예외를 던지지 않고 `None`을 반환한다 — 호출한 쪽
(`curriculum_audit.py`)은 이 경우 기존 규칙 기반 추천으로 그대로 대체한다.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-sonnet-5"
REQUEST_TIMEOUT_SECONDS = 20

SYSTEM_PROMPT = """
너는 2022 개정 교육과정 음악과 교과서 전문 집필자 및 검수위원이야.
전달받은 [제재곡 정보], [제재곡 유형], [학습자 수준], [현재 원고의 활동 문장](있다면),
[연계 성취기준], [참고 활동 표본]을 바탕으로 이 제재곡에 알맞은 활동을 새로 제안해라.

[중요] 원고의 활동 문장은 저자 초안일 뿐이다. 편집자는 난이도·흥미·성취기준 부합성을
종합적으로 판단해 상당 부분 다시 쓰는 경우가 대부분이다. 따라서 기존 활동 문장을
1:1로 다듬는 것이 아니라, 그 의도와 부족한 점을 참고만 하고 제재곡에 맞는
새로운 활동 3개를 구성하는 것이 목표다.

[검수·추천은 반드시 아래 순서로 수행하고, 각 결과를 출력 JSON의 해당 필드에 남겨라.]
1단계 (intent_analysis): 현재 원고 활동(있다면)이 학생에게 요구하는 행동과 의도를 요약한다.
   활동이 없으면 "현재 활동 없음"이라고 쓴다.
2단계 (standard_fit): 그 의도가 [연계 성취기준]의 핵심 성취 요소를 얼마나 충족하는지
   진단한다. fit_level은 "충분"/"부분 충족"/"미흡"/"현재 활동 없음" 중 하나로 판정한다.
3단계 (recommended_activities): [제재곡 유형]에 어울리는 서로 다른 활동 유형 3개를
   아래 후보 중에서 골라 새로 구성한다. 성취기준 미흡분을 [제재곡 정보]의 구체적 특성
   (가사, 정서, 셈여림, 가락, 시대적 배경 등)으로 보완한다.
   - 감상형: 정서·미적 특징을 느끼고 나누는 활동 (모든 제재곡에 사용 가능)
   - 가창형: 노랫말 낭송·가창과 연계한 활동 (가창곡에만 사용)
   - 연주형: 악기 연주·신체 표현과 연계한 활동 (연주곡에 사용)
   - 비평·맥락형: 시대적·문화적 배경과 연계한 활동 (모든 제재곡에 사용 가능)
   - 표현·창작형: 글쓰기 등 다른 매체로 표현하는 활동 (모든 제재곡에 사용 가능)
   가창곡이 아니면 '가창형'을, 연주곡이 아니면 '연주형'을 선택지에서 제외해라.

[문장 작성 규칙]
1. 모든 활동 문장은 반말 청유형으로 끝낸다 (~해 보자, ~불러 보자, ~나누어 보자, ~써 보자 등).
   "~봅시다", "~합니다" 같은 격식체는 절대 쓰지 않는다.
2. [학습자 수준]에 맞게 문장 길이와 어휘를 조정한다. 고등학교 기준 문장당 40~55자 내외,
   전문 용어는 1개까지만 허용한다.
3. 다음 학술 어휘는 쓰지 않는다: 수용, 내면화, 구조적 분석, 심미적 지각, 통찰.
4. [제재곡 정보]와 [현재 원고의 활동 문장]에 실제로 없는 사실은 추측하거나 지어내지 않는다.
5. 반드시 아래 JSON 스키마 그대로만 응답한다. 스키마 밖 설명이나 마크다운 코드블록은 출력하지 않는다.

{
  "intent_analysis": "1단계 결과",
  "standard_fit": { "fit_level": "충분 | 부분 충족 | 미흡 | 현재 활동 없음", "reason": "2단계 결과" },
  "recommended_activities": [
    { "activity_type": "감상형 | 가창형 | 연주형 | 비평·맥락형 | 표현·창작형",
      "text": "반말 청유형 활동 문장", "rationale": "제재곡의 어떤 특성으로 어떤 성취 요소를 보완했는지" }
  ]
}
""".strip()

_REQUIRED_ACTIVITY_KEYS = ("activity_type", "text", "rationale")


def _build_user_prompt(payload: dict[str, Any]) -> str:
    current_activities = payload.get("current_activities") or []
    activity_lines = (
        "\n".join(f"  {i}) {text}" for i, text in enumerate(current_activities, 1))
        if current_activities else "  (없음)"
    )
    samples = payload.get("reference_samples") or []
    sample_lines = (
        "\n".join(f"  {i}) \"{text}\"" for i, text in enumerate(samples, 1))
        if samples else "  (없음)"
    )
    return f"""
- 제재곡: {payload.get('topic', '제시된 악곡')}
- 제재곡 유형: {payload.get('piece_type', '감상곡')}
- 학습자 수준: {payload.get('target_level', '고등학교 1학년')}
- 관련 성취기준: [{payload.get('standard_code', '')}] {payload.get('standard_text', '')}
- 현재 원고 활동 문장:
{activity_lines}
- 참고 활동 표본(등록된 교과서, 같은 학습자 수준·비슷한 활동 영역만 필터링):
{sample_lines}

[요청 사항]
위 정보를 바탕으로 SYSTEM_PROMPT에 지정된 절차와 JSON 스키마로 응답해라.
""".strip()


def _validate(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    if "intent_analysis" not in data or "standard_fit" not in data:
        return None
    activities = data.get("recommended_activities")
    if not isinstance(activities, list) or not activities:
        return None
    for item in activities:
        if not isinstance(item, dict) or not all(key in item for key in _REQUIRED_ACTIVITY_KEYS):
            return None
    return data


class _ClaudeActivityAdapter:
    def recommend_activities(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            body = json.dumps({
                "model": MODEL,
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": _build_user_prompt(payload)}],
            }).encode("utf-8")
            request = urllib.request.Request(
                ANTHROPIC_API_URL, data=body, method="POST",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            text = response_data["content"][0]["text"]
            parsed = json.loads(text)
            return _validate(parsed)
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError,
                json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return None
        except Exception:
            # 예상치 못한 오류도 절대 밖으로 던지지 않는다 — 호출부는 규칙 기반으로 대체한다.
            return None


adapter = _ClaudeActivityAdapter()
