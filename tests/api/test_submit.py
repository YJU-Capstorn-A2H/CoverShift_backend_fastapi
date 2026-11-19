"""제출 링크 · 직원 제출 (A-10, A-11, A-12, A-17, W-7)."""
from __future__ import annotations

import pytest

from ._util import PAST, err


def _links(api, h, pid) -> dict[int, str]:
    r = api.c.post(f"/api/shift-periods/{pid}/submission-links", headers=h)
    assert r.status_code == 200, r.text
    return {x["employee_id"]: x["url"].rsplit("/", 1)[1] for x in r.json()["links"]}


@pytest.fixture
def store(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    api.regular = api.employee(h, "Aさん", True)
    api.part = api.employee(h, "Bさん", False)
    api.pid = api.period(h, off_request_limit=2, work_request_limit=1)
    api.tok = _links(api, h, api.pid)
    api.h = h
    return api


def _put(api, token, *items):
    return api.c.put(f"/api/submit/{token}/availability", json={"items": list(items)})


@pytest.mark.req("A-10", "A-17")
def test_submission_links_reuse_same_url_and_line_outbox_once_per_employee(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    e1, e2 = api.employee(h, "Aさん"), api.employee(h, "Bさん", False)
    api.sql("UPDATE employees SET line_user_id = 'U-line-1' WHERE employee_id = %s", (e1,))
    pid = api.period(h)
    r1 = api.c.post(f"/api/shift-periods/{pid}/submission-links", headers=h).json()
    assert [x["employee_id"] for x in r1["links"]] == [e1, e2]
    assert all(x["url"].startswith("http://web.test/submit/") for x in r1["links"])
    assert r1["sent"] == 1  # LINE 이 연결된 직원에게만 스텁 발송 (가정 신규)
    # 깨뜨리는 쪽: 다시 불러도 같은 링크, 발송은 0 (dedupe)
    r2 = api.c.post(f"/api/shift-periods/{pid}/submission-links", headers=h).json()
    assert r2["links"] == r1["links"] and r2["sent"] == 0
    assert api.sql("SELECT count(*) AS n FROM submission_links")[0]["n"] == 2
    box = api.c.get("/api/dev/line-outbox", headers=h).json()
    assert [(m["employee_id"], m["employee_name"], m["kind"]) for m in box] == [(e1, "Aさん", "submission_link")]
    assert box[0]["body"]["url"] == r1["links"][0]["url"]
    # 나중에 LINE 을 연결한 직원은 그 직원에게만 한 번 더 보낸다
    api.sql("UPDATE employees SET line_user_id = 'U-line-2' WHERE employee_id = %s", (e2,))
    assert api.c.post(f"/api/shift-periods/{pid}/submission-links", headers=h).json()["sent"] == 1


@pytest.mark.req("A-11", "W-7")
def test_submit_page_is_same_every_time_and_unknown_token_404(store):
    api = store
    t = api.tok[api.regular]
    a = api.c.get(f"/api/submit/{t}")
    b = api.c.get(f"/api/submit/{t}")
    assert a.status_code == 200 and a.json() == b.json()
    assert a.json() == {"employee_name": "Aさん", "is_regular": True, "target_month": "2026-12-01",
                        "submission_deadline": a.json()["submission_deadline"], "off_request_limit": 2,
                        "work_request_limit": 1, "items": []}
    assert err(api.c.get("/api/submit/no-such-token")) == (404, "NOT_FOUND")


@pytest.mark.req("A-11", "A-12")
def test_after_deadline_get_put_delete_are_410(store):
    api = store
    t = api.tok[api.part]
    assert _put(api, t, {"work_date": "2026-12-01", "kind": "off"}).status_code == 200
    api.c.patch(f"/api/shift-periods/{api.pid}", headers=api.h, json={"submission_deadline": PAST})
    assert err(api.c.get(f"/api/submit/{t}")) == (410, "SUBMISSION_CLOSED")
    assert err(_put(api, t, {"work_date": "2026-12-02", "kind": "off"})) == (410, "SUBMISSION_CLOSED")
    assert err(api.c.delete(f"/api/submit/{t}/availability/2026-12-01")) == (410, "SUBMISSION_CLOSED")
    assert len(api.sql("SELECT * FROM availability_submissions")) == 1  # 마감 뒤에는 아무것도 바뀌지 않는다


@pytest.mark.req("A-12")
def test_put_upserts_only_sent_dates(store):
    api = store
    t = api.tok[api.part]
    r = _put(api, t, {"work_date": "2026-12-01", "kind": "available", "start_time": "09:00", "end_time": "13:30"},
             {"work_date": "2026-12-02", "kind": "half_leave", "start_time": "13:00", "end_time": "17:00"})
    assert r.status_code == 200
    assert r.json()["items"] == [
        {"work_date": "2026-12-01", "kind": "available", "start_time": "09:00", "end_time": "13:30"},
        {"work_date": "2026-12-02", "kind": "half_leave", "start_time": "13:00", "end_time": "17:00"}]
    r = _put(api, t, {"work_date": "2026-12-01", "kind": "off"})  # 같은 날 다시 → 덮어쓰기, 12-02 는 그대로
    assert r.json()["items"] == [
        {"work_date": "2026-12-01", "kind": "off", "start_time": None, "end_time": None},
        {"work_date": "2026-12-02", "kind": "half_leave", "start_time": "13:00", "end_time": "17:00"}]
    assert api.c.get(f"/api/submit/{t}").json()["items"] == r.json()["items"]
    assert api.c.delete(f"/api/submit/{t}/availability/2026-12-01").status_code == 204
    assert [x["work_date"] for x in api.c.get(f"/api/submit/{t}").json()["items"]] == ["2026-12-02"]
    # 관리자 쪽 제출 현황 (기간 단위)
    sub = api.c.get(f"/api/shift-periods/{api.pid}/submissions", headers=api.h).json()
    assert [(x["employee_id"], x["name"], len(x["items"])) for x in sub] == [(api.regular, "Aさん", 0),
                                                                             (api.part, "Bさん", 1)]


@pytest.mark.req("A-12")
def test_put_item_rules_time_required_only_for_available_and_half_leave_30min(store):
    api = store
    t = api.tok[api.part]
    bad = [
        {"work_date": "2026-12-01", "kind": "available"},  # 시각 없음
        {"work_date": "2026-12-01", "kind": "half_leave", "start_time": "09:00"},  # 종료 없음
        {"work_date": "2026-12-01", "kind": "off", "start_time": "09:00", "end_time": "10:00"},  # off 에 시각
        {"work_date": "2026-12-01", "kind": "paid_leave", "start_time": "09:00", "end_time": "10:00"},
        {"work_date": "2026-12-01", "kind": "available", "start_time": "09:15", "end_time": "10:00"},  # 30분 단위 아님
        {"work_date": "2026-12-01", "kind": "available", "start_time": "10:00", "end_time": "10:00"},  # 종료 ≤ 시작
        {"work_date": "2027-01-01", "kind": "off"},  # 다른 달
        {"work_date": "2026-12-01", "kind": "sick"},  # 없는 종류
    ]
    for it in bad:
        assert err(_put(api, t, it)) == (422, "INVALID_ITEM"), it
    assert err(_put(api, t, {"work_date": "2026-12-01", "kind": "off"}, {"work_date": "2026-12-01",
                                                                       "kind": "off"})) == (422, "INVALID_ITEM")
    # 한 묶음 안에 하나라도 틀리면 아무것도 저장하지 않는다
    assert err(_put(api, t, {"work_date": "2026-12-03", "kind": "off"},
                    {"work_date": "2026-12-04", "kind": "available"})) == (422, "INVALID_ITEM")
    assert api.sql("SELECT count(*) AS n FROM availability_submissions")[0]["n"] == 0
    # 지키는 경우
    ok = [{"work_date": "2026-12-01", "kind": "off"}, {"work_date": "2026-12-02", "kind": "paid_leave"},
          {"work_date": "2026-12-31", "kind": "available", "start_time": "08:00", "end_time": "22:30"}]
    assert _put(api, t, *ok).status_code == 200  # 파트의 paid_leave 도 받는다 (가정 신규, V9_ko 437)


@pytest.mark.req("A-12")
def test_regular_staff_off_and_available_counts_are_limited_half_leave_not_counted(store):
    api = store
    t = api.tok[api.regular]  # off_request_limit=2, work_request_limit=1
    off = [{"work_date": f"2026-12-0{d}", "kind": "off"} for d in (1, 2)]
    half = {"work_date": "2026-12-05", "kind": "half_leave", "start_time": "09:00", "end_time": "13:00"}
    assert _put(api, t, *off, half).status_code == 200  # 반휴는 세지 않는다
    # 깨뜨리는 쪽: 3번째 off → 422, 저장되지 않는다
    r = _put(api, t, {"work_date": "2026-12-03", "kind": "off"})
    assert err(r) == (422, "LIMIT_EXCEEDED")
    assert api.sql("SELECT count(*) AS n FROM availability_submissions WHERE kind = 'off'")[0]["n"] == 2
    # 같은 날을 off → available 로 바꾸는 것은 개수가 늘지 않는다
    assert _put(api, t, {"work_date": "2026-12-02", "kind": "available", "start_time": "09:00",
                         "end_time": "18:00"}).status_code == 200
    assert err(_put(api, t, {"work_date": "2026-12-04", "kind": "available", "start_time": "09:00",
                             "end_time": "18:00"})) == (422, "LIMIT_EXCEEDED")
    # 파트 · 알바는 상한을 세지 않는다 (V9_ko 165 「사원의」)
    p = api.tok[api.part]
    assert _put(api, p, *[{"work_date": f"2026-12-1{d}", "kind": "off"} for d in range(5)]).status_code == 200
    # 상한이 null 이면 제한 없음
    api.c.patch(f"/api/shift-periods/{api.pid}", headers=api.h, json={"off_request_limit": None})
    assert _put(api, t, {"work_date": "2026-12-03", "kind": "off"},
                {"work_date": "2026-12-04", "kind": "off"}).status_code == 200
