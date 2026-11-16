# scripts/bootstrap.py
# 運営用: 最初の店舗＋管理者の作成 / 招待コードの発行（APIではなくスクリプト。基準文書 11/04・11/08）
# 운영용: 첫 점포＋관리자 생성 / 초대 코드 발급(API가 아니라 스크립트. 기준 문서 11/04・11/08)
#   python -m scripts.bootstrap store  "店名" email@example.com   （パスワードは対話入力 / 비밀번호는 대화형 입력）
#   python -m scripts.bootstrap invite <store_id>
import getpass
import sys

from app.core.db import close_pool
from app.core.tx import tx
from app.services.ops import create_invite_code, create_store_with_manager


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("store", "invite"):
        print(__doc__)
        return 2
    try:
        with tx() as conn:
            if argv[0] == "store":
                if len(argv) != 3:
                    print("使い方: store <店名> <メール> / 사용법: store <점포명> <이메일>")
                    return 2
                pw = getpass.getpass("パスワード(8文字以上) / 비밀번호(8자 이상): ")   # 画面・履歴に残さない / 화면・이력에 남기지 않는다
                if len(pw) < 8:
                    print("短すぎます / 너무 짧습니다")
                    return 2
                print(create_store_with_manager(conn, argv[1], argv[2], pw))
            else:
                r = create_invite_code(conn, int(argv[1]))
                print(f"招待コード(今だけ表示) / 초대 코드(지금만 표시): {r['code']}  期限 / 만료: {r['expires_at']}")
    finally:
        close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))