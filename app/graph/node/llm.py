# 3-2) node/llm.py -- LLM(Claude)を用意する道具と、返事から編成案のJSONを取り出す道具
import os, json, re
from langchain_anthropic import ChatAnthropic
from settings import MODEL_NAME


LLM = None                        # テストのときは、ここにニセの LLM を入れる


def get_llm():                    # LLMのモデルを返す。テストのときは、ニセの LLM を返す
    global LLM
    if LLM is None:
        LLM = ChatAnthropic(model=MODEL_NAME, api_key=os.environ["ANTHROPIC_API_KEY"], max_tokens=64000, streaming=True, thinking={"type": "adaptive"}, effort="low")
    return LLM


def extract_json(text):           # LLMの返事から、完全なJSONを取り出す(後ろのブロックから探す)
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("assignments"):
            return data

    start, end = text.rfind('{"assignments"'), text.rfind("}")      # 囲みがないとき
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict) and data.get("assignments"):
                return data
            
        except json.JSONDecodeError:
            pass
        
    raise RuntimeError("返事の中に完全なJSONが見つかりません。llm_raw_output.txt を見てください")