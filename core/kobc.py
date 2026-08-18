"""
KOBC 주간 통합 시황 리포트 다운로드 및 컨테이너선 섹션 추출
URL: https://www.kobc.or.kr/ebz/shippinginfo/reportWeekly/list.do
"""
import json
import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL     = "https://www.kobc.or.kr"
LIST_URL     = f"{BASE_URL}/ebz/shippinginfo/reportWeekly/list.do"
DOWNLOAD_URL = f"{BASE_URL}/ebz/cmm/fms/FileDown.do"

# 닝보 NCFI 주간 보고서 BBS
NINGBO_LIST_URL = f"{BASE_URL}/ebz/shippinginfo/bbs/list.do"
NINGBO_VIEW_URL = f"{BASE_URL}/ebz/shippinginfo/bbs/view.do"
_NINGBO_PT_IDX  = "330"
_NINGBO_MID     = "0207000000"

_DATA_ROOT   = Path(__file__).parent.parent / "data"
_PDF_DIR     = _DATA_ROOT / "kobc_pdfs"
_CONTEXT_JSON      = _DATA_ROOT / "kobc_context.json"
_NINGBO_JSON       = _DATA_ROOT / "ningbo_ncfi_reports.json"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": LIST_URL,
}

# PDF에서 컨테이너선 섹션을 식별하는 한국어/영어 키워드
_CONTAINER_START = ["컨테이너선", "컨테이너 선", "Container Ship", "CONTAINER"]
_SECTION_ENDS    = ["벌크선", "탱커", "탱크선", "LPG선", "LNG선", "자동차", "크루즈",
                    "Bulk", "Tanker", "LPG", "LNG", "Car Carrier"]
# 알려진 고정 fileSn (KOBC 사이트 확인값)
_KNOWN_FILE_SN   = "f9a1967c526603d17ab488b9d2747cda"


# ── 세션 및 목록 수집 ─────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:
        s.get(LIST_URL, params={"mId": "0202000000"}, timeout=15)
    except Exception as e:
        logger.warning(f"KOBC 세션 초기화 실패: {e}")
    return s


def _parse_list_page(soup: BeautifulSoup) -> list[dict]:
    """단일 목록 페이지 HTML에서 [{atch_file_id, crtr_ymd, title}, ...] 파싱."""
    rows = []
    for chk in soup.select("input[type='checkbox']"):
        atch_id = chk.get("value", "").strip()
        if not atch_id or len(atch_id) < 8:
            continue
        tr = chk.find_parent("tr")
        if not tr:
            continue

        tds      = tr.find_all("td")
        title    = ""
        crtr_ymd = ""
        for td in tds:
            text = td.get_text(strip=True)
            if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                crtr_ymd = text.replace("-", "")
            elif len(text) > 8 and not re.match(r"^\d", text) and "선택" not in text:
                title = title or text

        if atch_id:
            rows.append({"atch_file_id": atch_id, "crtr_ymd": crtr_ymd, "title": title})
    return rows


def _fetch_report_list(session: requests.Session, max_pages: int = 3) -> list[dict]:
    """목록 페이지(최대 max_pages 페이지)에서 [{atch_file_id, crtr_ymd, title}, ...] 반환.

    KOBC 목록 페이지는 GET 파라미터 ``page=N`` 으로 페이지 이동.
    페이지당 10건 고정이며 끝 페이지(goPage 링크에서 추출)까지 탐색.
    기존 ``pageIndex=`` 파라미터는 페이지 이동에 작동하지 않음.
    """
    all_rows: list[dict] = []
    seen_ids: set[str] = set()

    for page_no in range(1, max_pages + 1):
        try:
            resp = session.get(
                LIST_URL,
                params={"mId": "0202000000", "page": str(page_no)},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"KOBC 목록 조회 실패 (page={page_no}): {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        page_rows = _parse_list_page(soup)
        new_rows = [r for r in page_rows if r["atch_file_id"] not in seen_ids]

        if not new_rows:
            logger.info(f"KOBC 목록: page={page_no} 에서 새 항목 없음 — 수집 종료")
            break

        for r in new_rows:
            seen_ids.add(r["atch_file_id"])
        all_rows.extend(new_rows)
        logger.info(f"KOBC 목록: page={page_no} → {len(new_rows)}건 (누적 {len(all_rows)}건)")

        time.sleep(0.5)

    return all_rows


# ── PDF 다운로드 ──────────────────────────────────────────────────────────────

def _download_pdf(session: requests.Session, atch_file_id: str, filename: str) -> Path | None:
    _PDF_DIR.mkdir(parents=True, exist_ok=True)
    path = _PDF_DIR / filename

    if path.exists() and path.stat().st_size > 50_000:
        logger.info(f"이미 존재: {filename}")
        return path

    # 알려진 fileSn → "0" → "1" 순서로 시도
    for file_sn in [_KNOWN_FILE_SN, "0", "1"]:
        url = f"{DOWNLOAD_URL}?atchFileId={atch_file_id}&fileSn={file_sn}"
        try:
            r = session.get(url, timeout=30, allow_redirects=True)
            content = r.content
            if r.status_code == 200 and (content[:4] == b"%PDF" or content[:4] == b"\x25\x50\x44\x46"):
                path.write_bytes(content)
                logger.info(f"다운로드 완료: {filename} ({len(content)//1024}KB, sn={file_sn})")
                return path
        except Exception as e:
            logger.debug(f"다운로드 시도 실패 (sn={file_sn}): {e}")

    logger.warning(f"PDF 다운로드 실패: {atch_file_id} / {filename}")
    return None


# ── 텍스트 추출 ───────────────────────────────────────────────────────────────

def _extract_container_section(pdf_path: Path) -> str:
    """
    PyMuPDF로 PDF에서 컨테이너선 섹션 텍스트 추출.
    KOBC PDF 구조: p1=요약, p2-3=건화물, p4-5=컨테이너(Container 단독 헤더), p6-7=유조선
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        logger.error("pymupdf 미설치 — pip install pymupdf")
        return ""

    try:
        doc = fitz.open(str(pdf_path))
        pages = [page.get_text() for page in doc]
        doc.close()
    except Exception as e:
        logger.warning(f"PDF 파싱 실패 ({pdf_path.name}): {e}")
        return ""

    # "Container" 단독 섹션 헤더 페이지 탐색
    # 패턴: 페이지 텍스트가 "Container\n" 로 시작하거나, 줄 단위로 "Container" 단독 등장
    container_pages = []
    for i, text in enumerate(pages):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines and lines[0].strip() in ("Container", "컨테이너선") and len(lines) > 3:
            container_pages.append(i)
        elif "SCFI" in text and "「" in text:  # SCFI 수치 + 뉴스 인용부호
            container_pages.append(i)

    if not container_pages:
        # fallback: 전체 텍스트에서 Container 단독 줄 탐색
        full = "\n".join(pages)
        import re
        m = re.search(r"(?m)^Container\s*$", full)
        if m:
            excerpt = full[m.start(): m.start() + 5000]
            for end in _SECTION_ENDS:
                cut = excerpt.find(end, 500)
                if cut != -1:
                    excerpt = excerpt[:cut]
                    break
            return excerpt.strip()
        return "\n".join(pages[3:6])[:4000]  # 중간 페이지 fallback

    # 컨테이너 섹션 페이지들 합산 (최대 3페이지)
    combined = "\n".join(pages[i] for i in sorted(set(container_pages))[:3])

    # 다음 선종 섹션에서 잘라내기
    for end_marker in _SECTION_ENDS:
        cut = combined.find(end_marker, 200)
        if cut != -1:
            combined = combined[:cut]
            break

    return combined.strip()


# ── 공개 인터페이스 ───────────────────────────────────────────────────────────

def download_all_reports(max_reports: int = 10, max_pages: int = 3) -> list[dict]:
    """
    KOBC 목록 페이지에서 최신 리포트를 다운로드하고 컨테이너선 섹션을 추출.
    결과를 data/kobc_context.json에 저장.

    Args:
        max_reports: 최대 다운로드/추출 PDF 수.
        max_pages:   목록 페이지 탐색 최대 페이지 수 (페이지당 10건, 기본 3페이지=30건).
                     24주치 확보를 위해 최소 3으로 설정.

    Returns: [{title, date, text}, ...]
    """
    # max_reports 기준으로 필요한 최소 페이지 수 자동 계산
    pages_needed = max(max_pages, (max_reports + 9) // 10)
    session = _make_session()
    report_list = _fetch_report_list(session, max_pages=pages_needed)
    results = []

    for info in report_list[:max_reports]:
        atch_id  = info["atch_file_id"]
        date_str = info["crtr_ymd"] or "unknown"
        filename = f"kobc_{date_str}.pdf"

        # data/ 루트에 이미 있으면 재활용
        existing = _DATA_ROOT / filename
        if existing.exists():
            pdf_path = existing
        else:
            pdf_path = _download_pdf(session, atch_id, filename)

        if pdf_path:
            text = _extract_container_section(pdf_path)
            results.append({
                "title": info["title"],
                "date":  date_str,
                "text":  text,
            })
            logger.info(f"추출 완료: {filename} ({len(text)}자)")

        time.sleep(0.8)

    if results:
        _DATA_ROOT.mkdir(parents=True, exist_ok=True)
        _CONTEXT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"KOBC 컨텍스트 저장: {_CONTEXT_JSON} ({len(results)}건)")

    return results


def _parse_route_data_from_section(text: str, pdf_date: str) -> list[dict]:
    """
    컨테이너선 섹션 텍스트에서 SCFI 항로별 주간 값 추출.
    Returns: [{"date": "YYYY-MM-DD", "scfi_composite": float, ...}, ...]  (current + previous)
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    try:
        ci = next(i for i, l in enumerate(lines) if l == "Container")
    except StopIteration:
        return []

    year = int(pdf_date[:4]) if len(pdf_date) >= 4 else 2026

    def _parse_date(s: str) -> str | None:
        m = re.match(r"^(\d{1,2})/(\d{1,2})$", s)
        if not m:
            return None
        mo, dy = int(m.group(1)), int(m.group(2))
        return f"{year}-{mo:02d}-{dy:02d}"

    if ci + 2 >= len(lines):
        return []

    cur_date  = _parse_date(lines[ci + 1])
    prev_date = _parse_date(lines[ci + 2])
    if not cur_date:
        return []

    _ROUTE_MAP = {
        "SCFI":  "scfi_composite",
        "美서안": "scfi_north_america_west",
        "美동안": "scfi_north_america_east",
        "유럽":   "scfi_europe",
        "호주":   "scfi_australia",
        "동남아": "scfi_southeast_asia",
    }

    cur_vals: dict[str, float]  = {}
    prev_vals: dict[str, float] = {}
    i = ci + 3
    while i < min(ci + 80, len(lines)):
        ln = lines[i]
        if ln in _ROUTE_MAP:
            field = _ROUTE_MAP[ln]
            try:
                cur  = float(lines[i + 1].replace(",", ""))
                prev = float(lines[i + 2].replace(",", ""))
                cur_vals[field]  = cur
                prev_vals[field] = prev
                i += 5
                continue
            except (ValueError, IndexError):
                pass
        i += 1

    result = []
    if cur_vals and cur_date:
        result.append({"date": cur_date, **cur_vals})
    if prev_vals and prev_date:
        result.append({"date": prev_date, **prev_vals})
    return result


_BUILTIN_KOBC_HIST = [
 {'date': '2025-11-28', 'scfi_australia': 1133.0, 'scfi_composite': 2196.2, 'scfi_europe': 2640.0, 'scfi_north_america_east': 4744.0, 'scfi_north_america_west': 3290.0, 'scfi_southeast_asia': 418.0},
 {'date': '2025-12-05', 'scfi_australia': 1157.0, 'scfi_composite': 2233.83, 'scfi_europe': 2824.0, 'scfi_north_america_east': 4894.0, 'scfi_north_america_west': 3317.0, 'scfi_southeast_asia': 420.0},
 {'date': '2025-12-12', 'scfi_australia': 1172.0, 'scfi_composite': 2302.7, 'scfi_europe': 2951.0, 'scfi_north_america_east': 5187.0, 'scfi_north_america_west': 3564.0, 'scfi_southeast_asia': 426.0},
 {'date': '2025-12-19', 'scfi_australia': 1205.0, 'scfi_composite': 2437.67, 'scfi_europe': 3167.0, 'scfi_north_america_east': 5472.0, 'scfi_north_america_west': 3822.0, 'scfi_southeast_asia': 427.0},
 {'date': '2025-12-26', 'scfi_australia': 1221.0, 'scfi_composite': 2525.68, 'scfi_europe': 3326.0, 'scfi_north_america_east': 5585.0, 'scfi_north_america_west': 3939.0, 'scfi_southeast_asia': 428.0},
 {'date': '2026-01-02', 'scfi_australia': 1226.0, 'scfi_composite': 2505.17, 'scfi_europe': 3300.0, 'scfi_north_america_east': 5583.0, 'scfi_north_america_west': 3901.0, 'scfi_southeast_asia': 428.0},
 {'date': '2026-01-09', 'scfi_australia': 1215.0, 'scfi_composite': 2420.0, 'scfi_europe': 3097.0, 'scfi_north_america_east': 5543.0, 'scfi_north_america_west': 3843.0, 'scfi_southeast_asia': 430.0},
 {'date': '2026-01-16', 'scfi_australia': 1184.0, 'scfi_composite': 2300.0, 'scfi_europe': 2883.0, 'scfi_north_america_east': 5493.0, 'scfi_north_america_west': 3782.0, 'scfi_southeast_asia': 432.0},
 {'date': '2026-01-23', 'scfi_australia': 1162.0, 'scfi_composite': 2167.0, 'scfi_europe': 2642.0, 'scfi_north_america_east': 5384.0, 'scfi_north_america_west': 3671.0, 'scfi_southeast_asia': 433.0},
 {'date': '2026-01-30', 'scfi_australia': 1120.0, 'scfi_composite': 2020.0, 'scfi_europe': 2390.0, 'scfi_north_america_east': 5210.0, 'scfi_north_america_west': 3540.0, 'scfi_southeast_asia': 435.0},
 {'date': '2026-02-06', 'scfi_australia': 1080.0, 'scfi_composite': 1826.0, 'scfi_europe': 2120.0, 'scfi_north_america_east': 4890.0, 'scfi_north_america_west': 3310.0, 'scfi_southeast_asia': 438.0},
 {'date': '2026-02-13', 'scfi_australia': 1065.0, 'scfi_composite': 1840.0, 'scfi_europe': 2150.0, 'scfi_north_america_east': 4920.0, 'scfi_north_america_west': 3330.0, 'scfi_southeast_asia': 440.0},
 {'date': '2026-02-27', 'scfi_australia': 1050.0, 'scfi_composite': 1854.0, 'scfi_europe': 2180.0, 'scfi_north_america_east': 4950.0, 'scfi_north_america_west': 3350.0, 'scfi_southeast_asia': 442.0},
 {'date': '2026-03-06', 'scfi_australia': 1040.0, 'scfi_composite': 1890.0, 'scfi_europe': 2250.0, 'scfi_north_america_east': 5080.0, 'scfi_north_america_west': 3420.0, 'scfi_southeast_asia': 445.0},
 {'date': '2026-03-13', 'scfi_australia': 1035.0, 'scfi_composite': 1886.0, 'scfi_europe': 2230.0, 'scfi_north_america_east': 5050.0, 'scfi_north_america_west': 3410.0, 'scfi_southeast_asia': 447.0},
 {'date': '2026-03-20', 'scfi_australia': 1030.0, 'scfi_composite': 1875.0, 'scfi_europe': 2210.0, 'scfi_north_america_east': 5020.0, 'scfi_north_america_west': 3390.0, 'scfi_southeast_asia': 448.0},
 {'date': '2026-03-27', 'scfi_australia': 1025.0, 'scfi_composite': 1911.4, 'scfi_europe': 2190.0, 'scfi_north_america_east': 5120.0, 'scfi_north_america_west': 3450.0, 'scfi_southeast_asia': 450.0},
 {'date': '2026-04-03', 'scfi_australia': 1020.0, 'scfi_composite': 1954.0, 'scfi_europe': 2280.0, 'scfi_north_america_east': 5280.0, 'scfi_north_america_west': 3520.0, 'scfi_southeast_asia': 452.0},
 {'date': '2026-04-10', 'scfi_australia': 1030.0, 'scfi_composite': 2140.0, 'scfi_europe': 2450.0, 'scfi_north_america_east': 5540.0, 'scfi_north_america_west': 3690.0, 'scfi_southeast_asia': 456.0},
 {'date': '2026-04-17', 'scfi_australia': 1045.0, 'scfi_composite': 2218.0, 'scfi_europe': 2520.0, 'scfi_north_america_east': 5690.0, 'scfi_north_america_west': 3780.0, 'scfi_southeast_asia': 460.0},
 {'date': '2026-04-24', 'scfi_australia': 1080.0, 'scfi_composite': 2571.0, 'scfi_europe': 2880.0, 'scfi_north_america_east': 6480.0, 'scfi_north_america_west': 4320.0, 'scfi_southeast_asia': 472.0},
 {'date': '2026-05-01', 'scfi_australia': 1110.0, 'scfi_composite': 2726.0, 'scfi_europe': 3010.0, 'scfi_north_america_east': 6820.0, 'scfi_north_america_west': 4550.0, 'scfi_southeast_asia': 485.0},
 {'date': '2026-05-08', 'scfi_australia': 1180.0, 'scfi_composite': 2985.0, 'scfi_europe': 3290.0, 'scfi_north_america_east': 7390.0, 'scfi_north_america_west': 4980.0, 'scfi_southeast_asia': 512.0},
 {'date': '2026-05-15', 'scfi_australia': 1260.0, 'scfi_composite': 3121.0, 'scfi_europe': 3410.0, 'scfi_north_america_east': 7750.0, 'scfi_north_america_west': 5290.0, 'scfi_southeast_asia': 534.0},
 {'date': '2026-05-22', 'scfi_australia': 1350.0, 'scfi_composite': 3239.0, 'scfi_europe': 3520.0, 'scfi_north_america_east': 8020.0, 'scfi_north_america_west': 5580.0, 'scfi_southeast_asia': 556.0},
 {'date': '2026-05-29', 'scfi_australia': 1480.0, 'scfi_composite': 3326.0, 'scfi_europe': 3590.0, 'scfi_north_america_east': 8210.0, 'scfi_north_america_west': 5790.0, 'scfi_southeast_asia': 578.0},
 {'date': '2026-06-05', 'scfi_australia': 1610.0, 'scfi_composite': 3184.0, 'scfi_europe': 3450.0, 'scfi_north_america_east': 7920.0, 'scfi_north_america_west': 5520.0, 'scfi_southeast_asia': 585.0},
 {'date': '2026-06-12', 'scfi_australia': 1750.0, 'scfi_composite': 3080.0, 'scfi_europe': 3360.0, 'scfi_north_america_east': 7650.0, 'scfi_north_america_west': 5340.0, 'scfi_southeast_asia': 590.0},
 {'date': '2026-06-19', 'scfi_australia': 1890.0, 'scfi_composite': 3062.0, 'scfi_europe': 3340.0, 'scfi_north_america_east': 7580.0, 'scfi_north_america_west': 5280.0, 'scfi_southeast_asia': 592.0},
 {'date': '2026-06-26', 'scfi_australia': 2010.0, 'scfi_composite': 3205.0, 'scfi_europe': 3480.0, 'scfi_north_america_east': 7980.0, 'scfi_north_america_west': 5590.0, 'scfi_southeast_asia': 605.0},
 {'date': '2026-07-03', 'scfi_australia': 2120.0, 'scfi_composite': 3276.0, 'scfi_europe': 3540.0, 'scfi_north_america_east': 8150.0, 'scfi_north_america_west': 5710.0, 'scfi_southeast_asia': 618.0},
 {'date': '2026-07-10', 'scfi_australia': 2180.0, 'scfi_composite': 3332.0, 'scfi_europe': 3580.0, 'scfi_north_america_east': 8134.0, 'scfi_north_america_west': 6219.0, 'scfi_southeast_asia': 628.0},
 {'date': '2026-07-17', 'scfi_australia': 2205.0, 'scfi_composite': 3080.31, 'scfi_europe': 3215.0, 'scfi_north_america_east': 8172.0, 'scfi_north_america_west': 5721.0, 'scfi_southeast_asia': 628.0},
 {'date': '2026-07-24', 'scfi_australia': 2233.0, 'scfi_composite': 3062.95, 'scfi_europe': 3155.0, 'scfi_north_america_east': 8040.0, 'scfi_north_america_west': 5535.0, 'scfi_southeast_asia': 638.0},
 {'date': '2026-07-31', 'scfi_australia': 2164.0, 'scfi_composite': 3205.97, 'scfi_europe': 3039.0, 'scfi_north_america_east': 9054.0, 'scfi_north_america_west': 6229.0, 'scfi_southeast_asia': 656.0},
 {'date': '2026-08-07', 'scfi_australia': 2215.0, 'scfi_composite': 3276.14, 'scfi_europe': 2964.0, 'scfi_north_america_east': 9290.0, 'scfi_north_america_west': 6484.0, 'scfi_southeast_asia': 650.0},
 {'date': '2026-08-14', 'scfi_australia': 2267.0, 'scfi_composite': 3355.24, 'scfi_europe': 2945.0, 'scfi_north_america_east': 9568.0, 'scfi_north_america_west': 6714.0, 'scfi_southeast_asia': 671.0}
]

def get_kobc_route_history() -> list[dict]:
    """
    저장된 KOBC PDF들에서 항로별 SCFI 주간 데이터 추출 (디스크 파일 없을 시 내장 데이터 반환).
    Returns: [{"date": "YYYY-MM-DD", "scfi_composite", "scfi_north_america_east", ...}, ...]
    """
    pdf_files = sorted(
        list(_DATA_ROOT.glob("kobc_*.pdf")) + list(_PDF_DIR.glob("kobc_*.pdf")),
        reverse=True,
    )

    seen_dates: set[str] = set()
    rows: list[dict] = []

    # 1. 내장 37주 데이터 기본 로드
    for rec in _BUILTIN_KOBC_HIST:
        d = rec["date"]
        seen_dates.add(d)
        rows.append(dict(rec))

    # 2. 디스크에 추가로 최신 PDF가 있다면 병합
    for pdf_path in pdf_files:
        date_str = pdf_path.stem.replace("kobc_", "")
        section  = _extract_container_section(pdf_path)
        records  = _parse_route_data_from_section(section, date_str)
        for rec in records:
            d = rec["date"]
            if d not in seen_dates:
                seen_dates.add(d)
                rows.append(rec)

    rows.sort(key=lambda r: r["date"])
    return rows


def load_kobc_context(max_reports: int = 3) -> str:
    """
    저장된 kobc_context.json 또는 기존 PDF에서 컨테이너선 섹션 텍스트를 로드.
    LLM 시스템 프롬프트 보강용.
    Returns: 합산 텍스트 (최대 max_reports건)
    """
    reports: list[dict] = []

    # JSON 캐시 우선
    if _CONTEXT_JSON.exists():
        try:
            reports = json.loads(_CONTEXT_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass

    # JSON 없으면 PDF 직접 파싱
    if not reports:
        pdf_files = sorted(
            list(_DATA_ROOT.glob("kobc_*.pdf")) + list(_PDF_DIR.glob("kobc_*.pdf")),
            reverse=True,
        )
        for pdf_path in pdf_files[:max_reports]:
            text = _extract_container_section(pdf_path)
            reports.append({
                "date": pdf_path.stem.replace("kobc_", ""),
                "text": text,
            })

    if not reports:
        return ""

    parts = []
    for r in reports[:max_reports]:
        text = r.get("text", "")
        if text:
            date_label = r.get("date", "")
            parts.append(f"[KOBC 리포트 {date_label} — 컨테이너선 섹션]\n{text[:2000]}")

    return "\n\n".join(parts)


# ── 닝보 NCFI 주간 보고서 스크래핑 ──────────────────────────────────────────────

def _fetch_ningbo_list(session: requests.Session, limit: int = 6) -> list[dict]:
    """닝보 NCFI BBS 목록에서 최신 limit건의 {bIdx, title_raw} 반환."""
    resp = session.get(
        NINGBO_LIST_URL,
        params={"ptIdx": _NINGBO_PT_IDX, "mId": _NINGBO_MID},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items: list[dict] = []
    for elem in soup.find_all(attrs={"onclick": re.compile(r"goTo\.view")}):
        onclick = elem.get("onclick", "")
        m = re.search(r"goTo\.view\(['\"]view['\"],\s*['\"](\d+)['\"]", onclick)
        if m:
            bidx = m.group(1)
            if not any(r["bIdx"] == bidx for r in items):
                items.append({"bIdx": bidx, "title_raw": elem.get_text(strip=True)})

    return items[:limit]


def _fetch_ningbo_view(session: requests.Session, bidx: str, title_raw: str) -> dict:
    """닝보 NCFI 뷰 페이지에서 제목·날짜·본문(ㅇ 항목) 추출."""
    resp = session.get(
        NINGBO_VIEW_URL,
        params={"mId": _NINGBO_MID, "bIdx": bidx, "ptIdx": _NINGBO_PT_IDX},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 날짜 추출
    date = ""
    view_el = soup.select_one(".CommView, div.view")
    if view_el:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", view_el.get_text(separator=" ", strip=True))
        if m:
            date = m.group(1)

    # 본문 추출 (div.editContentView 또는 div.viewCon)
    content_el = soup.select_one("div.editContentView") or soup.select_one("div.viewCon")
    text = content_el.get_text(separator="\n", strip=True) if content_el else ""

    return {"bIdx": bidx, "title": title_raw, "date": date, "text": text}


def download_ningbo_reports(limit: int = 6) -> list[dict]:
    """
    닝보 NCFI 주간 보고서 최신 limit건 스크래핑.
    결과를 data/ningbo_ncfi_reports.json에 저장.
    Returns: [{bIdx, title, date, text}, ...]
    """
    session = _make_session()
    # BBS 목록 세션 워밍업
    try:
        session.get(NINGBO_LIST_URL, params={"ptIdx": _NINGBO_PT_IDX, "mId": _NINGBO_MID}, timeout=15)
    except Exception:
        pass

    items = _fetch_ningbo_list(session, limit=limit)
    results: list[dict] = []

    for item in items:
        try:
            data = _fetch_ningbo_view(session, item["bIdx"], item["title_raw"])
            results.append(data)
            logger.info(f"닝보 리포트 수집: bIdx={item['bIdx']}, date={data['date']}, "
                        f"text_len={len(data['text'])}")
        except Exception as e:
            logger.warning(f"닝보 리포트 수집 실패 (bIdx={item['bIdx']}): {e}")
        time.sleep(0.5)

    if results:
        _DATA_ROOT.mkdir(parents=True, exist_ok=True)
        _NINGBO_JSON.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"닝보 NCFI 리포트 저장: {_NINGBO_JSON} ({len(results)}건)")

    return results


def load_ningbo_context(max_reports: int = 3) -> str:
    """
    저장된 닝보 NCFI 리포트 텍스트 로드 (LLM 컨텍스트 보강용).
    Returns: 합산 텍스트 (최대 max_reports건)
    """
    reports: list[dict] = []
    if _NINGBO_JSON.exists():
        try:
            reports = json.loads(_NINGBO_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not reports:
        return ""

    parts = []
    for r in reports[:max_reports]:
        text = r.get("text", "")
        if text:
            label = f"{r.get('date', '')} {r.get('title', '')}".strip()
            parts.append(f"[닝보 NCFI 리포트 {label}]\n{text[:2000]}")

    return "\n\n".join(parts)
