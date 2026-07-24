"""Vercel Python 런타임 엔트리포인트.

`team_app.py`를 그대로 재사용한다. Vercel의 빌드 타임 검사는 `handler = ...`
같은 대입이 아니라 파일에 실제로 `class handler(...):` 문이 있는지를 찾는 것으로
보인다(대입만으로는 "top-level handler variable 없음" 오류가 났다) — 그래서
기존 `_handler(application)` 팩토리가 만든 클래스를 진짜 `class handler` 문으로
한 번 더 감싼다.

주의: 최상위 이름을 `application`으로 두면 Vercel이 이를 WSGI 앱으로 오인해
("Could not determine the application interface") 배포가 깨진다 — 그래서
`handler`만 남기고 나머지는 언더스코어를 붙인 이름을 쓴다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from team_app import TeamApplication, _handler  # noqa: E402

_team_application = TeamApplication(Path("/tmp/team_data"))
_TeamHandler = _handler(_team_application)


class handler(_TeamHandler):
    pass
