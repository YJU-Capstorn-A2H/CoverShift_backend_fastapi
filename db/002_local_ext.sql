-- CoverShift 로컬 mvp 확장 — 추천안(001_schema_recommended.sql)에 없는, 로컬 mvp 앱이 쓰는 표
-- 001 은 고치지 않는다 (notes 원본과 같은 내용). 이 파일은 001 다음에, 같은 실행 주체로 실행한다.
-- 결정 (2026-11-19 승혁): line_outbox 는 추천안에 다시 추가, 출근 요청은 별도 표(contact_requests).
--   추천안의 contact_log(편성안 단위 통지 기록)는 001 에 그대로 있고 앱은 쓰지 않는다.
-- contact_requests 는 옛 db/_old/002_ext_assumed.sql 의 G8 그대로다(표 이름만 다르다). line_outbox 는 발송 상태 열을 더했다.

-- G14) LINE 발송함 (명세 2-6 ⑥ D6). 트랜잭션 안에서는 「대기(pending)」로 적기만 하고,
--      커밋 뒤 발송기(app/line_dispatcher.py)가 한 건씩 보내고 결과를 적는다 (결정 2026-11-19 승혁).
--      dedupe_key = 같은 통지 두 번 방지 (명세 1-2 notified 「중복 발송 방지」)
--      retry_key  = LINE 의 X-Line-Retry-Key. 재시도 때 같은 값을 써야 하므로 행에 고정한다
--                   (LINE 문서 · 재시도 키: 첫 요청부터 넣는다 · 유효 24시간)
--                   message_id 에서 만들지 않는 이유: DB 를 다시 만들면 message_id 가 1부터 다시 나와, 24시간 안에 같은 키가
--                   다른 메시지에 쓰이면 LINE 이 409 를 주고 발송기는 「이미 보냄」으로 잘못 적는다
--      status     = pending 대기 / sent 보냄 / failed 실패(더 시도하지 않음) / no_recipient 보낼 곳 없음(LINE 미연결 · 점장 앞)
--      queued_at  = 대기에 들어간 시각. 발송기는 이 시각부터 정해진 기한까지만 재시도한다 (app/line_dispatcher.py).
--                   실패 행을 다시 대기로 돌리면(ops/requeue_line.py) 이 값이 새로 찍힌다. created_at 은 처음 적은 시각 그대로다
CREATE TABLE line_outbox (
    message_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id        BIGINT NOT NULL REFERENCES stores(store_id),
    employee_id     BIGINT REFERENCES employees(employee_id),        -- NULL = 점장 앞
    kind            TEXT   NOT NULL CHECK (kind IN ('link_token','submission_link','contact_ask','shift_confirmed','manager_notice')),
    dedupe_key      TEXT   NOT NULL UNIQUE,
    body            JSONB  NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    retry_key       UUID   NOT NULL DEFAULT gen_random_uuid(),
    status          TEXT   NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed','no_recipient')),
    attempts        INT    NOT NULL DEFAULT 0 CHECK (attempts >= 0),  -- 발송을 시도한 횟수 (성공한 시도 포함)
    next_attempt_at TIMESTAMPTZ DEFAULT now(),                        -- 이 시각부터 발송기가 잡는다. 대기일 때만 값이 있다
    sent_at         TIMESTAMPTZ,
    last_error      TEXT,                                             -- 상태 코드 + 짧은 문장. 토큰 · 시크릿은 넣지 않는다
    CHECK ((status = 'pending') = (next_attempt_at IS NOT NULL)),
    CHECK ((status = 'sent') = (sent_at IS NOT NULL)),
    CHECK (status <> 'failed' OR last_error IS NOT NULL)
);
-- 발송기가 「보낼 때가 된 대기 행」을 찾는 조회용. 보낸 행이 쌓여도 대기 행만 본다
CREATE INDEX line_outbox_due ON line_outbox (next_attempt_at) WHERE status = 'pending';

-- G8) 직원 출근 요청 기록 (옛 002 의 contact_log). M-6 이 꺼져 있으면 비어 있다.
CREATE TABLE contact_requests (
    contact_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id    BIGINT NOT NULL REFERENCES shift_periods(period_id),
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    work_date    DATE   NOT NULL,
    start_time   TIME   NOT NULL,
    end_time     TIME   NOT NULL,
    status       TEXT   NOT NULL DEFAULT 'asked' CHECK (status IN ('asked','accepted','declined','expired')),
    asked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at  TIMESTAMPTZ,
    CHECK ((status = 'asked') = (answered_at IS NULL)),
    UNIQUE (period_id, employee_id, work_date, start_time)
);

-- 권한 (001 의 15) 와 같은 방식). 001 의 REVOKE · GRANT 는 그때 있던 표 · 시퀀스에만 걸렸으므로 새 표에 다시 준다
REVOKE ALL ON line_outbox, contact_requests FROM PUBLIC;
-- 앱: 발송함은 추가(INSERT ... ON CONFLICT DO NOTHING) · 조회 + 발송기가 고치는 「발송 결과」 열만 UPDATE. 삭제 권한 없음.
--     본문(body) · 받는 직원(employee_id) · dedupe_key · retry_key · queued_at 은 고칠 수 없다 (재시도는 내용이 원래와 같아야 한다).
--     단 ① 받는 주소는 이 표에 없다 — 발송할 때 employees.line_user_id 에서 읽으므로, 그 값이 바뀌면 재시도는 새 주소로 간다.
--        ② 이 열 단위 권한은 앱이 covershift_app 으로 접속할 때만 걸린다 (표 소유자 · 슈퍼유저로 접속하면 걸리지 않는다).
--        ③ 재투입 스크립트(ops/requeue_line.py)는 retry_key · queued_at 을 고치므로 표 소유자 권한으로 돌린다
GRANT SELECT, INSERT ON line_outbox TO covershift_app;
GRANT UPDATE (status, attempts, next_attempt_at, sent_at, last_error) ON line_outbox TO covershift_app;
-- 앱: 출근 요청은 질문 INSERT + 답 기록 UPDATE. 삭제 권한은 주지 않는다
GRANT SELECT, INSERT, UPDATE ON contact_requests TO covershift_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO covershift_app;
