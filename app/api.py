# app/api.py
# すべてのルーターを /api の下にまとめる。ここに足せば main.py は変えなくてよい。
# 모든 라우터를 /api 아래로 모은다. 여기에 추가하면 main.py는 바꾸지 않아도 된다.
from fastapi import APIRouter

from app.routers import auth, drafts, employees, not_implemented, periods, public, store

api_router = APIRouter(prefix="/api")

# 実装済み / 구현 완료
# ※ 認証（register/login/logout）は E9（ヒョクジュン担当）。ここには含めない / 인증은 E9(혁준 담당). 여기에는 포함하지 않는다
api_router.include_router(auth.router, tags=["2-2 認証 / 인증"])
api_router.include_router(store.router, tags=["2-3 店舗・時間帯ルール / 점포・시간대 규칙"])
api_router.include_router(employees.router, tags=["2-2 従業員・業務 / 직원・업무"])
api_router.include_router(periods.router, tags=["月・提出リンク・編成開始 / 월・제출 링크・편성 시작"])
api_router.include_router(public.router, tags=["リンク用(ログイン不要) / 링크용(로그인 불필요)"])
api_router.include_router(drafts.router, tags=["005-初稿・承認 / 초안・승인"])

# 入口だけ（501/410）。実装済みより後に入れる（同じ道があれば実装済みが優先）
# 입구만(501/410). 구현 완료보다 뒤에 넣는다(같은 경로가 있으면 구현 완료가 우선)
api_router.include_router(not_implemented.router, tags=["未実装・廃止 / 미구현・폐지"])
