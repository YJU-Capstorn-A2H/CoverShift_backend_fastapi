"""app/graph_azuki_convert.py の試験 — DB も LangGraph も使わない(関数だけ)。"""
import pytest

from app.graph_azuki_convert import resume_to_azuki, review_wait, rows_to_azuki, rows_to_seung

HER = [{"staff_id": 3, "date": "2026-11-02", "start": "09:00", "end": "13:00"}]
HIS = [{"employee_id": 3, "work_date": "2026-11-02", "kind": "work", "start_time": "09:00", "end_time": "13:00"}]


def test_rows_roundtrip():
    assert rows_to_seung(HER) == HIS
    assert rows_to_azuki(HIS) == HER


def test_rows_to_azuki_drops_non_work_and_trims_seconds():
    rows = HIS + [{"employee_id": 4, "work_date": "2026-11-02", "kind": "paid_leave", "start_time": None, "end_time": None},
                  {"employee_id": 5, "work_date": "2026-11-03", "kind": "work", "start_time": "10:00:00", "end_time": "14:30:00"}]
    assert rows_to_azuki(rows) == HER + [{"staff_id": 5, "date": "2026-11-03", "start": "10:00", "end": "14:30"}]


def test_review_wait_shape():
    value = {"draft_store_shift": HER, "ask": "approved | redraft_requested | edit_saved",
             "problems": ["2026-11-02 13:00 社員がいません"], "blocked_reasons": ["資格者が足りません"], "redraft_left": 1}
    w = review_wait(value, values={"result": "limit_reached", "return_count": 3}, draft_id=7,
                    violations=[{"check_code": "V-1"}], blocked=[], limit=2)
    assert w["type"] == "review" and w["draft_id"] == 7
    assert w["result"] == "limit_reached" and w["auto_retry_count"] == 3
    assert w["manager_request_count"] == 1 and w["manager_request_limit"] == 2
    assert w["notes"] == ["資格者が足りません"] and w["violations"] == [{"check_code": "V-1"}]
    assert set(w) == {"type", "draft_id", "result", "violations", "blocked", "shortages", "notes",
                      "free_text_guidelines", "auto_retry_count", "manager_request_count", "manager_request_limit"}


def test_review_wait_result_valid_and_empty():
    base = {"ask": "x", "redraft_left": 2}
    assert review_wait({**base, "draft_store_shift": HER}, values={"result": "valid"}, draft_id=1,
                       violations=[], blocked=[], limit=2)["result"] == "valid"
    assert review_wait({**base, "draft_store_shift": []}, values={"result": "retry"}, draft_id=1,
                       violations=[], blocked=[], limit=2)["result"] == "draft_empty"


def test_resume_review():
    assert resume_to_azuki({"decision": "approved", "manager_id": 1, "draft_id": 7, "waived_keys": ["a"]}) == {"decision": "approved"}
    assert resume_to_azuki({"decision": "redraft_requested", "manager_id": 1, "draft_id": 7, "instruction": " 9時を4人 "}) == \
        {"decision": "redraft_requested", "instruction": "9時を4人"}
    assert resume_to_azuki({"decision": "edit_saved", "manager_id": 1, "draft_id": 7, "assignments": HIS}) == \
        {"decision": "edit_saved", "assignments": HER}


def test_resume_contact():
    assert resume_to_azuki({"contact_id": 1, "answer": "yes"}) == "可能"
    assert resume_to_azuki({"contact_id": 1, "answer": "no"}) == "断り"
    with pytest.raises(ValueError):
        resume_to_azuki({"contact_id": 1, "answer": "maybe"})
