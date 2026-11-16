# このアプリには、どんなAPIがあって、起動と終了のときに何をするかを、まとめて決める
# 이 앱에 어떤 API가 있고, 기동과 종료 때 무엇을 하는지 정리한다
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from app.api import api_router
from app.core.db import close_pool, open_pool
from app.core.errors import install_error_handlers
from app.routers import health

# 環境変数を読み込む / 환경 변수를 읽는다
load_dotenv()

# アプリケーションのライフサイクルを管理する / 애플리케이션의 라이프사이클을 관리한다
@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()   # 起動時: DBにつなぐ / 기동 시: DB에 연결
    yield
    close_pool()  # 終了時: 接続を閉じる / 종료 시: 연결을 닫음


app = FastAPI(lifespan=lifespan)
install_error_handlers(app)         # エラー形式 {"error":{"code","message"}} に統一 / 에러 형식 통일
app.include_router(health.router)
app.include_router(api_router)      # 新しいAPI（/api/...）/ 새 API(/api/...)

# グラフ実行側(D3/Aegra)をつなぐときは、ここで1回だけ:
#   from app.services.graph_gateway import set_gateway; set_gateway(MyGateway())
