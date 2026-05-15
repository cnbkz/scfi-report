# SCFI 자동보고 시스템 — 코드 에이전트 구현 명세서

## 0. 목적 및 배경

SCM팀에서 매주 금요일 오후 수동으로 수행하던 **SCFI(Shanghai Containerized Freight Index) 지수 확인 → 시황 분석 → 이메일 보고** 업무를 완전 자동화한다.

- 담당자: 백재민 (SCM팀 수석)
- 자동화 전 소요시간: 약 10분/주
- 병목 구간: 알림 수시 확인(매주 금 15:00~17:00 랜덤), 수치 입력, 코멘트 작성

---

## 1. 기술 스택

| 구분 | 선택 기술 | 비고 |
|------|-----------|------|
| UI | Streamlit | 데이터 앱 대시보드 |
| 스크래핑 | requests + BeautifulSoup (또는 Playwright) | surff.kr/indices |
| 뉴스 수집 | requests + feedparser / BeautifulSoup | RSS 또는 HTML 크롤링 |
| 스케줄러 | APScheduler | 매주 금요일 폴링 |
| LLM | Anthropic Claude API (claude-sonnet-4-6) | 시황 코멘트 생성 |
| 이메일 | smtplib (표준 라이브러리) | HTML 본문 발송 |
| 데이터 저장 | CSV (pandas) 또는 SQLite | 주간 이력 관리 |
| 보고서 | Jinja2 HTML 템플릿 | PDF는 weasyprint 옵션 |
| 설정 관리 | python-dotenv (.env 파일) | API 키, SMTP 정보 |
| 언어 | Python 3.11+ | |

---

## 2. 프로젝트 디렉터리 구조

```
scfi_reporter/
├── app.py                  # Streamlit 대시보드 메인
├── scheduler.py            # APScheduler 실행 진입점
├── .env                    # 환경변수 (API 키, SMTP 등) — git 제외
├── requirements.txt
├── data/
│   └── history.csv         # 주간 수집 이력
├── core/
│   ├── scraper.py          # surff.kr 스크래핑
│   ├── news.py             # 해상시황 뉴스 크롤링
│   ├── calculator.py       # 전주 대비 증감 계산
│   ├── llm.py              # Claude API 호출 및 코멘트 생성
│   ├── reporter.py         # 보고서 HTML/PDF 렌더링
│   └── mailer.py           # 이메일 발송 (SMTP)
├── templates/
│   └── report.html         # Jinja2 보고서 템플릿
└── logs/
    └── run.log             # 실행 로그
```

---

## 3. 환경변수 (.env)

```
# LLM
ANTHROPIC_API_KEY=sk-ant-...

# SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=scm-reporter@hansol.com
SMTP_PASS=...
EMAIL_RECIPIENTS=scm-team@hansol.com,logistics@hansol.com

# 설정
POLL_START_HOUR=15
POLL_END_HOUR=17
POLL_INTERVAL_MIN=5
```

---

## 4. 기능 명세

### 4-1. 발표 감지 및 폴링 (`scheduler.py`)

- **실행 조건**: 매주 금요일, `POLL_START_HOUR`~`POLL_END_HOUR` 사이
- **폴링 간격**: `POLL_INTERVAL_MIN` (기본 5분)
- **신규 감지 로직**: 이번 주차(ISO week) 데이터가 `history.csv`에 없으면 신규 발표로 판단
- **트리거**: 신규 감지 시 전체 파이프라인 자동 실행

```python
# 구현 참고
from apscheduler.schedulers.blocking import BlockingScheduler
# 매주 금요일 15:00~17:00 사이 5분마다 실행
scheduler.add_job(poll_and_run, 'cron', day_of_week='fri',
                  hour='15-16', minute='*/5')
```

---

### 4-2. SCFI 지수 및 FAK 운임 데이터 수집 (`core/scraper.py`)

**수집 대상 URL**: `https://surff.kr/indices`

**수집 항목**:

| 변수명 | 항목 | 단위 |
|--------|------|------|
| `scfi_composite` | SCFI 종합지수 | pt |
| `scfi_north_america_east` | 북미 동안 지수 | pt |
| `scfi_north_america_west` | 북미 서안 지수 | pt |
| `fak_na_east` | 북미 동안 FAK 운임 | USD/CBM |
| `fak_na_west` | 북미 서안 FAK 운임 | USD/CBM |
| `fak_australia` | 호주 FAK 운임 | USD/CBM |
| `fak_europe` | 유럽 FAK 운임 | USD/CBM |

**구현 요건**:
- `requests` + `BeautifulSoup`으로 HTML 파싱
- 사이트가 JS 렌더링 필요 시 `playwright` 사용
- 수집 실패(접속 오류, 파싱 오류) 시 `ScraperError` 예외 발생
- 반환 타입: `dict[str, float]`

---

### 4-3. 컨테이너 해상시황 뉴스 크롤링 (`core/news.py`)

**수집 대상 키워드**: `SCFI`, `컨테이너 운임`, `해상 운임`, `container freight`

**수집 매체** (우선순위 순):
1. `https://www.ksg.co.kr` (한국해운신문) — RSS 또는 HTML
2. `https://www.maritimepress.co.kr` (마리타임프레스)
3. `https://www.freightwaves.com` (FreightWaves) — RSS

**구현 요건**:
- 금주(월~금) 발행 기사만 필터링
- 중복 제거 (URL 기준)
- 최대 5건 추출
- 반환 타입: `list[dict]` — 각 항목은 `{"title": str, "url": str, "summary": str, "date": str}`
- 수집 실패 시 빈 리스트 반환 (파이프라인 중단 없이 진행)

---

### 4-4. 데이터 파싱 및 정합성 검증 (`core/scraper.py` 내부)

**검증 항목**:
- 모든 수집값이 `None`이 아닐 것
- 수치가 양수(> 0)일 것
- SCFI 종합지수 범위: 500 ~ 10,000 (이상값 감지)

**실패 시**: `ValidationError` 예외 발생 → 오류 로그 기록 → 담당자 알림 이메일 발송 → 프로세스 종료

---

### 4-5. 전주 대비 계산 (`core/calculator.py`)

**입력**: 금주 수집 `dict`, 전주 데이터 (`history.csv` 마지막 행)

**출력 예시**:
```python
{
  "scfi_composite": {
    "current": 1842,
    "previous": 1795,
    "change": 47,
    "change_pct": 2.62,
    "direction": "▲"   # ▲ / ▼ / -
  },
  ...
}
```

**계산 공식**:
```
change = current - previous
change_pct = (change / previous) * 100
direction = "▲" if change > 0 else ("▼" if change < 0 else "-")
```

---

### 4-6. 이력 데이터 관리 (`data/history.csv`)

**CSV 컬럼**:
```
week_year, week_no, collected_at,
scfi_composite, scfi_na_east, scfi_na_west,
fak_na_east, fak_na_west, fak_australia, fak_europe
```

**요건**:
- 동일 `week_year` + `week_no` 행이 이미 존재하면 저장 건너뜀 (중복 방지)
- `pandas.DataFrame.to_csv(mode='a', header=False)` 방식 사용
- 전주 데이터 조회: 마지막 저장 행 반환

---

### 4-7. AI 시황 분석 코멘트 생성 (`core/llm.py`)

**API**: Anthropic Claude (`claude-sonnet-4-6`)

**프롬프트 구조**:
```
system: "당신은 SCM(공급망관리) 전문가입니다. 컨테이너 해상운임 지수를 분석하여
        실무 담당자에게 전달하는 3~5줄의 간결하고 전문적인 시황 분석 코멘트를 작성합니다.
        수치 변동의 원인과 향후 방향성을 포함해 주세요."

user: """
  금주 SCFI 데이터:
  - 종합지수: {current} pt (전주 {previous} pt, {direction}{change} / {change_pct}%)
  - 북미 동안: ...
  - 북미 서안: ...
  - FAK 운임: ...
  
  금주 해상시황 뉴스:
  1. {news[0].title} - {news[0].summary}
  2. ...
  
  위 데이터를 바탕으로 시황 분석 코멘트를 작성해 주세요.
"""
```

**요건**:
- `max_tokens=500`
- API 실패(timeout, rate limit 등) 시 기본 템플릿 코멘트 반환:
  ```
  "금주 SCFI 종합지수는 {current}pt로 전주 대비 {direction}{change_pct}% 변동하였습니다. 
   지역별 세부 현황은 첨부 데이터를 참고 바랍니다."
  ```

---

### 4-8. 주간 분석보고서 생성 (`core/reporter.py`)

**보고서 섹션 구성**:
1. 헤더: 발행 주차, 기준일, 생성 시각
2. SCFI 지수 현황 테이블
3. FAK 운임 현황 테이블
4. 해상시황 뉴스 요약 (최대 5건)
5. AI 시황 분석 코멘트
6. 푸터: 자동 생성 안내

**Jinja2 템플릿** (`templates/report.html`):
- 증감 방향(▲/▼/-)에 따라 색상 강조: 빨강(▲) / 파랑(▼) / 회색(-)
- 이메일 인라인 CSS 방식 (외부 CSS 미사용)
- 모바일 대응 반응형 레이아웃

---

### 4-9. 이메일 자동 발송 (`core/mailer.py`)

**발송 형식**: HTML 본문 + (선택) PDF 첨부

**구현 요건**:
```python
# 사용 라이브러리: smtplib, email.mime
# TLS 연결: SMTP_SSL 또는 starttls
# 수신자: 환경변수 EMAIL_RECIPIENTS (콤마 구분)
# 제목 형식: "[SCFI 주간시황] 2026년 20주차 (05/09 기준)"
```

**재시도 로직**:
- 최대 3회 재시도
- 재시도 간격: 5초
- 3회 초과 실패 시: 오류 상세 로그 기록 후 종료

---

### 4-10. Streamlit 대시보드 (`app.py`)

**화면 구성**:

```
┌──────────────────────────────────────────────────────────┐
│  📊 SCFI 자동보고 시스템          [수동 수집] [이메일 발송] │
├──────────────────────────────────────────────────────────┤
│  시스템 상태  │  마지막 수집일  │  다음 폴링              │
├──────────────────────────────────────────────────────────┤
│  SCFI 지수 현황 (금주/전주/증감/증감률) 테이블            │
├──────────────────────────────────────────────────────────┤
│  FAK 운임 현황 테이블                                     │
├──────────────────────────────────────────────────────────┤
│  주간 지수 추이 라인 차트 (과거 8주)                      │
├──────────────────────────────────────────────────────────┤
│  수집 뉴스 목록 (제목 + 링크)                             │
├──────────────────────────────────────────────────────────┤
│  AI 코멘트 미리보기  [재생성]  [직접 편집]                │
└──────────────────────────────────────────────────────────┘
```

**요건**:
- `st.cache_data(ttl=300)` 적용
- 수동 수집 버튼: 즉시 파이프라인 실행
- 이메일 발송 버튼: 현재 데이터로 즉시 발송
- AI 코멘트: `st.text_area`로 수정 후 발송 가능

---

### 4-11. 스케줄러 관리 (`scheduler.py`)

```python
from apscheduler.schedulers.blocking import BlockingScheduler

scheduler = BlockingScheduler(timezone='Asia/Seoul')

# 매주 금요일 15:00~16:59 사이 5분마다 폴링
scheduler.add_job(
    poll_and_run,
    trigger='cron',
    day_of_week='fri',
    hour='15-16',
    minute='*/5',
    id='scfi_poll'
)
```

---

### 4-12. 에러 처리 및 재시도

| 에러 유형 | 처리 방법 |
|-----------|----------|
| 사이트 접속 실패 | 10초 간격으로 최대 3회 재시도 → 초과 시 알림 이메일 발송 |
| 데이터 파싱/정합성 실패 | 즉시 오류 로그 → 알림 이메일 → 프로세스 중단 |
| 뉴스 크롤링 실패 | 경고 로그 기록 → 빈 리스트로 파이프라인 계속 진행 |
| LLM API 실패 | 경고 로그 → 기본 템플릿 코멘트로 대체 후 계속 진행 |
| 이메일 발송 실패 | 5초 간격 최대 3회 재시도 → 초과 시 담당자 알림 + 로그 |

---

### 4-13. 설정 관리

모든 설정은 `.env` + `python-dotenv`로 관리한다. 하드코딩 금지.

---

### 4-14. 로그 및 모니터링 (`logs/run.log`)

```python
import logging
logging.basicConfig(
    filename='logs/run.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
```

**로그 필수 기록 항목**:
- 폴링 시작/종료
- 신규 데이터 감지 여부
- 각 단계 성공/실패 및 소요시간
- 이메일 발송 결과 (수신자, 시각)

---

## 5. 전체 파이프라인 흐름

```
[스케줄러] 금요일 15:00~17:00 5분 폴링
    │
    ▼
[신규 데이터 감지?] ──NO──→ 대기 후 재폴링
    │ YES
    ▼
[surff.kr 스크래핑]  ──실패(3회)──→ 오류 알림 → 종료
    │ 성공
    ▼
[파싱 정합성 검증]  ──실패──→ 오류 알림 → 종료
    │ 정상
    ▼
[뉴스 크롤링]  ──실패──→ 빈 리스트로 계속 진행
    │
    ▼
[전주 대비 계산]
    │
    ▼
[이력 CSV 저장]
    │
    ▼
[LLM API 호출]  ──실패──→ 기본 템플릿 코멘트 사용
    │
    ▼
[보고서 HTML 생성]
    │
    ▼
[이메일 발송]  ──실패(3회)──→ 담당자 최종 알림
    │ 성공
    ▼
[발송 이력 저장 + 로그 기록]
    │
    ▼
[Streamlit 대시보드 업데이트]
```

---

## 6. 구현 우선순위 (MVP 순서)

| 단계 | 구현 항목 | 파일 |
|------|----------|------|
| 1 | SCFI 스크래핑 | `core/scraper.py` |
| 2 | 전주 대비 계산 + CSV 저장 | `core/calculator.py` |
| 3 | 뉴스 크롤링 | `core/news.py` |
| 4 | LLM 코멘트 생성 | `core/llm.py` |
| 5 | 보고서 HTML 렌더링 | `core/reporter.py`, `templates/report.html` |
| 6 | 이메일 발송 | `core/mailer.py` |
| 7 | Streamlit 대시보드 | `app.py` |
| 8 | 스케줄러 연동 | `scheduler.py` |

---

## 7. 구현 시 주의사항

1. **surff.kr 구조 확인 필수**: 실제 HTML 구조를 먼저 확인한 후 CSS 셀렉터 작성
2. **이메일 인라인 CSS**: Gmail 등 일부 클라이언트는 외부 CSS를 차단하므로 인라인 스타일만 사용
3. **프롬프트 캐싱**: Anthropic API 호출 시 시스템 프롬프트에 `cache_control` 적용하여 비용 절감
4. **한국 시간대**: 모든 시각 처리는 `Asia/Seoul` 타임존 기준
5. **보안**: `.env` 파일은 `.gitignore`에 반드시 포함, API 키 하드코딩 절대 금지
6. **Streamlit 재실행**: `st.rerun()`으로 수동 새로고침 구현

---

## 8. requirements.txt

```
streamlit>=1.35.0
requests>=2.31.0
beautifulsoup4>=4.12.0
playwright>=1.40.0
feedparser>=6.0.0
pandas>=2.0.0
apscheduler>=3.10.0
anthropic>=0.25.0
python-dotenv>=1.0.0
jinja2>=3.1.0
plotly>=5.20.0
```
