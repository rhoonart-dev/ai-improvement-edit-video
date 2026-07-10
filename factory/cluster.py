#!/usr/bin/env python
"""원작(IP) 클러스터 분류 — eb_ip 레지스트리.

grain = 원작(IP) 1편. 라이선스 유무 무관하게 담되(is_laeebly_licensed 구분), 현재 분류는
  laeebly 라이선스 원작(identification_code 있음)만. PK=ip_key(라이선스: code / 비라이선스: 후속).
클러스터 = 포맷(축1) × 서사구조(축2). 정서톤(축3)은 condition → tone_tags(멀티라벨).

흐름 (동기, 쇼츠 성과 적재 시점):
  resolve_cluster(원작 메타)
    → eb_ip 조회(ip_key)
        있으면  → (cluster_id, tone_tags) 반환                (LLM 0)
        없으면  → Gemini 분류 → 행 insert → 반환              (원작당 최초 1회)
원작이 안 붙는 쇼츠(identification_code·title 모두 없음)는 (None, None) → cluster 미부여.

seed_from_xlsx: 손으로 만든 분류 xlsx(포맷×서사×톤)를 laeebly licensed_video와
  제목으로 조인해 identification_code별 'xlsx_seed' 행으로 선적재(재분류 방지).
"""
import json
import re
from pathlib import Path

from config import HERE, IDENTIFY_TEXT_CONF

# ── 축 enum (xlsx 축_정의 기준) ──────────────────────────────────────
FORMAT_AXES = ["드라마", "숏드라마", "영화", "예능", "애니메이션", "논픽션"]
NARRATIVE_AXES = ["연속서사", "에피소드완결", "단편완결", "비서사"]
XLSX_PATH = HERE / "licensed_video_classification.xlsx"   # factory 내부 사본(독립)

# laeebly video_type → 포맷 축 힌트(규칙 1차, LLM이 최종 판단·보정)
VIDEO_TYPE_HINT = {
    "드라마": "드라마", "숏드라마": "숏드라마", "영화": "영화", "예능": "예능",
    "애니메이션": "애니메이션", "다큐멘터리": "논픽션",
    "웹콘텐츠": "예능", "유튜브": "예능",
}


def make_cluster_id(fmt: str, narr: str) -> str | None:
    if not fmt or not narr:
        return None
    return f"{fmt}×{narr}"


def _norm_title(t: str) -> str:
    """제목 매칭용 정규화 — 괄호주석·시즌표기·공백·기호 제거, 소문자.
       같은 원작이 같은 키로 묶이도록. (예: '맨 끝줄 소년 (2025)' → '맨끝줄소년')"""
    if not t:
        return ""
    t = str(t)
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]|《[^》]*》", " ", t)   # 괄호류 주석 제거
    t = re.sub(r"(시즌|season|s)\s*\d+", " ", t, flags=re.I)  # 시즌 표기
    t = re.sub(r"[\s\-_·:,.!?~'\"“”‘’#]+", "", t)          # 공백·기호
    return t.strip().lower()


def ip_key_for(identification_code, title=None) -> str | None:
    """eb_ip PK. 라이선스=코드 그대로 / 비라이선스=정규화 제목에 't:' 접두어(충돌 방지)."""
    if identification_code:
        return identification_code
    nt = _norm_title(title)
    return f"t:{nt}" if nt else None


# ═══════════════════════════════════════════════════════════════════
# Gemini JSON 헬퍼 — 재시도 + 배열/코드펜스 처리 (extract_v04 패턴)
# ═══════════════════════════════════════════════════════════════════
def _gemini_json(contents, key, model, tries=3, temp=0.0):
    """contents로 generate → JSON dict 반환. 실패/배열/코드펜스 처리. 실패 시 None."""
    if not key:
        return None
    try:
        from google import genai
    except Exception:
        return None
    client = genai.Client(api_key=key)
    last = None
    for _ in range(tries):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents,
                config={"response_mime_type": "application/json", "temperature": temp})
            txt = (resp.text or "").strip()
            if txt.startswith("```"):
                txt = txt.strip("`")
                txt = txt[4:].strip() if txt.lower().startswith("json") else txt
            data = json.loads(txt)
            if isinstance(data, list):
                data = next((x for x in data if isinstance(x, dict)), {})
            return data
        except Exception as e:
            last = e
    return None


# ═══════════════════════════════════════════════════════════════════
# 비라이선스 원작 식별 — description(1순위) + 영상(2순위) → 원작+포맷+서사+장르
# ═══════════════════════════════════════════════════════════════════
IDENTIFY_PROMPT = """이 쇼츠가 '기존 영상 작품'(드라마·숏드라마·영화·예능·애니메이션·다큐 등)을
편집한 클립인지 식별하고, 맞다면 그 원작을 특정하라.
판단 근거 우선순위: ①설명란의 원작명·시놉시스·OTT링크 → ②화면 하단 로고·자막 → ③배우·장면.

has_source_video = true : 위와 같은 기존 '영상 작품'을 잘라 편집한 클립.
has_source_video = false: 원본 영상 작품이 없는 자체제작 — 이미지+TTS 논평·이슈정리·밈·
   게임플레이 짜깁기·개인 브이로그 등. (이 경우 source_title은 null)
★설명란에 원작명·시놉시스·OTT링크가 명시돼 있으면 화면(배우 얼굴 등)보다 그것을 우선한다.

[축1 포맷] 드라마 / 숏드라마 / 영화 / 예능 / 애니메이션 / 논픽션 중 하나
[축2 서사구조] 연속서사 / 에피소드완결 / 단편완결 / 비서사 중 하나
[축3 정서톤/장르] 멀티라벨 배열 (예: ["로맨스","코미디"])

출력 JSON만(설명·코드펜스 금지):
{{"has_source_video": true 또는 false,
  "source_title": "원작 제목 정확히 (has_source_video=false면 null)",
  "format_axis": "...", "narrative_axis": "...", "tone_tags": ["..."],
  "confidence": 0~1, "evidence": "근거 1문장"}}

[제목] {title}
[설명란] {desc}"""


def identify_unlicensed(ctl, description, video_path, key, model):
    """비라이선스 원작 식별. description(+영상) → 분류 dict. video_path=None이면 텍스트만.
       실패 시 None."""
    prompt = IDENTIFY_PROMPT.format(
        title=ctl.get("title_text") or ctl.get("licensed_video_title") or "",
        desc=(description or "(설명란 없음)")[:1500])
    contents = [prompt]
    if video_path:
        try:
            from google import genai
            client = genai.Client(api_key=key)
            up = client.files.upload(file=str(video_path))
            import time
            for _ in range(60):
                if getattr(up.state, "name", str(up.state)) == "ACTIVE":
                    break
                time.sleep(1)
                up = client.files.get(name=up.name)
            contents = [up, prompt]
        except Exception:
            contents = [prompt]           # 영상 실패 → 텍스트만
    data = _gemini_json(contents, key, model)
    if data is None:
        return None
    fmt, narr = data.get("format_axis"), data.get("narrative_axis")
    ok = fmt in FORMAT_AXES and narr in NARRATIVE_AXES
    return {
        "has_source_video": bool(data.get("has_source_video")),
        "source_title": data.get("source_title"),
        "format_axis": fmt, "narrative_axis": narr,
        "tone_tags": data.get("tone_tags") or [],
        "cluster_id": make_cluster_id(fmt, narr),
        "confidence": data.get("confidence"),
        "classify_status": "confirmed" if ok else "needs_review",
        "classify_model": model,
    }


# ═══════════════════════════════════════════════════════════════════
# LLM 분류 (Gemini) — 메타만으로 포맷·서사·톤 판정 (영상 없이, 원작당 1회)
# ═══════════════════════════════════════════════════════════════════
CLASSIFY_PROMPT = """너는 한국 영상 콘텐츠를 '포맷 × 서사구조'로 분류하는 분류기다.
아래 원작(라이선스 영상) 메타를 보고 JSON 하나로 분류하라.

[축1 포맷] 다음 중 하나: 드라마 / 숏드라마 / 영화 / 예능 / 애니메이션 / 논픽션
  - 드라마: 여러 회차 TV/웹 극. 숏드라마: 세로·초단편 극(회당 1~3분). 영화: 극장·단편 한 덩어리.
  - 예능: 버라이어티/토크/리얼리티. 애니메이션: 애니. 논픽션: 다큐·교양·정보.
[축2 서사구조] 다음 중 하나: 연속서사 / 에피소드완결 / 단편완결 / 비서사
  - 연속서사: 회차가 맥락 누적→클리프행어(연속극). 에피소드완결: 회차 독립·자기완결(시트콤/예능 회차).
  - 단편완결: 영화처럼 한 덩어리로 완결. 비서사: 줄거리 없음(정보·음악·관찰형).
[축3 정서톤] 멀티라벨 배열. 예: ["로맨스","코미디"] / ["스릴러","미스터리"] / ["휴먼","감동"]. 장르 라벨만.

원작 메타:
  제목: {title}
  laeebly video_type: {video_type}
  laeebly genre: {genre}
  국가: {nation} / 연도: {year}

규칙: 위 enum 값만 사용(축1·축2는 정확히 하나). 확신 없으면 가장 가까운 값.
출력 JSON: {{"format_axis": "...", "narrative_axis": "...", "tone_tags": ["..."], "confidence": 0~1, "reason": "1문장"}}
설명·마크다운·코드펜스 금지."""


def classify_llm(meta: dict, key: str, model: str) -> dict | None:
    """Gemini 메타 분류. 실패 시 None."""
    if not key:
        return None
    try:
        from google import genai
    except Exception:
        return None
    prompt = CLASSIFY_PROMPT.format(
        title=meta.get("title") or "", video_type=meta.get("source_video_type") or "",
        genre=meta.get("source_genre") or "", nation=meta.get("nation") or "",
        year=meta.get("published_year") or "")
    try:
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=model, contents=[prompt],
            config={"response_mime_type": "application/json", "temperature": 0.0})
        txt = (resp.text or "").strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            txt = txt[4:].strip() if txt.lower().startswith("json") else txt
        data = json.loads(txt)
    except Exception:
        return None
    fmt = data.get("format_axis")
    narr = data.get("narrative_axis")
    if fmt not in FORMAT_AXES or narr not in NARRATIVE_AXES:
        # enum 이탈 → needs_review로 넘기되 가능한 값은 보존
        return {"format_axis": fmt, "narrative_axis": narr,
                "tone_tags": data.get("tone_tags") or [],
                "cluster_id": make_cluster_id(fmt, narr),
                "confidence": data.get("confidence"),
                "classify_status": "needs_review", "classify_model": model}
    return {"format_axis": fmt, "narrative_axis": narr,
            "tone_tags": data.get("tone_tags") or [],
            "cluster_id": make_cluster_id(fmt, narr),
            "confidence": data.get("confidence"),
            "classify_status": "confirmed", "classify_model": model}


# ═══════════════════════════════════════════════════════════════════
# 레지스트리 캐시 (조회 → 없으면 분류 → insert)
# ═══════════════════════════════════════════════════════════════════
class IPRegistry:
    """eb_ip(원작 IP) in-memory 캐시 + resolve-or-classify.
       라이선스(identification_code 有)=laeebly 메타로 분류.
       비라이선스(코드 無)=description(1순위)+영상으로 원작 식별→ip_key='t:'+정규화제목.
       둘 다 eb_ip에 쌓이고 캐시로 중복 LLM 방지. 자체제작은 공유 IP 없이 쇼츠 클러스터만."""

    COLS = ("ip_key,identification_code,title,cluster_id,tone_tags,format_axis,"
            "narrative_axis,is_laeebly_licensed,classify_status")

    def __init__(self, pipe, key: str, model: str):
        self.pipe, self.key, self.model = pipe, key, model
        self.cache = {}   # ip_key -> row
        for r in pipe.select("eb_ip", {"select": self.COLS}):
            self.cache[r["ip_key"]] = r
        self.n_classified = 0        # 라이선스 신규 분류
        self.n_unlicensed = 0        # 비라이선스 신규 식별

    def resolve(self, ctl: dict, description=None, video_path=None) -> tuple:
        """(cluster_id, tone_tags, is_laeebly_licensed, has_source_video) 반환.
           라이선스=ctl만으로(항상 원본 클립), 비라이선스=description(1순위)+영상으로 식별.
           자체제작(원본 영상물 아님)은 cluster=None, has_source_video=False."""
        code = ctl.get("identification_code")
        if code:
            return self._resolve_licensed(code, ctl)
        return self._resolve_unlicensed(ctl, description, video_path)

    # ── 라이선스: identification_code로 조회 → 없으면 laeebly 메타 분류. 항상 원본 클립 ──
    def _resolve_licensed(self, code, ctl):
        hit = self.cache.get(code)
        if hit:
            return hit.get("cluster_id"), hit.get("tone_tags"), True, True
        meta = {
            "ip_key": code, "identification_code": code, "is_laeebly_licensed": True,
            "title": ctl.get("canonical_title") or ctl.get("licensed_video_title"),
            "source_video_type": ctl.get("video_type"),
            "source_genre": ctl.get("genre"),
            "nation": ctl.get("nation"), "published_year": ctl.get("published_year"),
        }
        cls = classify_llm(meta, self.key, self.model)
        if cls is None:
            fmt = VIDEO_TYPE_HINT.get(meta["source_video_type"])
            cls = {"format_axis": fmt, "narrative_axis": None, "tone_tags": [],
                   "cluster_id": None, "confidence": None,
                   "classify_status": "needs_review", "classify_model": None}
        row = {**meta, **cls, "classify_source": "llm_auto"}
        self._register(code, row)
        self.n_classified += 1
        return row.get("cluster_id"), row.get("tone_tags"), True, True

    # ── 비라이선스: description으로 먼저(B) → 부실/불확실하면 영상까지 식별 ──
    def _resolve_unlicensed(self, ctl, description, video_path):
        if not self.key:
            return None, None, False, None
        cls = None
        # B) 설명란 텍스트만으로 충분히 확신하면 영상 안 봄 (오판 방지 + 값쌈)
        if description and len(description) > 20:
            t = identify_unlicensed(ctl, description, None, self.key, self.model)
            if t and t.get("confidence") is not None \
                    and float(t["confidence"]) >= IDENTIFY_TEXT_CONF:
                cls = t
        if cls is None:                      # 설명란 부실/불확실 → 영상 포함
            cls = identify_unlicensed(ctl, description, video_path, self.key, self.model)
        if cls is None:
            return None, None, False, None
        return self._finalize_unlicensed(cls)

    def _finalize_unlicensed(self, cls):
        """식별 결과 → (cluster, tone, is_licensed=False, has_source_video).
           원본 영상물 아니면(자체제작) cluster/IP 없음."""
        if not cls.get("has_source_video") or not cls.get("source_title"):
            return None, None, False, False          # 자체제작 → 클러스터 미부여
        ip_key = ip_key_for(None, cls["source_title"])
        if not ip_key:
            return None, None, False, True
        hit = self.cache.get(ip_key)                 # 같은 원작 이미 등록됐으면 상속
        if hit:
            return hit.get("cluster_id"), hit.get("tone_tags"), False, True
        row = {"ip_key": ip_key, "identification_code": None, "is_laeebly_licensed": False,
               "title": cls["source_title"], "classify_source": "llm_auto",
               **{k: cls.get(k) for k in ("format_axis", "narrative_axis", "cluster_id",
                                          "tone_tags", "confidence", "classify_status",
                                          "classify_model")}}
        self._register(ip_key, row)
        self.n_unlicensed += 1
        return row.get("cluster_id"), row.get("tone_tags"), False, True

    def _register(self, ip_key, row):
        try:
            self.pipe.upsert("eb_ip", [row], on_conflict="ip_key")
        except Exception:
            pass
        self.cache[ip_key] = row


# ═══════════════════════════════════════════════════════════════════
# xlsx seed — 손으로 만든 분류를 identification_code별로 선적재
# ═══════════════════════════════════════════════════════════════════
def _tone_list(cell) -> list:
    if not cell:
        return []
    return [t.strip() for t in re.split(r"[,/·]", str(cell)) if t.strip()]


def seed_from_xlsx(pipe, laeebly) -> dict:
    """xlsx(제목 기준 분류) × laeebly licensed_video(코드·제목) 조인 →
       eb_ip에 'xlsx_seed' 행 upsert (전부 laeebly 라이선스 원작). 리포트 dict 반환."""
    try:
        import openpyxl
    except ImportError:
        raise SystemExit("openpyxl 필요: pip install openpyxl")
    if not XLSX_PATH.exists():
        raise SystemExit(f"분류 xlsx 없음: {XLSX_PATH}")

    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True)
    ws = wb["예시뱅크_분류"]
    # 제목 정규화 → (format, narrative, tone)
    xlsx_by_title = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[0]:
            continue
        xlsx_by_title[_norm_title(r[0])] = {
            "format_axis": r[1], "narrative_axis": r[2], "tone_tags": _tone_list(r[3])}

    # laeebly 원작 전체(코드+제목+메타)
    lvs = laeebly.q(
        "select identification_code, btrim(title) as title, video_type, genre, "
        "nation, published_year from licensed_video "
        "where nullif(btrim(title),'') is not null")

    rows, matched, unmatched = [], 0, []
    for lv in lvs:
        cls = xlsx_by_title.get(_norm_title(lv["title"]))
        if not cls:
            unmatched.append(lv["title"])
            continue
        matched += 1
        rows.append({
            "ip_key": lv["identification_code"],           # 라이선스: ip_key = code
            "identification_code": lv["identification_code"],
            "is_laeebly_licensed": True,
            "title": lv["title"],
            "format_axis": cls["format_axis"],
            "narrative_axis": cls["narrative_axis"],
            "cluster_id": make_cluster_id(cls["format_axis"], cls["narrative_axis"]),
            "tone_tags": cls["tone_tags"],
            "source_video_type": lv["video_type"], "source_genre": lv["genre"],
            "nation": lv["nation"], "published_year": lv["published_year"],
            "classify_source": "xlsx_seed", "classify_status": "confirmed",
        })
    if rows:
        pipe.upsert("eb_ip", rows, on_conflict="ip_key")
    return {"laeebly_works": len(lvs), "seeded": len(rows),
            "matched_titles": matched, "unmatched_sample": unmatched[:15],
            "n_unmatched": len(unmatched)}
