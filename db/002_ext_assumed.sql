-- CoverShift MVP — v17 에 없는 표 (전부 (가정). 팀 승인 대상, ASSUMPTIONS.md 참조)
-- v17(001) 원본은 고치지 않는다. 기준 문서 규칙(기준 문서 → 3명에게 알림 → 코드)보다 코드가 앞선 부분이다.

-- G2) 점장 조작 + 틀 규칙 JSON (명세 2-6 ⑤ 「manager_actions: 없음」 / 「승인 두 번 방지 제약과 같이 설계」)
CREATE TABLE manager_actions (
    action_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id    BIGINT NOT NULL REFERENCES shift_periods(period_id),
    draft_id     BIGINT REFERENCES shift_drafts(draft_id),
    manager_id   BIGINT NOT NULL REFERENCES manager_accounts(manager_id),
    action       TEXT   NOT NULL CHECK (action IN ('rules_confirmed','approved','redraft_requested','edit_saved')),
    instruction  TEXT,            -- 재편성 요청의 지시문 / 틀 규칙으로 바꾸기 전의 점장 원문
    rules        JSONB,           -- rules_confirmed 일 때: {"rules":[{rule_key,template_key,params,source_text}], "free_text":[...]}
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (action IN ('rules_confirmed') OR draft_id IS NOT NULL),
    CHECK ((action = 'rules_confirmed') = (rules IS NOT NULL))
);
-- 같은 편성안에 「승인」이 두 번 들어가지 않게 (명세 2-6 ⑤ DB 제약 ②)
CREATE UNIQUE INDEX one_approval_per_draft ON manager_actions(draft_id) WHERE action = 'approved';
CREATE INDEX ON manager_actions(period_id, created_at);

-- G14) LINE 스텁 발송함 (명세 2-6 ⑥ D6: 보내지 않고 로그・화면에 찍는다)
--      dedupe_key = 같은 통지 두 번 방지 (명세 1-2 notified 「중복 발송 방지」)
CREATE TABLE line_outbox (
    message_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id     BIGINT NOT NULL REFERENCES stores(store_id),
    employee_id  BIGINT REFERENCES employees(employee_id),        -- NULL = 점장 앞
    kind         TEXT   NOT NULL CHECK (kind IN ('link_token','submission_link','contact_ask','shift_confirmed','manager_notice')),
    dedupe_key   TEXT   NOT NULL UNIQUE,
    body         JSONB  NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- G8) 직원 연락 기록 (명세 2-6 ⑤ contact_log 「맨 마지막에 만들고 뺄 수 있음」). M-6 이 꺼져 있으면 비어 있다.
CREATE TABLE contact_log (
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

-- 명세 2-6 ⑤ 「LLM용 읽기 롤은 name 열을 못 봄(열 단위 GRANT)」 → MCP 서버는 이 롤로 SET ROLE 한 뒤 읽는다.
DO $$ BEGIN
    CREATE ROLE covershift_llm_read NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT SELECT (employee_id, store_id, is_regular, has_qualification, closing_ok, weekly_days, fixed_weekdays)
    ON employees TO covershift_llm_read;
GRANT SELECT ON availability_submissions, store_time_rules, shift_periods TO covershift_llm_read;
GRANT covershift_llm_read TO CURRENT_USER;

-- G21) 위반의 종류(required/warning)를 저장 시점의 실제 값으로 남긴다. v17 에는 열이 없어서
--      API 가 규칙 표로 추정하고 있었다 → 규칙이 바뀌면 화면의 「필수/경고」가 어긋난다 (api 에이전트 계약 변경 요청 2).
ALTER TABLE validation_violations
    ADD COLUMN severity TEXT CHECK (severity IN ('required','warning'));
