"""생성된 원고 점검 결과를 localhost에서 제공한다."""

from __future__ import annotations

import functools
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote


def latest_audit(output_root: Path) -> Path:
    candidates = list(output_root.resolve().glob("*/audit.html"))
    if not candidates:
        raise FileNotFoundError(f"audit.html 결과를 찾지 못했습니다: {output_root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def serve_audit(output_root: Path, host: str = "127.0.0.1", port: int = 8765,
                open_browser: bool = True) -> None:
    root = output_root.resolve()
    target = latest_audit(root)
    relative_target = target.relative_to(root).as_posix()

    class AuditHandler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - 표준 라이브러리 메서드명
            if self.path in {"/", "/index.html"}:
                self.send_response(302)
                self.send_header("Location", "/" + quote(relative_target))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            super().do_GET()

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    handler = functools.partial(AuditHandler, directory=str(root))
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"로컬 점검 서버: {url}")
    print("종료하려면 Ctrl+C를 누르세요.")
    if open_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="원고 점검 결과를 localhost에서 엽니다.")
    parser.add_argument("output", type=Path, nargs="?", default=Path("output/audit"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    serve_audit(args.output, args.host, args.port, not args.no_open)
