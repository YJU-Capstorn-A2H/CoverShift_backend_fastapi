"""設定の読み込み。値は .env から読む(コードに直接書かない)。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # docker-compose.yml の設定(ポート5433)に合わせた、開発用の初期値
    database_url: str = "postgresql://covershift:covershift_dev@localhost:5433/covershift"

    # --- API用に追記 / API용 추가 ---
    jwt_secret: str | None = None      # .env の JWT_SECRET
    jwt_expire_minutes: int = 480      # トークンの有効分（8時間）/ 토큰 유효 분(8시간)
    dev_skip_auth: bool = False        # .env の DEV_SKIP_AUTH=1 で開発中だけログイン不要 / 개발 중에만 로그인 불필요
    link_token_days: int = 7           # LINE連携リンクの有効日数 / LINE 연동 링크 유효 일수

    # .env に書いてある、このクラスにない項目は無視する
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()