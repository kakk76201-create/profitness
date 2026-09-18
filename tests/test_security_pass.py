"""Проход по безопасности: заголовки и CSP, фото прогресса в БД (+перенос
старых файлов с диска, +чужое фото 404), общий потолок ИИ с алертом,
предохранитель ALLOW_INSECURE_AUTH в проде, commit профиля только при изменении."""
import base64, hashlib, io, os, pathlib, re, subprocess, sys, tempfile
from unittest import mock

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "sec.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "1"
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ["PROGRESS_PHOTOS_DIR"] = os.path.join(tmp, "photos")
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

from PIL import Image
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal
from backend import models as M, ratelimit, auth as A
from backend.main import app

init_db()
c = TestClient(app)
fails = []


def chk(n, cond, x=""):
    if not cond:
        fails.append(n + ("  " + str(x) if x else ""))


# ---------- 1. Заголовки безопасности и CSP ----------
h = c.get("/").headers
chk("nosniff", h.get("x-content-type-options") == "nosniff")
chk("referrer-policy", "strict-origin" in h.get("referrer-policy", ""))
chk("permissions-policy", "camera=(self)" in h.get("permissions-policy", ""))
chk("no HSTS locally", "strict-transport-security" not in h)
csp = h.get("content-security-policy", "")
chk("frame-ancestors только Telegram",
    "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org" in csp, csp[:200])
script_src = csp.split("script-src", 1)[1].split(";")[0] if "script-src" in csp else ""
chk("script-src по хешам, без unsafe-inline", "'sha256-" in script_src and "'unsafe-inline'" not in script_src, script_src)
chk("CSP и на API", "content-security-policy" in c.get("/api/health").headers)
html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
bodies = [m.group(1) for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S | re.I)
          if m.group(1).strip()]
chk("инлайн-скрипты найдены", len(bodies) >= 2, len(bodies))
for b in bodies:
    hsh = "'sha256-" + base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode() + "'"
    chk("хеш инлайн-скрипта в CSP", hsh in script_src, hsh)

# ---------- 2. Фото прогресса хранится в БД ----------
buf = io.BytesIO(); Image.new("RGB", (2400, 1200), (200, 90, 10)).save(buf, "PNG")
r = c.post("/progress/upload", files={"file": ("a.png", buf.getvalue(), "image/png")}, data={"weight": "80"})
chk("upload 200", r.status_code == 200, r.text[:200])
pid = r.json()["id"]
with SessionLocal() as db:
    row = db.get(M.ProgressPhoto, pid)
    chk("снимок в БД, не на диске", row.image_data is not None and row.photo_path is None)
    chk("mime jpeg", row.image_mime == "image/jpeg")
    stored = bytes(row.image_data)
im2 = Image.open(io.BytesIO(stored))
chk("пережат до 1600", max(im2.size) <= 1600, im2.size)
chk("EXIF стёрт", not im2.info.get("exif"))
r = c.get(f"/progress/{pid}/image")
chk("выдача 200 jpeg", r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg"), r.status_code)
chk("private no-store", "no-store" in r.headers.get("cache-control", ""))
chk("байты совпадают", r.content == stored)
r = c.post("/progress/upload", files={"file": ("a.png", b"not an image", "image/png")})
chk("мусор вместо картинки → 400", r.status_code == 400, r.status_code)
chk("удаление", c.delete(f"/progress/{pid}").status_code == 200)

# ---------- 3. Старый файл с диска переносится в БД при первой выдаче ----------
pdir = pathlib.Path(os.environ["PROGRESS_PHOTOS_DIR"]); pdir.mkdir(parents=True, exist_ok=True)
legacy = pdir / "1_legacy.jpg"; Image.new("RGB", (50, 50), (1, 2, 3)).save(legacy, "JPEG")
with SessionLocal() as db:
    ph = M.ProgressPhoto(telegram_id=1, photo_path="1_legacy.jpg", date="2026-01-01")
    db.add(ph); db.commit(); lid = ph.id
r = c.get(f"/progress/{lid}/image")
chk("старый файл отдан", r.status_code == 200 and r.content == legacy.read_bytes(), r.status_code)
with SessionLocal() as db:
    row = db.get(M.ProgressPhoto, lid)
    chk("старый файл перенесён в БД", row.image_data is not None and row.image_mime == "image/jpeg")
legacy.unlink()
chk("после потери диска отдаётся из БД", c.get(f"/progress/{lid}/image").status_code == 200)
with SessionLocal() as db:
    db.add(M.User(telegram_id=2, username="x")); db.commit()
    ph = M.ProgressPhoto(telegram_id=2, image_data=b"xx", image_mime="image/jpeg", date="2026-01-01")
    db.add(ph); db.commit(); oid = ph.id
chk("чужое фото → 404", c.get(f"/progress/{oid}/image").status_code == 404)

# ---------- 4. Общий потолок ИИ ----------
ratelimit.AI_GLOBAL_PER_DAY = 2; ratelimit.AI_GLOBAL_PER_MIN = 100
ratelimit._day_hits.clear(); ratelimit._minute_hits.clear()
ratelimit._alert_state.update(day=-1, warned=False, capped=False)
sent = []
ratelimit._alert_owner_async = lambda text: sent.append(text)
try:
    ratelimit.enforce_ai(101); ratelimit.enforce_ai(102)
    chk("два вызова в потолке", True)
except HTTPException as e:
    chk("два вызова в потолке", False, e.detail)
try:
    ratelimit.enforce_ai(103)
    chk("третий упирается в потолок", False)
except HTTPException as e:
    chk("код service_busy", e.status_code == 429 and e.detail.get("error") == "service_busy", e.detail)
chk("алерт владельцу отправлен", any("исчерпан" in t for t in sent), sent)
chk("два алерта: 80 % и упор", len(sent) == 2 and "80 %" in sent[0], sent)
try:
    ratelimit.enforce_ai(104)
except HTTPException:
    pass
chk("повторный упор без нового алерта", len(sent) == 2, len(sent))

# ---------- 5. Предохранитель: ALLOW_INSECURE_AUTH в проде роняет старт ----------
guard_db = os.path.join(tmp, "guard.db").replace("\\", "/")
code = (
    "import os; os.environ['ALLOW_INSECURE_AUTH']='1'; os.environ['RAILWAY_ENVIRONMENT']='production'; "
    f"os.environ['DATABASE_URL']='sqlite:///{guard_db}'; os.environ['ENABLE_SCHEDULER']='0'; import backend.main"
)
proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=180)
chk("старт в проде с небезопасной авторизацией падает",
    proc.returncode != 0 and "ALLOW_INSECURE_AUTH" in (proc.stderr + proc.stdout), proc.stderr[-300:])

# ---------- 6. Профиль: commit только при изменениях ----------
with SessionLocal() as db:
    A._upsert_user(db, telegram_id=555, username="u", first_name="F", photo_url=None, language_code="ru")
    with mock.patch.object(db, "commit", wraps=db.commit) as cm:
        A._upsert_user(db, telegram_id=555, username="u", first_name="F", photo_url=None, language_code="ru")
        chk("без изменений — без commit", cm.call_count == 0, cm.call_count)
        A._upsert_user(db, telegram_id=555, username="u2", first_name="F", photo_url=None)
        chk("изменение — один commit", cm.call_count == 1, cm.call_count)
    u = db.query(M.User).filter(M.User.telegram_id == 555).first()
    chk("username обновлён", u.username == "u2")

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: заголовки и CSP по хешам; фото прогресса в БД (пережатие, без EXIF, перенос с диска,\n"
      "    чужое 404); общий потолок ИИ с одним алертом; предохранитель прода; commit профиля по изменению")
