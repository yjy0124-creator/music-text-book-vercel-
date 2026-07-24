#!/usr/bin/env bash
# Oracle Cloud "Always Free" VM(Ubuntu 기준)에 이 앱을 Docker로 띄우는 설정 스크립트.
# VM에 SSH로 접속한 뒤 이 스크립트를 실행한다:
#   curl -fsSL https://raw.githubusercontent.com/yjy0124-creator/music-text-book-vercel-/main/oracle_vm_setup.sh | bash
# 또는 저장소를 clone한 뒤 직접 실행해도 된다.
set -euo pipefail

REPO_URL="https://github.com/yjy0124-creator/music-text-book-vercel-.git"
APP_DIR="$HOME/music-textbook"

echo "== Docker 설치 =="
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  sudo usermod -aG docker "$USER"
  echo "Docker 설치 완료 (그룹 적용을 위해 재로그인이 필요할 수 있음)"
fi

echo "== 저장소 내려받기 =="
if [ -d "$APP_DIR" ]; then
  git -C "$APP_DIR" pull
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "== OS 방화벽(ufw)에 8780 포트 허용 =="
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow 8780/tcp || true
fi

echo "== 컨테이너 빌드 및 실행 =="
cd "$APP_DIR"
if [ ! -f .env ]; then
  echo "ANTHROPIC_API_KEY=" > .env
  echo ".env 파일을 만들었습니다. 필요하면 $APP_DIR/.env 에 키를 채워 넣고 다시 실행하세요."
fi
sudo docker compose up -d --build

echo
echo "완료. VM의 공인 IP:8780 으로 접속되는지 확인하세요 (예: http://<VM_PUBLIC_IP>:8780/)."
echo "단, Oracle Cloud 콘솔의 서브넷 보안 목록(Security List)에서도 8780/TCP 수신 규칙을 추가해야 외부에서 접속됩니다."
