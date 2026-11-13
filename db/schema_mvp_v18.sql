-- CoverShift MVP schema v18 (2026-11-13) 【提案】v17 ＋ manager_actions・contact_log 基準文書(11/08版)を反映＋希望テーブル＋closing_ok＋半休(half_leave)  / 기준 문서(11/08판) 반영 + 희망 테이블 + closing_ok + 반휴(half_leave)

-- 1) 管理者アカウント
-- 1) 관리자 계정
CREATE TABLE manager_accounts (
    manager_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT true      -- 店長交代で古いアカウントは false にする（消さない）  / 점장이 바뀌면 옛 계정은 false로 한다(지우지 않는다)
);

-- 2) 店舗（1アカウント = 1店舗）
-- 2) 점포(계정 1개 = 점포 1개)
CREATE TABLE stores (
    store_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    manager_id  BIGINT NOT NULL UNIQUE REFERENCES manager_accounts(manager_id),
    name        TEXT NOT NULL
);

-- 2-b) 招待コード（運営が発行 → 新しい管理者が登録に使う。1回だけ・1日有効。11/04 승혁 결정）
-- 2-b) 초대 코드(운영 측이 발급 → 새 관리자가 등록에 사용. 1회만・1일 유효. 11/04 승혁 결정)
CREATE TABLE invite_codes (
    code        TEXT PRIMARY KEY,                                       -- ランダムな文字列（本番はハッシュ保存を推奨）  / 무작위 문자열(실서비스는 해시 저장 권장)
    store_id    BIGINT NOT NULL REFERENCES stores(store_id),            -- どの店舗の管理者を引き継ぐか  / 어느 점포의 관리자를 이어받는지
    expires_at  TIMESTAMPTZ NOT NULL,                                   -- 発行から1日  / 발급 후 1일
    used_at     TIMESTAMPTZ,
    used_by     BIGINT REFERENCES manager_accounts(manager_id),         -- 登録して新しく管理者になった人  / 등록해서 새로 관리자가 된 사람
    CHECK ((used_at IS NULL) = (used_by IS NULL))
);

-- 3) 曜日×時間帯ごとの初期設定（時間帯は1日3つ程度。1時間帯 = 1行）
-- 3) 요일×시간대별 초기 설정(시간대는 하루 3개 정도. 1시간대 = 1행)
--    開始・終了は「その曜日の一番早い開始 / 一番遅い終了」から読み取る。
--   시작・종료는 「그 요일의 가장 이른 시작 / 가장 늦은 종료」에서 읽는다.
--    資格者 = 登録販売者（MVPは登販のみ）。
--   자격자 = 등록판매자(MVP는 등판만).
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE store_time_rules (
    rule_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id         BIGINT   NOT NULL REFERENCES stores(store_id),
    weekday          SMALLINT NOT NULL CHECK (weekday BETWEEN 1 AND 7),   -- 1=月 ... 7=日  / 1=월 ... 7=일
    start_time       TIME     NOT NULL,
    end_time         TIME     NOT NULL,
    min_headcount    SMALLINT NOT NULL CHECK (min_headcount >= 1),        -- 休憩込みの必要人数  / 휴식 포함 필요 인원
    min_qualified    SMALLINT NOT NULL DEFAULT 0 CHECK (min_qualified >= 0),   -- 時間帯ごとの必要な資格者数（基準文書 11/07）  / 시간대별 필요한 자격자 수(기준 문서 11/07)
    CHECK (end_time > start_time),
    EXCLUDE USING gist (store_id WITH =, weekday WITH =,
        tsrange(('2000-01-01'::date + start_time), ('2000-01-01'::date + end_time)) WITH &&)
);

-- 4) 従業員登録
-- 4) 직원 등록
CREATE TABLE employees (
    employee_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id                 BIGINT  NOT NULL REFERENCES stores(store_id),
    name                     TEXT    NOT NULL,
    is_regular               BOOLEAN NOT NULL,                      -- true=社員, false=アルバイト等  / true=정직원, false=알바 등
    has_qualification        BOOLEAN NOT NULL DEFAULT false,        -- 登録販売者かどうか  / 등록판매자인지
    closing_ok       BOOLEAN NOT NULL DEFAULT false,         -- 22:30の閉店まで残ってよい人か（検証ルール「22:30まではclosing_okの人だけ」。11/12 アズキ決定）  / 22:30 폐점까지 남아도 되는 사람인가(검증 규칙 「22:30까지는 closing_ok인 사람만」. 11/12 아즈키 결정)
    weekly_days              SMALLINT CHECK (weekly_days BETWEEN 1 AND 7),         -- 週に出る日数（曜日は問わない人向け。空欄=決まりなし）  / 주에 나오는 일수(요일은 상관없는 사람용. 빈칸=정해진 것 없음)
    fixed_weekdays           SMALLINT[] CHECK (fixed_weekdays <@ ARRAY[1,2,3,4,5,6,7]::SMALLINT[]),  -- 毎週出る曜日が固定の人向け（例 {1,3,5}=月水金。空欄=固定なし）  / 매주 나오는 요일이 고정인 사람용(예 {1,3,5}=월수금. 빈칸=고정 없음)
    line_user_id             TEXT UNIQUE,                           -- NULL = LINE未連携  / NULL = LINE 미연동
    link_token               TEXT UNIQUE,                           -- 初回LINE連携リンクの合言葉（連携したら消す）  / 첫 LINE 연동 링크의 암호(연동하면 지운다)
    link_token_expires_at    TIMESTAMPTZ,
    CHECK ((link_token IS NULL) = (link_token_expires_at IS NULL)),
    CHECK (fixed_weekdays IS NULL OR weekly_days IS NULL OR weekly_days = cardinality(fixed_weekdays))
);

-- 5) 毎月の設定（何月分か・提出期限・社員の公休日数）
-- 5) 월별 설정(몇 월분인지・제출 기한・정직원 공휴일수)
CREATE TABLE shift_periods (
    period_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id             BIGINT      NOT NULL REFERENCES stores(store_id),
    target_month         DATE        NOT NULL CHECK (EXTRACT(DAY FROM target_month) = 1),  -- 2026-12-01 = 12月分  / 2026-12-01 = 12월분
    submission_deadline  TIMESTAMPTZ NOT NULL,                                              -- 希望の提出期限  / 희망 제출 기한
    designated_off_days  SMALLINT    NOT NULL CHECK (designated_off_days BETWEEN 0 AND 31), -- 社員1人あたりの月の公休日数  / 정직원 1명당 월 공휴일수
    off_request_limit    SMALLINT    CHECK (off_request_limit BETWEEN 0 AND 31),            -- 社員が出せる希望休の上限日数（空欄=制限なし）  / 정직원이 낼 수 있는 희망휴 상한 일수(빈칸=제한 없음)
    work_request_limit   SMALLINT    CHECK (work_request_limit BETWEEN 0 AND 31),           -- 社員が出せる希望出勤日の上限日数（空欄=制限なし）  / 정직원이 낼 수 있는 희망 출근일 상한 일수(빈칸=제한 없음)
    thread_id            TEXT        UNIQUE,                                                  -- 編成期間1つ=thread1つ（LangGraph/Aegra。11/07 승혁）  / 편성 기간 1개 = thread 1개(LangGraph/Aegra. 11/07 승혁)
    UNIQUE (store_id, target_month)
);

-- 6) 毎月の提出リンク（有効期限 = その月の提出期限。期限の列は持たない）
-- 6) 월별 제출 링크(유효 기한 = 그 달의 제출 기한. 기한 열은 두지 않는다)
CREATE TABLE submission_links (
    token        TEXT   PRIMARY KEY,
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    period_id    BIGINT NOT NULL REFERENCES shift_periods(period_id),
    UNIQUE (employee_id, period_id)      -- 1人に月1本。再発行は作り直し  / 1명에 월 1개. 재발급은 새로 만든다
);

-- 7) 従業員の希望（1人×1日で1行。休み = 時刻が空欄）
-- 7) 직원의 희망(1명×1일에 1행. 휴일 = 시각이 빈칸)
CREATE TABLE availability_submissions (
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    work_date    DATE   NOT NULL,
    kind         TEXT   NOT NULL CHECK (kind IN ('available','off','paid_leave','half_leave')),
        -- available  = 出られる（時間つき）。時間の範囲内で、AIが入れる時間を決めてよい。
        -- available  = 나올 수 있음(시간 포함). 시간 범위 안에서 AI가 넣을 시간을 정해도 된다.
        --              アルバイト/パートの「出勤可能時間」、社員の「出勤希望日」
        --             알바/파트의 「출근 가능 시간」, 정직원의 「출근 희망일」
        -- off        = 希望休（社員の希望休。上限は shift_periods.off_request_limit、アプリで数える）
        -- off        = 희망휴(정직원의 희망휴. 상한은 shift_periods.off_request_limit, 앱에서 센다)
        -- paid_leave = 有給の申請（全日。承認された案で、シフトには載るが出勤ではない）
        -- paid_leave = 유급 신청(전일. 승인된 안에서 시프트에는 실리지만 출근은 아니다)
        --              ※パート・アルバイトに許すかは未決
        --             ※파트・알바에게 허용할지는 미결
        -- half_leave = 半休（時刻つき。その時間どおりに出勤として案へ入れる＝範囲内で自由ではない）。
        -- half_leave = 반휴(시각 포함. 그 시간 그대로 출근으로 안에 넣는다 = 범위 안 자유가 아님).
        --              社員・パート・アルバイトとも出せる。希望休・出勤希望の上限には数えない。給与の扱いは未決
        --             정직원・파트・알바 모두 낼 수 있다. 희망휴・출근 희망 상한에는 세지 않는다. 급여 취급은 미결
        -- 行なし     = 社員:希望なし（配置してよい） / アルバイト・パート:出られない
        -- 행 없음    = 정직원: 희망 없음(배치해도 됨) / 알바・파트: 나올 수 없음
    start_time   TIME,
    end_time     TIME,
    PRIMARY KEY (employee_id, work_date),                          -- 1人1日1行。同じ日の再入力は上書き(UPSERT)  / 1명 1일 1행. 같은 날 재입력은 덮어쓰기(UPSERT)
    CHECK ((kind IN ('available','half_leave')) = (start_time IS NOT NULL AND end_time IS NOT NULL)),
    CHECK (start_time IS NULL OR end_time > start_time),
    CHECK (start_time IS NULL OR (EXTRACT(MINUTE FROM start_time) IN (0,30)
                              AND EXTRACT(MINUTE FROM end_time)   IN (0,30)))
);

-- 8) シフト案（AIが作る案・確定した案）。1か月分の案が何度か作られる
-- 8) 시프트 안(AI가 만드는 안・확정된 안). 한 달분의 안이 여러 번 만들어진다
CREATE TABLE shift_drafts (
    draft_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id   BIGINT NOT NULL REFERENCES shift_periods(period_id),
    status      TEXT   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','confirmed')),
    parent_draft_id BIGINT REFERENCES shift_drafts(draft_id),             -- やり直しの元になった案（検証の指摘を受けて作り直した場合）  / 다시 만들기의 바탕이 된 안(검증 지적을 받아 다시 만든 경우)
    confirmed_at  TIMESTAMPTZ,                                            -- 管理者が承認した時刻  / 관리자가 승인한 시각
    confirmed_by  BIGINT REFERENCES manager_accounts(manager_id),         -- 承認した管理者（店長交代後も残る）  / 승인한 관리자(점장이 바뀐 뒤에도 남는다)
    CHECK ((status = 'confirmed') = (confirmed_at IS NOT NULL)),
    CHECK ((confirmed_at IS NULL) = (confirmed_by IS NULL)),
    CHECK (parent_draft_id IS NULL OR parent_draft_id <> draft_id)
);
-- 確定できる案は、1か月につき1つだけ
-- 확정할 수 있는 안은 한 달에 하나뿐
CREATE UNIQUE INDEX one_confirmed_draft_per_period ON shift_drafts(period_id) WHERE status = 'confirmed';

-- 9) 案の中身（誰が・いつ）。1人1日1行。
-- 9) 안의 내용(누가・언제). 1명 1일 1행.
--    kind='work' は出勤（時刻あり）、'paid_leave' は有給（シフトには載るが出勤ではない。時刻は空欄）
--   kind='work'는 출근(시각 있음), 'paid_leave'는 유급(시프트에는 실리지만 출근은 아님. 시각은 빈칸)
--    半休(half_leave)の日も kind='work' で時刻つきの出勤として入れる（出勤日数に1日と数える）。
--   반휴(half_leave) 날도 kind='work'로 시각이 있는 출근으로 넣는다(출근일수에 1일로 센다).
--    半休かどうかは availability_submissions で判定する（この表には印を持たない）
--   반휴인지는 availability_submissions로 판정한다(이 표에는 표시를 두지 않는다)
CREATE TABLE shift_assignments (
    draft_id     BIGINT NOT NULL REFERENCES shift_drafts(draft_id),
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    work_date    DATE   NOT NULL,
    kind         TEXT   NOT NULL DEFAULT 'work' CHECK (kind IN ('work','paid_leave')),
    start_time   TIME,
    end_time     TIME,
    break_start  TIME,          -- 休憩の開始（記録だけ。validateでは見ない）  / 휴식 시작(기록만. validate에서는 보지 않는다)
    break_end    TIME,
    PRIMARY KEY (draft_id, employee_id, work_date),
    CHECK ((kind = 'work') = (start_time IS NOT NULL AND end_time IS NOT NULL)),
    CHECK ((start_time IS NULL) = (end_time IS NULL)),
    CHECK (end_time > start_time),
    CHECK ((break_start IS NULL) = (break_end IS NULL)),
    CHECK (break_start IS NULL OR (kind = 'work' AND break_end > break_start AND break_start >= start_time AND break_end <= end_time)),
    CHECK (EXTRACT(MINUTE FROM start_time) IN (0,30) AND EXTRACT(MINUTE FROM end_time) IN (0,30))
);

-- 10) 業務の一覧（管理者が自由に設定。sort_order は「簡単な順」などの表示順）
-- 10) 업무 목록(관리자가 자유롭게 설정. sort_order는 「쉬운 순」 등의 표시 순서)
CREATE TABLE tasks (
    task_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id    BIGINT   NOT NULL REFERENCES stores(store_id),
    name        TEXT     NOT NULL,
    sort_order  SMALLINT NOT NULL DEFAULT 0,
    UNIQUE (store_id, name)
);

-- 11) 誰がどの業務を一人でできるか【管理者が確認して確定した結果】（行がある = できる。行がない = 数えない）
-- 11) 누가 어느 업무를 혼자 할 수 있는가【관리자가 확인해서 확정한 결과】(행 있음 = 가능. 행 없음 = 세지 않음)
--     validateが信頼するのはこの表だけ。評価（自己・他己）は下の evaluations に溜めて、管理者が見て確定する
--    validate가 신뢰하는 것은 이 표뿐. 평가(자기・타인)는 아래 evaluations에 모아 두고 관리자가 보고 확정한다
CREATE TABLE employee_skills (
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),
    task_id      BIGINT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    PRIMARY KEY (employee_id, task_id)
);

-- 12) 時間帯ごとに、その業務をできる人が最低何人必要か
-- 12) 시간대별로 그 업무를 할 수 있는 사람이 최소 몇 명 필요한가
CREATE TABLE store_task_needs (
    rule_id     BIGINT   NOT NULL REFERENCES store_time_rules(rule_id) ON DELETE CASCADE,
    task_id     BIGINT   NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    min_people  SMALLINT NOT NULL CHECK (min_people >= 1),
    PRIMARY KEY (rule_id, task_id)
);

-- 13) 業務ごとの評価（自己評価と他己評価）
-- 13) 업무별 평가(자기 평가와 타인 평가)
--     自己評価: evaluator_id = employee_id（日付なし）
--    자기 평가: evaluator_id = employee_id(날짜 없음)
--     他己評価: 一緒に勤務した社員が付ける（work_date = 一緒に勤務した日）
--    타인 평가: 함께 근무한 정직원이 매긴다(work_date = 함께 근무한 날)
CREATE TABLE evaluations (
    evaluation_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id    BIGINT NOT NULL REFERENCES employees(employee_id),             -- 評価される人  / 평가받는 사람
    task_id        BIGINT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    evaluator_id   BIGINT NOT NULL REFERENCES employees(employee_id),             -- 評価する人  / 평가하는 사람
    rating         TEXT   NOT NULL CHECK (rating IN ('solo','guided')),           -- solo=一人でできる / guided=指導が必要  / solo=혼자 가능 / guided=지도 필요
    work_date      DATE,
    CHECK ((evaluator_id = employee_id) = (work_date IS NULL))
);
-- 自己評価は 人×業務 で1つ（書き直しは上書き）
-- 자기 평가는 사람×업무로 1개(다시 쓰면 덮어쓰기)
CREATE UNIQUE INDEX one_self_evaluation ON evaluations(employee_id, task_id) WHERE evaluator_id = employee_id;
-- 他己評価は 同じ評価者が、同じ日に、同じ人の同じ業務を2回つけられない
-- 타인 평가는 같은 평가자가 같은 날 같은 사람의 같은 업무를 2번 매길 수 없다
CREATE UNIQUE INDEX one_other_evaluation ON evaluations(employee_id, task_id, evaluator_id, work_date) WHERE evaluator_id <> employee_id;

-- 14) 検証で引っかかった内容（1件1行で溜める。次の作り直しにAIへ渡す「コメント」の元にもなる）
-- 14) 검증에서 걸린 내용(1건 1행으로 쌓는다. 다음 재작성 때 AI에 넘기는 「코멘트」의 바탕도 된다)
--     コメントの文章は保存せず、check_code と detail からコードで作る（同じ指摘は同じ文章になる）
--    코멘트 문장은 저장하지 않고 check_code와 detail에서 코드로 만든다(같은 지적은 같은 문장이 된다)
CREATE TABLE validation_violations (
    violation_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    draft_id      BIGINT NOT NULL REFERENCES shift_drafts(draft_id),   -- どの案で見つかったか  / 어느 안에서 발견됐는가
    check_code    TEXT   NOT NULL,                                      -- どのチェックか（6種類など）  / 어느 검사인가(6종류 등)
    work_date     DATE,                                                 -- 該当する日（なければ空欄）  / 해당하는 날(없으면 빈칸)
    employee_id   BIGINT REFERENCES employees(employee_id),             -- 該当する人（なければ空欄。名前は持たない）  / 해당하는 사람(없으면 빈칸. 이름은 갖지 않는다)
    detail        JSONB,                                                -- チェックごとの詳しい数字（例: {"need":2,"have":1,"time":"13:00"}）  / 검사별 상세 숫자(예: {"need":2,"have":1,"time":"13:00"})
    allowed_by    BIGINT REFERENCES manager_accounts(manager_id),       -- 例外として許可した管理者  / 예외로 허가한 관리자
    allowed_at    TIMESTAMPTZ,
    CHECK ((allowed_by IS NULL) = (allowed_at IS NULL))
);
CREATE INDEX ON validation_violations(draft_id);
CREATE INDEX ON validation_violations(check_code, work_date);

-- ============================================================
-- v18 【提案】 追加の2テーブル（v17の15テーブルは変更なし）/ v18 【제안】 추가 2개 테이블(v17의 15개 테이블은 변경 없음)
-- 根拠: 基準文書 11/07「まだ受け取っていないテーブル」/ 근거: 기준 문서 11/07 「아직 못 받은 테이블」
-- ※ 列・値は推測を含む（推測です）。アズキの承認と승혁・혁준の確認を待つ。/ ※ 열・값에는 추측이 포함됨(추측입니다). 아즈키 승인과 승혁・혁준 확인 대기.
-- ============================================================

-- 15) 店長の操作の記録 / 점장 조작 기록（承認・再編成要求・編集保存 / 승인・재편성 요청・편집 저장）
--     基準文書: 「承認の二重防止はDB制約」「店長指示の枠ルールは manager_actions のJSON欄に保存。collectが読んで State の extra_rules に移す」
--     기준 문서: 「승인 두 번 방지는 DB 제약」「점장 지시 틀 규칙은 manager_actions의 JSON 칸에 저장. collect가 읽어 State의 extra_rules로 옮긴다」
CREATE TABLE manager_actions (
    action_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    draft_id          BIGINT NOT NULL REFERENCES shift_drafts(draft_id),         -- どの案への操作か / 어느 안에 대한 조작인가
    manager_id        BIGINT NOT NULL REFERENCES manager_accounts(manager_id),   -- 操作した管理者 / 조작한 관리자
    action_type       TEXT   NOT NULL CHECK (action_type IN ('approve','redraft_request','edit_save')),
        -- approve = 承認 / 승인,  redraft_request = 再編成要求 / 재편성 요청(지시 포함 가능),  edit_save = 編集保存 / 편집 저장（State の decision 3値に対応 / State의 decision 3값에 대응）
    instruction_text  TEXT,    -- 再編成要求のときの店長の指示文（任意）/ 재편성 요청 때 점장의 지시문(선택)
    rules             JSONB,   -- 指示を枠ルールにした結果（not_available / max_consecutive / min_headcount の配列）。例 / 예: [{"type":"min_headcount","weekday":6,"start":"17:00","end":"22:30","count":5}]
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (action_type = 'redraft_request' OR (instruction_text IS NULL AND rules IS NULL)),   -- 指示は再編成要求のときだけ / 지시는 재편성 요청일 때만
    CHECK (rules IS NULL OR jsonb_typeof(rules) = 'array')
);
-- 同じ案に「承認」は1回だけ（ボタンの二度押しは入口のロックでも止めるが、DBでもう一度止める）
-- 같은 안에 「승인」은 1번만(버튼 두 번 누름은 입구 락으로도 막지만 DB에서도 한 번 더 막는다)
CREATE UNIQUE INDEX one_approve_per_draft ON manager_actions(draft_id) WHERE action_type = 'approve';
CREATE INDEX ON manager_actions(draft_id, created_at);

-- 16) 従業員への連絡の記録 / 직원 연락 기록（MVPはLINEスタブ。送らずに記録・画面表示 / MVP는 LINE 스텁. 보내지 않고 기록・화면 표시）
--     基準文書: 「従業員連絡(contact)は最後に付け、遅ければ外す」「無応答2時間」「返信ボタン」/ 기준 문서: 「직원 연락(contact)은 맨 마지막에 붙이고 늦으면 뺀다」「무응답 2시간」「답장 버튼」
CREATE TABLE contact_log (
    contact_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    draft_id     BIGINT NOT NULL REFERENCES shift_drafts(draft_id),         -- どの案についての連絡か / 어느 안에 대한 연락인가
    employee_id  BIGINT NOT NULL REFERENCES employees(employee_id),         -- 宛先（名前は持たない）/ 수신자(이름은 갖지 않는다)
    kind         TEXT   NOT NULL DEFAULT 'shift_notice' CHECK (kind IN ('shift_notice')),   -- 連絡の種類。MVPは1種類（推測です。増やすときはCHECKを直す）/ 연락 종류. MVP는 1종류(추측입니다)
    channel      TEXT   NOT NULL DEFAULT 'line_stub' CHECK (channel IN ('line_stub','line')),   -- line_stub = 送らず記録・画面表示 / 보내지 않고 기록・화면 표시
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reply        TEXT   CHECK (reply IN ('ok','ng')),   -- 返信ボタンの結果（値は推測です）。NULL = 未返信 / 답장 버튼 결과(값은 추측입니다). NULL = 미응답
    replied_at   TIMESTAMPTZ,
    CHECK ((reply IS NULL) = (replied_at IS NULL))
);
CREATE INDEX ON contact_log(draft_id, employee_id);
CREATE INDEX contact_log_waiting ON contact_log(sent_at) WHERE reply IS NULL;   -- 無応答の確認用 / 무응답 확인용