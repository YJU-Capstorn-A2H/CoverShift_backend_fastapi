"""위반 이력 묶기(C3)와 승인 전 최종 검사(check_approval)."""
from __future__ import annotations

from collections.abc import Sequence

from app.domain import NON_WAIVABLE_CODES, Violation, ViolationGroup


def group_history(history: Sequence[Sequence[Violation]]) -> list[ViolationGroup]:
    """C3 (V9_ko 486, F-5): history[i] = i+1 회차의 위반 목록 → Violation.key 마다 묶음 1개.

    - rounds = 그 위반이 나온 회차(1부터, 오름차순, 같은 회차에 두 번 나와도 한 번).
    - 순서 = 처음 나온 순서 (같은 회차 안에서는 그 회차 목록의 순서).
    - example = 가장 최근 회차의 것 (detail 의 have 같은 숫자가 지금 상태를 보여 주도록). check_code · severity 도 거기서 읽는다.
    """
    rounds: dict[str, list[int]] = {}
    latest: dict[str, Violation] = {}
    for i, vs in enumerate(history, start=1):
        for v in vs:
            k = v.key
            rs = rounds.setdefault(k, [])
            if not rs or rs[-1] != i:
                rs.append(i)
            latest[k] = v
    return [ViolationGroup(key=k, check_code=latest[k].check_code, severity=latest[k].severity,
                           rounds=tuple(rs), example=latest[k]) for k, rs in rounds.items()]


def blocking(violations: Sequence[Violation], waived_keys: set[str]) -> list[Violation]:
    """check_approval (V9_ko 87, 1077, F-8): 승인을 막는 위반.
    = 예외 불가 코드(QUAL_MISSING · DOUBLE_BOOKING, V9_ko 479) 전부 — waived_keys 에 있어도 막는다
    + 허용 목록에 없는 required. warning 은 막지 않는다."""
    return [v for v in violations
            if v.check_code in NON_WAIVABLE_CODES or (v.severity == "required" and v.key not in waived_keys)]
