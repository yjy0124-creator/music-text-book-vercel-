"""Vercel Python 런타임 엔트리포인트.

`team_app.py`를 그대로 재사용한다 — Vercel은 이 파일에서 BaseHTTPRequestHandler를
상속한 `handler`라는 이름의 클래스를 찾으므로, 기존 `_handler(application)`이
반환하는 핸들러를 그 이름으로 노출하기만 하면 된다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from team_app import TeamApplication, _handler  # noqa: E402

application = TeamApplication(Path("/tmp/team_data"))
handler = _handler(application)
