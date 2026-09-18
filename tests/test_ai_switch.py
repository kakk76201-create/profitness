"""Смена модели распознавания одной переменной: параметры вызова под семейство
модели и отдельный клиент для фото (base_url/ключ провайдера)."""
import os, sys, pathlib, importlib
ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ["OPENAI_VISION_BASE_URL"] = "https://example.invalid/v1/"
os.environ["OPENAI_VISION_API_KEY"] = "vision-key"
os.environ["OPENAI_VISION_MODEL"] = "gpt-5-mini"
from backend import ai_service as A
importlib.reload(A)
fails = []
def chk(n, cond, x=""):
    if not cond: fails.append(n + ("  " + str(x) if x else ""))

chk("gpt-4o: классические параметры", A._completion_params("gpt-4o", 700, 0.3) == {"max_tokens": 700, "temperature": 0.3})
chk("gpt-5-mini: без temperature, max_completion_tokens", A._completion_params("gpt-5-mini", 700, 0.3) == {"max_completion_tokens": 700})
chk("gpt-5.6-luna: то же", A._completion_params("gpt-5.6-luna", 500, 0.5) == {"max_completion_tokens": 500})
chk("o4-mini: то же", "temperature" not in A._completion_params("o4-mini", 100, 0.1))
chk("gemini через совместимый API: классические", A._completion_params("gemini-3.1-flash-lite", 700, 0.3) == {"max_tokens": 700, "temperature": 0.3})
chk("VISION_MODEL из env", A.VISION_MODEL == "gpt-5-mini")
vc = A._get_vision_client()
chk("отдельный клиент для фото", vc is not A._get_client())
chk("base_url провайдера", str(vc.base_url).startswith("https://example.invalid/v1"), vc.base_url)
chk("ключ провайдера", vc.api_key == "vision-key")
chk("Whisper остаётся на OpenAI", str(A._get_transcribe_client().base_url).startswith("https://api.openai.com"), A._get_transcribe_client().base_url)
if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: параметры под GPT-5/o-серию и классические модели; отдельный vision-клиент с base_url/ключом; Whisper на OpenAI")
