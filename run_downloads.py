"""24주 KOBC PDF + 닝보 NCFI 보고서 다운로드"""
import sys, logging
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', handlers=[logging.StreamHandler(sys.stdout)])

from dotenv import load_dotenv; load_dotenv(override=True)
from core.kobc import download_all_reports, download_ningbo_reports, get_kobc_route_history

print("=== KOBC 24주 PDF 다운로드 ===")
reports = download_all_reports(max_reports=24, max_pages=3)
print(f"완료: {len(reports)}건")

print("\n=== 닝보 NCFI 보고서 다운로드 ===")
ningbo = download_ningbo_reports(limit=6)
print(f"완료: {len(ningbo)}건")

print("\n=== 루트 히스토리 요약 ===")
rows = get_kobc_route_history()
print(f"총 {len(rows)}주: {rows[0]['date'] if rows else 'N/A'} ~ {rows[-1]['date'] if rows else 'N/A'}")
