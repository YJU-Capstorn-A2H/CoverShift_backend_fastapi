-- CoverShift DB 추천안 (2026-11-18) — 팀 develop v18 + DB 전문가 검토 추천 전부 (승혁 「DB 전문가 추천 전부」 11/18)
-- 근거 문서: notes/2026-11-18-DB-전문가-검토.md (번호 = 그 문서의 D-C*)
-- 이 파일은 승혁 개인 ERD 용 제안이다. 팀 develop v18 · 로컬 mvp 스키마 · 코드는 이 파일에 맞춰 고치지 않았다.
-- 실행 주체: 소유자(마이그레이션) 롤 covershift_owner 로 실행한다. 롤 비밀번호는 이 파일에 쓰지 않는다(운영에서 따로 설정).
--
-- 바뀐 것 (v18 대비)
--   evaluations 삭제 ............................. decision-log 10/27 자기평가 완전 제외
--   employee_skills · store_task_needs 삭제 ......... D-C18 B (tasks 는 유지, 12/4 이후 10/27 모양으로 새로 만든다)
--   employees.left_on 추가 ........................ D-C5 A (퇴직일. 이후 편성 제외)
--   shift_drafts.status 에 'superseded' ........... D-C6 A (확정 후 수정 = 새 draft 를 다시 승인, 옛 확정본은 superseded)
--   shift_drafts.created_at 추가 .................. C12
--   shift_assignments.kind 에 'half_leave' ......... D-C3 A (반휴를 확정안에 기록)
--   validation_violations.severity 추가 ........... C8
--   validation_violations 의 allowed_by · allowed_at → violation_waivers 로 분리 ... D-C17 B (D-C1 이 append-only 라 조건부 추천 B 적용)
--   validation_violations(check_code, work_date) 인덱스 삭제 ... C14 (쓰는 쿼리 0)
--   manager_actions 를 mvp 002 모양으로 ............ D-C4 A (period_id NOT NULL, draft_id NULL 허용, action 4값)
--   롤 3개 + 권한 + 트리거 ......................... D-C1 B · D-C2 A
-- 그대로 둔 것: stores · manager_accounts 분리 (D-C16 0), availability PK (D-C15 0), 승인 이중 기록 (D-C7 0), contact_log v18 모양 (C19 인지만)
-- 이 파일 밖 (인지 · 기록만): 백업 C9, 마이그레이션 도구 C10, 토큰 해시 C11, FK 인덱스 C14 나머지

-- =========================================================
-- 0) 롤 (D-C2 A). 클러스터 단위라 이미 있으면 건너뛴다
-- =========================================================
DO $$ BEGIN CREATE ROLE covershift_owner NOLOGIN;            EXCEPTION WHEN duplicate_object THEN NULL; END $$;  -- 표 소유 · 마이그레이션 전용
DO $$ BEGIN CREATE ROLE covershift_app LOGIN NOSUPERUSER;    EXCEPTION WHEN duplicate_object THEN NULL; END $$;  -- API · 그래프 · Aegra 접속
DO $$ BEGIN CREATE ROLE covershift_llm_read LOGIN NOSUPERUSER; EXCEPTION WHEN duplicate_object THEN NULL; END $$; -- MCP 서버 전용. 이름(name)을 못 읽는다

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- 1) 관리자 계정 (그대로)
CREATE TABLE manager_accounts (
    manager_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT true          -- 점장 교대 시 옛 계정은 false (지우지 않는다, 11/04)
);

-- 2) 점포 (그대로, 계정 1 = 점포 1)
CREATE TABLE stores (
    store_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    manager_id  BIGINT NOT NULL UNIQUE REFERENCES manager_accounts(manager_id),
    name        TEXT NOT NULL
);

-- 2-b) 초대 코드 (그대로)
CREATE TABLE invite_codes (
    code        TEXT PRIMARY KEY,
    store_id    BIGINT NOT NULL REFERENCES stores(store_id),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    used_by     BIGINT REFERENCES manager_accounts(manager_id),
    CHECK ((used_at IS NULL) = (used_by IS NULL))
);

-- 3) 요일 × 시간대 규칙 (그대로)
CREATE TABLE store_time_rules (
    rule_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id         BIGINT   NOT NULL REFERENCES stores(store_id),
    weekday          SMALLINT NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    start_time       TIME     NOT NULL,
    end_time         TIME     NOT NULL,
    min_headcount    SMALLINT NOT NULL CHECK (min_headcount >= 1),
    min_qualified    SMALLINT NOT NULL DEFAULT 0 CHECK (min_qualified >= 0),
    CHECK (end_time > start_time),
    EXCLUDE USING gist (store_id WITH =, weekday WITH =,
        tsrange(('2000-01-01'::date + start_time), ('2000-01-01'::date + end_time)) WITH &&)
);

-- 4) 직원 (+ left_on, D-C5 A)
CREATE TABLE employees (
    employee_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id                 BIGINT  NOT NULL REFERENCES stores(store_id),
    name                     TEXT    NOT NULL,
    is_regular               BOOLEAN NOT NULL,
    has_qualification        BOOLEAN NOT NULL DEFAULT false,
    closing_ok               BOOLEAN NOT NULL DEFAULT false,
    weekly_days              SMALLINT CHECK (weekly_days BETWEEN 1 AND 7),
    fixed_weekdays           SMALLINT[] CHECK (fixed_weekdays <@ ARRAY[1,2,3,4,5,6,7]::SMALLINT[]),
    line_user_id             TEXT UNIQUE,
    link_token               TEXT UNIQUE,
    link_token_expires_at    TIMESTAMPTZ,
    left_on                  DATE,                       -- 퇴직일. 이 날 이후는 편성 · 링크 · 통지에서 뺀다. NULL = 재직 (D-C5 A)
    CHECK ((link_token IS NULL) = (link_token_expires_at IS NULL)),
    CHECK (fixed_weekdays IS NULL OR weekly_days IS NULL OR weekly_days = cardinality(fixed_weekdays))
);

-- 5) 월별 설정 (그대로)
CREATE TABLE shift_periods (
    period_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id             BIGINT      NOT NULL REFERENCES stores(store_id),
    target_month         DATE        NOT NULL CHECK (EXTRACT(DAY FROM target_month) = 1),
    submission_deadline  TIMESTAMPTZ NOT NULL,
    designated_off_days  SMALLINT    NOT NULL CHECK (designated_off_days BETWEEN 0 AND 31),
    off_request_limit    SMALLINT    CHECK (off_request_limit BETWEEN 0 AND 31),
    work_request_limit   SMALLINT    CHECK (work_request_limit BETWEEN 0 AND 31),
    thread_id            TEXT        UNIQUE,
    UNIQUE (store_id, target_month)
);

-- 6) 월별 제출 링크 (그대로)
CREATE TABLE submission_links (
    token        TEXT   PRIMARY KEY,
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    period_id    BIGINT NOT NULL REFERENCES shift_periods(period_id),
    UNIQUE (employee_id, period_id)
);

-- 7) 희망 (그대로, D-C15 0: 기간은 work_date 의 달로 정해진다)
CREATE TABLE availability_submissions (
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    work_date    DATE   NOT NULL,
    kind         TEXT   NOT NULL CHECK (kind IN ('available','off','paid_leave','half_leave')),
    start_time   TIME,
    end_time     TIME,
    PRIMARY KEY (employee_id, work_date),
    CHECK ((kind IN ('available','half_leave')) = (start_time IS NOT NULL AND end_time IS NOT NULL)),
    CHECK (start_time IS NULL OR end_time > start_time),
    CHECK (start_time IS NULL OR (EXTRACT(MINUTE FROM start_time) IN (0,30)
                              AND EXTRACT(MINUTE FROM end_time)   IN (0,30)))
);

-- 8) 편성안 (+ superseded D-C6 A, + created_at C12)
CREATE TABLE shift_drafts (
    draft_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id       BIGINT NOT NULL REFERENCES shift_periods(period_id),
    status          TEXT   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','confirmed','superseded')),
        -- superseded = 한 번 확정됐다가 수정판(새 draft, parent = 이 안)이 확정되어 물러난 안. 지우지 않는다
    parent_draft_id BIGINT REFERENCES shift_drafts(draft_id),
    confirmed_at    TIMESTAMPTZ,
    confirmed_by    BIGINT REFERENCES manager_accounts(manager_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((status IN ('confirmed','superseded')) = (confirmed_at IS NOT NULL)),
    CHECK ((confirmed_at IS NULL) = (confirmed_by IS NULL)),
    CHECK (parent_draft_id IS NULL OR parent_draft_id <> draft_id)
);
CREATE UNIQUE INDEX one_confirmed_draft_per_period ON shift_drafts(period_id) WHERE status = 'confirmed';

-- 9) 배정 (+ half_leave D-C3 A). 앱 롤은 INSERT · SELECT 만 (D-C1 B, 아래 권한)
CREATE TABLE shift_assignments (
    draft_id     BIGINT NOT NULL REFERENCES shift_drafts(draft_id),
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    work_date    DATE   NOT NULL,
    kind         TEXT   NOT NULL DEFAULT 'work' CHECK (kind IN ('work','half_leave','paid_leave')),
        -- half_leave = 반휴. 시각 그대로 출근(출근일수 1일). 희망 표가 바뀌어도 확정안에 남는다
    start_time   TIME,
    end_time     TIME,
    break_start  TIME,
    break_end    TIME,
    PRIMARY KEY (draft_id, employee_id, work_date),
    CHECK ((kind IN ('work','half_leave')) = (start_time IS NOT NULL AND end_time IS NOT NULL)),
    CHECK ((start_time IS NULL) = (end_time IS NULL)),
    CHECK (end_time > start_time),
    CHECK ((break_start IS NULL) = (break_end IS NULL)),
    CHECK (break_start IS NULL OR (kind IN ('work','half_leave') AND break_end > break_start AND break_start >= start_time AND break_end <= end_time)),
    CHECK (EXTRACT(MINUTE FROM start_time) IN (0,30) AND EXTRACT(MINUTE FROM end_time) IN (0,30))
);

-- 10) 업무 목록 (그대로. 검사에는 안 씀, 11/04)
CREATE TABLE tasks (
    task_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id    BIGINT   NOT NULL REFERENCES stores(store_id),
    name        TEXT     NOT NULL,
    sort_order  SMALLINT NOT NULL DEFAULT 0,
    UNIQUE (store_id, name)
);

-- 11) 검증 위반 (+ severity C8, 허용은 별도 표 D-C17 B). 앱 롤은 INSERT · SELECT 만
CREATE TABLE validation_violations (
    violation_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    draft_id      BIGINT NOT NULL REFERENCES shift_drafts(draft_id),
    check_code    TEXT   NOT NULL,
    severity      TEXT   CHECK (severity IN ('required','warning')),
    work_date     DATE,
    employee_id   BIGINT REFERENCES employees(employee_id),
    detail        JSONB
);
CREATE INDEX ON validation_violations(draft_id);

-- 11-b) 위반 예외 허용 (D-C17 B). 위반 1건에 허용 1번
CREATE TABLE violation_waivers (
    violation_id  BIGINT PRIMARY KEY REFERENCES validation_violations(violation_id),
    allowed_by    BIGINT NOT NULL REFERENCES manager_accounts(manager_id),
    allowed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason        TEXT
);

-- 12) 점장 조작 · 틀 규칙 (D-C4 A = mvp 002 모양)
CREATE TABLE manager_actions (
    action_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id    BIGINT NOT NULL REFERENCES shift_periods(period_id),
    draft_id     BIGINT REFERENCES shift_drafts(draft_id),           -- rules_confirmed 는 안이 생기기 전이라 NULL
    manager_id   BIGINT NOT NULL REFERENCES manager_accounts(manager_id),
    action       TEXT   NOT NULL CHECK (action IN ('rules_confirmed','approved','redraft_requested','edit_saved')),
    instruction  TEXT,
    rules        JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (action = 'rules_confirmed' OR draft_id IS NOT NULL),
    CHECK ((action = 'rules_confirmed') = (rules IS NOT NULL))
);
CREATE UNIQUE INDEX one_approval_per_draft ON manager_actions(draft_id) WHERE action = 'approved';
CREATE INDEX ON manager_actions(period_id, created_at);

-- 13) 직원 연락 (v18 그대로, C19 인지만)
CREATE TABLE contact_log (
    contact_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    draft_id     BIGINT NOT NULL REFERENCES shift_drafts(draft_id),
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    kind         TEXT   NOT NULL DEFAULT 'shift_notice' CHECK (kind IN ('shift_notice')),
    channel      TEXT   NOT NULL DEFAULT 'line_stub' CHECK (channel IN ('line_stub','line')),
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reply        TEXT   CHECK (reply IN ('ok','ng')),
    replied_at   TIMESTAMPTZ,
    CHECK ((reply IS NULL) = (replied_at IS NULL))
);
CREATE INDEX ON contact_log(draft_id, employee_id);
CREATE INDEX contact_log_waiting ON contact_log(sent_at) WHERE reply IS NULL;

-- =========================================================
-- 14) 확정안 잠금 트리거 (D-C1 B: shift_drafts 만 트리거, 배정은 권한)
--     허용: draft → confirmed, confirmed → superseded (상태만 바뀔 때)
--     거부: confirmed/superseded 안의 그 밖의 UPDATE, confirmed/superseded 안의 DELETE
-- =========================================================
CREATE FUNCTION guard_confirmed_draft() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('confirmed','superseded') THEN
            RAISE EXCEPTION 'draft % is % and cannot be deleted', OLD.draft_id, OLD.status;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'confirmed' THEN
        IF NEW.status = 'superseded'
           AND (NEW.period_id, NEW.parent_draft_id, NEW.confirmed_at, NEW.confirmed_by, NEW.created_at)
               IS NOT DISTINCT FROM (OLD.period_id, OLD.parent_draft_id, OLD.confirmed_at, OLD.confirmed_by, OLD.created_at) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'draft % is confirmed: only confirmed -> superseded is allowed', OLD.draft_id;
    ELSIF OLD.status = 'superseded' THEN
        RAISE EXCEPTION 'draft % is superseded and immutable', OLD.draft_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER shift_drafts_guard BEFORE UPDATE OR DELETE ON shift_drafts
    FOR EACH ROW EXECUTE FUNCTION guard_confirmed_draft();

-- =========================================================
-- 15) 권한 (D-C1 B · D-C2 A · D-C17 B)
-- =========================================================
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO covershift_app, covershift_llm_read;

-- 앱: 일반 표는 읽기 · 쓰기
GRANT SELECT, INSERT, UPDATE, DELETE ON
    manager_accounts, stores, invite_codes, store_time_rules, employees, shift_periods,
    submission_links, availability_submissions, tasks, contact_log
    TO covershift_app;
-- 앱: 편성안은 상태 전이만 (트리거가 내용 보호). 삭제 권한은 주지 않는다
GRANT SELECT, INSERT, UPDATE ON shift_drafts TO covershift_app;
-- 앱: 추가만 되는 표 (UPDATE · DELETE · TRUNCATE 없음)
GRANT SELECT, INSERT ON shift_assignments, validation_violations, violation_waivers, manager_actions TO covershift_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO covershift_app;

-- MCP(LLM 읽기): 이름 열 제외, 편성 입력만
GRANT SELECT (employee_id, store_id, is_regular, has_qualification, closing_ok, weekly_days, fixed_weekdays, left_on)
    ON employees TO covershift_llm_read;
GRANT SELECT ON availability_submissions, store_time_rules, shift_periods TO covershift_llm_read;
