# mini-codex 실행 방법

[![tests](https://github.com/deploy103/mini-codex/actions/workflows/tests.yml/badge.svg)](https://github.com/deploy103/mini-codex/actions/workflows/tests.yml)

`mini-codex`는 지금 폴더의 코드를 읽고, `.env`에 있는 APIM/OpenAI 키로 모델에게 작업을 요청한 뒤, 모델이 제안한 파일 수정과 검증 명령을 로컬에서 실행하는 작은 코딩 에이전트입니다.

호스팅된 OpenAI Codex 자체는 아니고, Codex처럼 동작하도록 만든 로컬 CLI 도구입니다.

## 가장 쉬운 실행

이제 가상환경을 직접 만들거나 켤 필요 없이 프로젝트 폴더에서 바로 실행합니다.

```bash
cd path/to/mini-codex
./codex "README를 읽고 사용법을 더 쉽게 고쳐줘"
```

`./codex`는 `.venv`가 없으면 자동으로 만들고, 필요한 패키지가 없으면 자동으로 설치한 뒤 `mini_codex`를 실행합니다.

PowerShell에서 실행한다면 같은 방식으로 `./codex.ps1`을 쓰면 됩니다. WSL의 `.venv`와 충돌하지 않도록 PowerShell은 `.venv-win`을 자동으로 사용합니다.

```powershell
./codex.ps1 "README를 읽고 사용법을 더 쉽게 고쳐줘"
```

아래 별칭들도 PowerShell에서는 `./codex.ps1 config`처럼 동일하게 사용할 수 있습니다.
직접 설치한 `mini-codex` 명령에서도 `mini-codex config`, `mini-codex logs`, `mini-codex test`처럼 같은 별칭을 사용할 수 있습니다.

설정 확인도 한 줄이면 됩니다.

```bash
./codex --show-config
```

짧은 별칭도 있습니다.

```bash
./codex config              # 설정 확인
./codex gui                 # 창으로 실행
./codex doctor              # 로컬 실행 준비 상태 점검
./codex dry "작업 내용"     # 파일 수정/명령 실행 없이 계획만 확인
./codex permission list     # shell 명령 권한 프로필 목록
./codex test                # pytest 실행
./codex logs                # 최근 실행 기록 목록
./codex last                # 마지막 실행 기록 출력
./codex status              # workspace/git/기록/권한 상태 확인
./codex diff                # staged/unstaged 변경 요약 확인
```

작업 중에는 다음 흐름이 화면에 표시됩니다.

- 어떤 활동을 하는지
- 모델이 어떤 순서로 작업할지 계획한 활동 단계
- 모델이 어떤 파일 수정과 명령 실행을 계획했는지
- 실제 실행한 명령어, 실행 이유, timeout
- 명령의 exit code, stdout, stderr
- 최종 성공/실패 결과

파일 수정은 전체 파일 교체뿐 아니라 단일 파일 unified diff patch도 처리할 수 있습니다.

검증 명령의 stdout/stderr는 명령이 끝날 때까지 기다리지 않고 실행 중에도 바로 표시됩니다.

실행 기록은 기본으로 `.mini_codex/runs/` 아래 Markdown 파일로 남습니다. 설정된 API 키가 명령 출력에 섞여 나오면 화면, 기록 파일, 다음 repair 요청에 `[redacted]`로 가려서 전달합니다.

화면에는 대략 이런 식으로 보입니다.

```text
[activity] Preparing local coding run
[activity] Scanning workspace context
result: collected 42,000 bytes of workspace context from 24 files
[activity] Plan received
Planned activities:
  - inspect files - understand the current structure
Files to change:
  - update: README.md
Commands to run:
  - .venv/bin/python -m pytest -q (verify the change)
update: README.md (changed, +12 -2)
result: 1 changed, 0 unchanged, +12 -2
$ .venv/bin/python -m pytest -q
  why: verify the change
  timeout: 120s
result: passed in 4.42s
result: 1 passed, 0 failed, 0 skipped
[activity] Finished: Task completed.
Elapsed: 8.31s
```

## 창으로 실행

터미널이 답답하면 데스크톱 창을 열어서 계속 작업할 수 있습니다.

```bash
./codex gui
```

PowerShell에서는:

```powershell
./codex.ps1 gui
```

창에서는 작업 내용을 입력하고 `Run`을 누르면 됩니다. 실행은 백그라운드 프로세스로 돌아가서 모델 응답과 명령 출력이 늦어져도 창은 계속 반응합니다. `Dry run`, `No commands`, `Config`, `Doctor`, 최근 transcript 열기도 같은 창에서 사용할 수 있습니다.

WSL처럼 현재 Python에 `tkinter`가 없으면 자동으로 로컬 브라우저 창을 엽니다. 터미널에는 `mini-codex GUI: http://127.0.0.1:.../` 주소가 표시됩니다.

## 선택: 직접 설치해서 실행하기

일반 사용자는 이 섹션을 건너뛰고 `./codex "작업 내용"`만 쓰면 됩니다. 아래는 자동 래퍼를 쓰지 않고 직접 설치하고 싶을 때만 필요합니다.

프로젝트 폴더에서 실행합니다.

```bash
cd path/to/mini-codex
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

이미 `.venv`가 만들어져 있으면 다음부터는 설치를 다시 하지 않아도 됩니다.

## 선택: 수동 실행할 때 가상환경 켜기

직접 설치 방식을 선택했다면 새 터미널을 열 때 먼저 가상환경을 켭니다.

```bash
cd path/to/mini-codex
source .venv/bin/activate
```

프롬프트 앞에 `(.venv)`가 보이면 준비된 상태입니다.

## .env 확인

현재 프로젝트는 이 형식의 `.env`를 사용합니다.

```env
APIM_BASE_URL=<your-apim-base-url>/foundry
APIM_KEY=...
CHAT_MODEL=gpt-5.4
EMBEDDING_MODEL=...
VISION_MODEL=...
```

중요한 점:

- `APIM_BASE_URL`은 모델명 없이 `/foundry`까지만 넣습니다.
- 프로그램이 자동으로 `{APIM_BASE_URL}/{CHAT_MODEL}/` 형태로 호출합니다.
- 인증은 `api-key` 헤더로 처리합니다.
- `.env`는 절대 Git에 올리지 마세요.

설정이 잘 읽히는지 확인하려면:

```bash
./codex --show-config
```

정상이라면 키 값은 숨긴 채 모델명, API mode, timeout 등이 출력됩니다.

## 기본 실행

작업을 문자열로 넘기면 됩니다.

```bash
./codex "간단한 계산기 CLI를 만들어줘"
```

예시:

```bash
./codex "README를 읽고 사용법을 더 쉽게 고쳐줘"
./codex "테스트를 추가하고 pytest가 통과하게 수정해줘"
./codex "현재 코드 구조를 보고 버그가 있으면 고쳐줘"
```

명령어가 길면 따옴표 안에 자연어로 길게 써도 됩니다.

마지막 실행 기록을 참고해서 이어가려면:

```bash
./codex --resume-last "방금 실패한 부분을 이어서 고쳐줘"
```

## 안전하게 미리보기

실제 파일을 수정하지 않고 모델이 무엇을 하려는지만 보고 싶으면:

```bash
./codex --dry-run "이 프로젝트를 리팩터링해줘"
```

파일 수정은 허용하되, 모델이 제안한 shell 명령은 실행하지 않으려면:

```bash
./codex --no-commands "코드만 수정해줘"
```

가장 안전하게 확인만 하려면 둘 다 사용합니다.

```bash
./codex --dry-run --no-commands "현재 프로젝트 문제점을 확인해줘"
```

명령을 실행하기 전에 매번 확인받으려면 approval mode를 켭니다.

```bash
./codex --approval-mode always "테스트를 고치고 실행해줘"
```

터미널 입력을 받을 수 없는 환경에서는 명령이 실행되지 않고 skipped로 기록됩니다.

## shell 명령 권한 프로필

모델이 제안한 shell 명령은 기본 위험 명령 차단 목록을 항상 통과해야 합니다. 여기에 permission 프로필을 추가로 적용해, 실행 가능한 명령 범위를 더 좁힐 수 있습니다.

기본 제공 프로필은 세 가지입니다.

- `readonly`: shell 명령을 모두 건너뜁니다.
- `restricted`: pytest, lint, build, read-only git 명령처럼 흔한 검증 명령만 허용합니다.
- `trusted`: 기본 위험 명령 차단 목록 외에는 shell 명령을 허용합니다.

프로필 목록을 봅니다.

```bash
./codex permission list
```

새 프로필은 `.mini_codex/permissions/<name>.json`에 저장됩니다. `/permission/new` 형태도 지원합니다.

```bash
./codex /permission/new team-safe
./codex permission new local-trusted --template trusted
./codex permission show team-safe
```

작업 실행 때 프로필을 고릅니다.

```bash
./codex --permission restricted "테스트를 추가하고 실행해줘"
./codex --permission team-safe "현재 버그를 고쳐줘"
```

대화형 모드에서도 `/permission/new team-safe`, `/permission list`처럼 입력할 수 있습니다.

## API 모드와 타임아웃 지정

보통은 `auto`로 충분합니다.

```bash
./codex --api-mode auto "작업 내용"
```

Responses API만 강제로 쓰려면:

```bash
./codex --api-mode responses "작업 내용"
```

응답이 오래 걸릴 때 timeout을 늘리거나 줄일 수 있습니다.

```bash
./codex --request-timeout 120 "작업 내용"
```

`.env`에 기본 timeout을 넣을 수도 있습니다.

```env
APIM_TIMEOUT=180
```

## 실행 결과 확인

작업 후 실행 기록을 확인합니다.

```bash
./codex last
```

현재 workspace 상태를 API 키 없이 확인합니다.

```bash
./codex status
```

git 변경 요약만 빠르게 보려면:

```bash
./codex diff
```

이 폴더가 git 저장소라면 변경된 파일을 확인합니다.

```bash
git diff
```

테스트를 실행합니다.

```bash
./codex test
```

또는 가상환경 명령을 직접 써도 됩니다.

```bash
.venv/bin/python -m pytest -q
```

## 자주 쓰는 명령 모음

```bash
./codex config
./codex doctor
./codex dry "현재 상태만 점검해줘"
./codex status
./codex diff
./codex "원하는 작업을 여기에 적기"
./codex test
```

## 문제가 생길 때

`Missing API key`가 나오면 `.env`에 `APIM_KEY`가 있는지 확인합니다.

`Resource not found` 또는 `404`가 나오면 `APIM_BASE_URL`이 `/foundry`까지만 들어갔는지 확인합니다.

모델 접근 오류가 나오면 `CHAT_MODEL` 값을 실제 사용 가능한 모델명으로 바꿉니다.

응답이 너무 오래 걸리면:

```bash
./codex --request-timeout 60 "짧은 작업"
```

설정을 다시 확인합니다.

```bash
./codex config
```
