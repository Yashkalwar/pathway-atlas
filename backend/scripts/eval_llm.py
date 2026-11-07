"""Measure LLM-only tool routing accuracy on the eval set (requires Ollama).
Usage (from backend/): ../.venv/Scripts/python -m scripts.eval_llm [model]"""
import sys
import time

from app.llm import agent
from app.llm import tools as T
from tests.eval_questions import EVAL


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else agent.MODEL
    c = agent._client()
    ok, t0 = 0, time.time()
    for q, expected in EVAL:
        try:
            r = c.chat(model=model, messages=[{"role": "system", "content": agent.SYSTEM}, {"role": "user", "content": q}],
                       tools=T.ollama_tools(), think=False, options={"temperature": 0, "num_ctx": 4096}, keep_alive="30m")
            got = (r.message.tool_calls or [None])[0]
            got = got.function.name if got else None
        except Exception as e:  # noqa: BLE001
            got = f"error: {e}"[:60]
        ok += got == expected
        print(f"{'OK ' if got == expected else 'BAD'} {expected:<38} {str(got):<38} {q}")
    print(f"\n{model}: {ok}/{len(EVAL)} = {100 * ok / len(EVAL):.0f}% routing accuracy, {(time.time() - t0) / len(EVAL):.1f}s/question")


if __name__ == "__main__":
    main()
