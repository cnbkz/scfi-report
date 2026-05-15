"""
SCFI 지수 전주 대비 증감 계산 모듈
"""

FIELD_META: dict[str, tuple[str, str]] = {
    "scfi_composite":          ("SCFI 종합지수",    "pt"),
    "scfi_north_america_east": ("북미 동안 (USEC)", "pt"),
    "scfi_north_america_west": ("북미 서안 (USWC)", "pt"),
    "scfi_europe":             ("유럽",             "pt"),
    "scfi_australia":          ("호주/오세아니아",   "pt"),
}


def _calc_row(key: str, current: float, previous: float) -> dict:
    change     = round(current - previous, 2)
    change_pct = round((change / previous * 100) if previous else 0.0, 2)
    direction  = "▲" if change > 0 else ("▼" if change < 0 else "-")
    label, unit = FIELD_META[key]
    return {
        "key":        key,
        "label":      label,
        "unit":       unit,
        "current":    current,
        "previous":   previous,
        "change":     change,
        "change_pct": change_pct,
        "direction":  direction,
    }


def calculate(current_data: dict, previous_data: dict | None) -> dict:
    result: dict[str, dict] = {}
    for key in FIELD_META:
        cur  = float(current_data.get(key, 0))
        prev = float(previous_data.get(key, cur)) if previous_data else cur
        result[key] = _calc_row(key, cur, prev)
    return result


def scfi_rows(calc_result: dict) -> list[dict]:
    return list(calc_result.values())
