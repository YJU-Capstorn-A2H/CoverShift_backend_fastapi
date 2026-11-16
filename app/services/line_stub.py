# app/services/line_stub.py
# LINEスタブ（D6）。本物は送らず、ログに出す。本物にするときはこの関数だけ差し替える。
# LINE 스텁(D6). 실제로는 보내지 않고 로그에 출력한다. 실제로 바꿀 때는 이 함수만 교체한다.
import logging

log = logging.getLogger("covershift.line")
SENT: list[dict] = []     # テストや画面表示用に控えを残す / 테스트나 화면 표시용으로 기록을 남긴다


def send_line(line_user_id: str, text: str) -> None:
    log.info("[LINE stub] to=%s text=%s", line_user_id, text)
    SENT.append({"line_user_id": line_user_id, "text": text})
