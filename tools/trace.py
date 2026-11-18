"""하네스 ④: REQUIREMENTS.md 의 번호 중 테스트가 하나도 없는 것만 출력한다.

테스트 쪽 표시: pytest 는 @pytest.mark.req("V-1") (여러 개면 req("V-1", "F-2")), 화면은 'req:W-1' 문자열.
종료 코드: 빈 칸이 있으면 1.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ids = re.findall(r"^\| ([MVTFAWD]-\d+) \|", (ROOT / "REQUIREMENTS.md").read_text(), flags=re.M)
covered: dict[str, set[str]] = {}
files = list((ROOT / "tests").rglob("test_*.py")) + [
    p for p in (ROOT / "web" / "src").rglob("*") if p.is_file() and p.suffix in (".ts", ".js", ".vue")
] if (ROOT / "web" / "src").exists() else list((ROOT / "tests").rglob("test_*.py"))
for f in files:
    text = f.read_text(errors="ignore")
    found = set()
    for m in re.finditer(r"req\(([^)]*)\)", text):
        found |= set(re.findall(r"[MVTFAWD]-\d+", m.group(1)))
    found |= set(re.findall(r"req:([MVTFAWD]-\d+)", text))
    for i in found:
        covered.setdefault(i, set()).add(str(f.relative_to(ROOT)))
missing = [i for i in ids if i not in covered]
print(f"요구 번호 {len(ids)}개 / 테스트 있음 {len(ids) - len(missing)}개 / 없음 {len(missing)}개")
for i in missing:
    print(f"  테스트 없음: {i}")
unknown = sorted(set(covered) - set(ids))
for i in unknown:
    print(f"  표에 없는 번호를 단 테스트: {i} ← {sorted(covered[i])}")
sys.exit(1 if missing or unknown else 0)
