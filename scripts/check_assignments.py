#!/usr/bin/env python3
"""루프 운영 정본 검증 — 배정·작품 카드·정책이 서로 맞는지 생성 전에 확인한다.

왜 필요한가: scene_loop 는 머신마다 따로 돌고 상태도 머신별이라 서로를 볼 수 없다. 그래서
잘못된 배정(한 채널을 두 머신이 담당)이나 잘못된 카드(플레이리스트 한정 작품에 채널 URL)는
**실행 시점에는 정상처럼 보이고** 결과물이 나온 뒤에야 드러난다. 이 스크립트가 그 앞을 막는다.

두 모드:
  기본        파일만으로 되는 검사(DB 불필요) — 러너·스킬이 생성 전에 이걸 돌린다
  --laeebly   권리 DB 대조(작품명 완전일치·지오블락 요구) — 새 작품·새 배정을 붙일 때 사람이 돌린다

★ 게이트 술어는 publish_youtube 에서 import 해 쓴다. 재구현하면 배정 게이트와 발행 게이트의
  판정이 갈라져, 배정에서 통과한 것이 발행에서 막히거나(비용 낭비) 그 반대(사고)가 된다.

실행: python scripts/check_assignments.py [--laeebly] [--strict]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import channel_registry as reg  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTICE_PATH = REPO_ROOT / "config" / "work_publish_notice.json"

BLOCK, WARN, INFO = "⛔", "⚠️", "※"

SOURCE_TYPES = ("youtube_playlist", "youtube_channel", "local")
CARD_KEYS = {"source", "constraints", "rights_lookup", "identification_code", "_guide", "_note"}
SOURCE_KEYS = {"type", "url", "dir_slug", "file_glob", "episode_regex",
               "start_episode", "min_source_duration_sec"}
CONSTRAINT_KEYS = {"geoblock_required", "subtitles"}
SUBTITLE_VALUES = ("provided", "none")


# ─────────────────────────── 순수 판정 (단위테스트) ───────────────────────────

def unknown_keys(d, allowed):
    """설정 dict 에서 허용되지 않은 키. 오타를 잡기 위한 것 — 예: min_source_duration_secs 로
    쓰면 하한이 통째로 사라져 45초 예고편이 소스가 된다(index_episodes 는 >0 일 때만 길이를 본다)."""
    return sorted(k for k in d if k not in allowed)


def url_matches_type(kind, url):
    """소스 범위(type)와 URL 모양이 맞는가. 순수.

    🛑 어긋나면 권리 범위를 벗어난다 — '해당 플레이리스트 영상만 사용 가능'인 작품에 채널 URL 을
    넣으면 채널 전체가 소스가 된다."""
    u = url or ""
    if kind == "youtube_playlist":
        return "playlist?list=" in u or "/playlist" in u
    if kind == "youtube_channel":
        return ("/videos" in u) and ("playlist?list=" not in u)
    return True


def regex_problem(pattern):
    """정규식이 쓸 수 있는가 → 문제 사유 문자열 또는 None. 순수."""
    if not pattern:
        return "정규식이 비어 있음(기본값 없음 — 작품마다 회차 표기가 달라 추측하지 않는다)"
    try:
        c = re.compile(pattern)
    except re.error as e:
        return f"컴파일 실패: {e}"
    if c.groups < 1:
        return "캡처그룹이 없음(그룹1이 회차번호여야 한다)"
    return None


def has_work_anchor(pattern):
    """채널 전체 소스에서 작품을 한정하는 앵커가 정규식에 있는가. 순수.

    채널 업로드 목록에는 여러 작품이 섞여 있어 'EP.3' 만으로는 다른 작품 3화를 집는다.
    해시태그·작품명 같은 한글/영문 리터럴이 들어 있어야 한정이 된다."""
    body = re.sub(r"\\[a-zA-Z]|\[[^\]]*\]|\{[^}]*\}|[()\\.*+?^$|]", "", pattern or "")
    return bool(re.search(r"[가-힣]{2,}|[A-Za-z]{4,}", body.replace("EP", "")))


def is_nfc(s):
    return unicodedata.normalize("NFC", s or "") == (s or "")


def duration_smoke(entries, pattern, min_sec, start_ep=1):
    """캐시된 인덱스로 '이 정규식·하한이면 무엇이 남는가' → (남은 회차수, 구멍 회차 목록). 순수.

    도깨비 EP3 사고 패턴(회차가 통째로 사라졌는데 에러가 아니라 '소스 없음'으로 보임)을 밤 실행
    전에 드러내려는 것이다.

    ★'구멍'만 센다 = 살아남은 마지막 회차보다 **앞인데** 탈락한 회차. 뒤쪽 탈락은 아직 방영 전이라
    예고편만 올라온 정상 상태다(스트릿 EP7 실측 — 64초 예고 1건뿐). 매번 뜨는 경고는 사람이
    무시하게 되므로 의미 있는 것만 남긴다."""
    pat = re.compile(pattern)
    matched, kept = set(), set()
    for e in entries or []:
        titles = [e.get("title")] + list(e.get("alt_titles") or [])
        m = next((pat.search(t or "") for t in titles if t and pat.search(t or "")), None)
        if not m:
            continue
        n = int(m.group(1))
        if n < start_ep:
            continue
        matched.add(n)
        d = e.get("duration")
        if d is not None and float(d) >= (min_sec or 0):
            kept.add(n)
    holes = sorted(n for n in (matched - kept) if kept and n < max(kept))
    return len(kept), holes


# ─────────────────────────── 리포트 ───────────────────────────

class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, msg):
        self.rows.append((level, msg))

    def block(self, msg):
        self.add(BLOCK, msg)

    def warn(self, msg):
        self.add(WARN, msg)

    def info(self, msg):
        self.add(INFO, msg)

    def counts(self):
        return (sum(1 for lv, _ in self.rows if lv == BLOCK),
                sum(1 for lv, _ in self.rows if lv == WARN))

    def print(self):
        for lv, msg in self.rows:
            print(f"  {lv} {msg}")
        b, w = self.counts()
        print(f"\n{BLOCK} {b}건 · {WARN} {w}건")


# ─────────────────────────── 오프라인 검사 ───────────────────────────

def check_offline(rep, *, records, works, assignments, notice, index_dir=None, sources_root=None,
                  scope_machine=None):
    """배정 정본 검증. scope_machine 을 주면 **카드 상세 검사는 그 머신 담당분만** 본다.

    범위를 나누는 이유(2026-07-28 실측): 전 머신을 깊게 보면 '아직 이관하지 않은 머신의 카드가
    없다' 는 ⛔ 가 이 머신의 러너 게이트를 막는다. 남의 사정으로 내 생성이 멈추면 안 된다.
    구조 검사(중복 배정·alias 충돌·채널 존재)는 본질적으로 전역이라 범위와 무관하게 항상 본다."""
    machines = (assignments or {}).get("machines") or {}
    if not machines:
        rep.block("config/assignments.json 에 machines 가 없습니다 — 배정 정본이 비어 있습니다")
        return

    # 2. 머신 id · alias 다중 매칭
    seen_alias = {}
    for mid, rec in machines.items():
        for kind in ("hostname", "user"):
            for a in ((rec.get("aliases") or {}).get(kind) or []):
                key = (kind, a.lower())
                if key in seen_alias:
                    rep.block(f"alias {kind}={a!r} 가 머신 '{seen_alias[key]}' 와 '{mid}' 둘에 있습니다 "
                              f"— 자동 감지가 어느 쪽인지 결정할 수 없습니다")
                seen_alias[key] = mid

    # 3. 채널 중복 배정
    owner = {}
    for mid, rec in machines.items():
        for ch in (rec.get("channels") or []):
            if ch in owner:
                rep.block(f"채널 '{ch}' 가 머신 '{owner[ch]}' 와 '{mid}' 두 곳에 배정됐습니다 — "
                          f"두 대가 같은 채널에 생성·발행하면 거의 같은 쇼츠가 두 개 올라갑니다")
            owner[ch] = mid

    ch_names = set(reg.channel_names(records))
    assigned_works = set()

    for mid, rec in machines.items():
        for ch in (rec.get("channels") or []):
            # 4. 배정 채널이 channels.json 에 있는가
            if ch not in ch_names:
                cands = [n for n in ch_names if reg.norm_work_title(n, fold=True) ==
                         reg.norm_work_title(ch, fold=True)]
                rep.block(f"채널 '{ch}'({mid}) 가 config/channels.json 에 없습니다"
                          + (f" — 후보: {cands}" if cands else ""))
                continue
            wks = reg.works_of(ch, records)
            if not wks:
                rep.warn(f"채널 '{ch}'({mid}) 에 배정된 작품이 없습니다 — 그 채널은 아무것도 만들지 않습니다")
            in_scope = scope_machine is None or mid == scope_machine
            for work in wks:
                assigned_works.add(work)
                # 5. 카드 존재
                card = reg.work_card(work, works)
                if card is None:
                    if not in_scope:
                        rep.info(f"작품 '{work}'(채널 {ch} · {mid}) 카드 없음 — 그 머신은 아직 "
                                 f"예전 방식으로 돈다(이관 시 추가 필요)")
                        continue
                    cands = reg.work_card_candidates(work, works)
                    rep.block(f"작품 '{work}'(채널 {ch}) 카드가 config/works.json 에 없습니다"
                              + (f" — 후보: {cands}" if cands else ""))
                    continue
                _check_card(rep, work, ch, card, index_dir=index_dir, sources_root=sources_root)

    # 11. notice 키 드리프트 — 카드가 아예 없는 작품의 표기 설정은 정상이다(그 작품은 아직
    #     카드가 없을 뿐). 문제는 '카드와 거의 같은데 글자가 다른' 경우다 — publish_youtube 는
    #     정확 dict 조회라 그런 키는 조용히 무시돼 표기가 빠진다.
    for k in (notice or {}):
        if k in works:
            continue
        near = reg.work_card_candidates(k, works)
        if near:
            rep.block(f"config/work_publish_notice.json 의 '{k}' 가 작품 카드 {near} 와 글자가 "
                      f"다릅니다 — publish_youtube 는 정확 dict 조회라 표기가 조용히 누락됩니다")

    # 12. 미배정 채널 / 죽은 카드
    idle = sorted(ch_names - set(owner))
    if idle:
        rep.info(f"어떤 머신에도 배정되지 않은 채널 {len(idle)}개: {', '.join(idle)} "
                 f"(다른 머신이 아직 배정 정본으로 안 옮겼으면 정상)")
    for w in sorted(set(works) - assigned_works):
        rep.warn(f"작품 카드 '{w}' 를 쓰는 배정 채널이 없습니다(죽은 카드)")


def _check_card(rep, work, channel, card, *, index_dir=None, sources_root=None):
    where = f"'{work}'(채널 {channel})"

    # 13. NFC
    if not is_nfc(work):
        rep.block(f"{where}: 작품 카드 키가 NFC 가 아닙니다 — 눈으로 같아도 dict 조회와 "
                  f"SQL 완전일치가 실패합니다")

    # 1. 미지 키
    for bad in unknown_keys(card, CARD_KEYS):
        rep.block(f"{where}: 알 수 없는 카드 키 '{bad}' — 오타면 그 설정이 통째로 무시됩니다")
    src = card.get("source") or {}
    for bad in unknown_keys(src, SOURCE_KEYS):
        rep.block(f"{where}: 알 수 없는 source 키 '{bad}' — 오타면 그 값이 기본값으로 조용히 떨어집니다")
    con = card.get("constraints") or {}
    for bad in unknown_keys(con, CONSTRAINT_KEYS):
        rep.block(f"{where}: 알 수 없는 constraints 키 '{bad}'")

    # 6. 카드 정합
    if not card.get("_guide"):
        rep.block(f"{where}: _guide 가 없습니다 — 권리사 가이드를 읽고 원문을 인용하세요"
                  f"(코드가 권리 범위를 추측하지 않는다는 규칙의 기계화)")
    kind = src.get("type")
    if kind not in SOURCE_TYPES:
        rep.block(f"{where}: source.type={kind!r} 은 알 수 없습니다 {SOURCE_TYPES}")
        return
    if kind.startswith("youtube"):
        if not url_matches_type(kind, src.get("url")):
            rep.block(f"{where}: type={kind} 인데 url 모양이 맞지 않습니다 "
                      f"({src.get('url')!r}) — 권리 범위를 벗어날 수 있습니다")
        if not (src.get("min_source_duration_sec") or 0) > 0:
            rep.block(f"{where}: youtube 소스인데 min_source_duration_sec 가 없습니다 — "
                      f"하한이 없으면 45초 예고편이 소스로 뽑힙니다")
    else:
        for k in ("dir_slug", "file_glob"):
            if not src.get(k):
                rep.block(f"{where}: local 소스인데 source.{k} 가 없습니다")

    prob = regex_problem(src.get("episode_regex"))
    if prob:
        rep.block(f"{where}: episode_regex — {prob}")
    elif kind == "youtube_channel" and not has_work_anchor(src.get("episode_regex")):
        rep.block(f"{where}: 채널 전체가 소스인데 정규식에 작품 한정 앵커가 없습니다 — "
                  f"같은 채널의 다른 작품 회차를 집습니다(해시태그로 한정하세요)")

    # subtitles 필수
    if con.get("subtitles") not in SUBTITLE_VALUES:
        rep.block(f"{where}: constraints.subtitles 가 {SUBTITLE_VALUES} 중 하나여야 합니다 "
                  f"(현재 {con.get('subtitles')!r}) — 빠뜨리면 오자막이 조용히 박힙니다")

    # 9. 캐시 인덱스 스모크
    if kind.startswith("youtube") and index_dir:
        entries = _load_cached_index(index_dir, src.get("url"))
        if entries is None:
            rep.info(f"{where}: 인덱스 캐시가 없어 정규식 스모크를 건너뜁니다")
        else:
            n_keep, holes = duration_smoke(entries, src["episode_regex"],
                                           src.get("min_source_duration_sec"),
                                           src.get("start_episode", 1))
            if n_keep == 0:
                rep.warn(f"{where}: 캐시 {len(entries)}건에서 쓸 수 있는 회차 0개 — "
                         f"회차가 조용히 사라지는 상태입니다(정규식·하한 확인)")
            elif holes:
                rep.warn(f"{where}: 중간 회차 {holes} 가 하한 "
                         f"{src.get('min_source_duration_sec')}초에서 탈락합니다 — 앞뒤 회차는 "
                         f"살아 있는데 가운데가 비었습니다(도깨비 EP3 사고 패턴)")
            else:
                rep.info(f"{where}: 사용 가능 회차 {n_keep}개")

    # 10. 로컬 소스 폴더
    if kind == "local" and sources_root:
        d = pathlib.Path(sources_root) / (src.get("dir_slug") or "")
        if not d.exists():
            rep.warn(f"{where}: 소스 폴더가 없습니다 {d} — 그 채널은 '회차 없음'으로 대기합니다")
        elif not list(d.glob(src.get("file_glob") or "*")):
            rep.warn(f"{where}: 소스 폴더에 {src.get('file_glob')} 가 없습니다 {d}")


def _load_cached_index(index_dir, url):
    import hashlib
    p = pathlib.Path(index_dir) / f"{hashlib.sha1((url or '').encode('utf-8')).hexdigest()[:12]}.json"
    if not p.exists():
        return None
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("entries") or []
    except (OSError, json.JSONDecodeError):
        return None


# ─────────────────────────── laeebly 대조 ───────────────────────────

def check_laeebly(rep, *, works, assigned_only=None):
    try:
        import psycopg
        import publish_youtube as pub
    except ImportError as e:
        rep.warn(f"laeebly 대조를 건너뜁니다(import 실패: {e})")
        return
    url = os.environ.get("LAEEBLY_DB_URL")
    if not url:
        rep.warn("LAEEBLY_DB_URL 미설정 — 권리 DB 대조를 건너뜁니다")
        return
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select title, guide from licensed_video")
        rows = cur.fetchall()
    by_title = {t: g for t, g in rows}
    norm = {reg.norm_work_title(t, fold=True): t for t in by_title}

    for work, card in (works or {}).items():
        if assigned_only and work not in assigned_only:
            continue
        declared_none = (card.get("rights_lookup") == "none")
        row = by_title.get(work) if work in by_title else by_title.get(work + " (g)")
        if row is None and work not in by_title:
            cand = norm.get(reg.norm_work_title(work, fold=True))
            msg = (f"'{work}' 가 laeebly 에 완전일치로 없습니다"
                   + (f" — 후보: '{cand}'" if cand else "")
                   + " · 한 글자만 달라도 식별코드·가이드·지오블락 조회가 통째로 실패하고 "
                     "경고만 뜬 채 발행됩니다")
            (rep.warn if declared_none else rep.block)(
                msg + (" (rights_lookup=none 으로 선언됨 — 권리 근거를 _note 로 관리 중)"
                       if declared_none else ""))
            continue
        guide = by_title.get(work) or by_title.get(work + " (g)") or ""
        needs = pub.guide_requires_geoblock(guide)
        declared = bool((card.get("constraints") or {}).get("geoblock_required"))
        if needs and not declared:
            rep.block(f"'{work}': laeebly 가이드는 지오블락을 요구하는데 카드가 "
                      f"geoblock_required=false 입니다 — 배정 게이트가 뚫립니다")
        elif declared and not needs:
            rep.warn(f"'{work}': 카드는 지오블락 필요인데 가이드에서 감지되지 않습니다"
                     f"(안전측이라 차단하지 않음)")
        if pub.guide_requires_notice(guide):
            notice = pub.load_notice_config()
            if work not in notice:
                rep.warn(f"'{work}': 가이드가 설명란 표기를 요구하는 것으로 보이는데 "
                         f"config/work_publish_notice.json 에 설정이 없습니다")


# ─────────────────────────── main ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="루프 운영 정본 검증(배정·작품 카드·정책)")
    ap.add_argument("--laeebly", action="store_true", help="권리 DB 대조까지 수행(LAEEBLY_DB_URL 필요)")
    ap.add_argument("--strict", action="store_true", help="⚠️ 도 실패로 취급(러너·CI용)")
    ap.add_argument("--all", action="store_true",
                    help="전 머신의 작품 카드까지 깊게 본다(기본은 이 머신 담당분만 — 남의 미이관 상태가 "
                         "이 머신의 러너 게이트를 막지 않도록)")
    ap.add_argument("--machine", help="검사 범위로 삼을 머신 id(기본: 자동 감지)")
    a = ap.parse_args()

    records = reg.load_channels()
    works = reg.load_works()
    assignments = reg.load_assignments()
    local = reg.load_machine_local()
    notice = {}
    try:
        notice = (json.loads(NOTICE_PATH.read_text(encoding="utf-8")) or {}).get("works") or {}
    except (OSError, json.JSONDecodeError):
        pass

    scope = None
    if not a.all:
        try:
            scope = a.machine or reg.detect_machine_id(assignments, explicit=a.machine,
                                                       env=os.environ.get("SCENE_LOOP_MACHINE"),
                                                       local=local.get("machine"))
        except LookupError:
            scope = None          # 이 머신이 아직 배정 정본에 없으면 전역으로 본다

    rep = Report()
    check_offline(rep, records=records, works=works, assignments=assignments, notice=notice,
                  index_dir=REPO_ROOT / "results" / "youtube_index",
                  sources_root=local.get("sources_root") or reg.default_sources_root(),
                  scope_machine=scope)
    if a.laeebly:
        assigned = set()
        for mid, rec in (assignments.get("machines") or {}).items():
            if scope and mid != scope:
                continue
            for ch in (rec.get("channels") or []):
                assigned.update(reg.works_of(ch, records))
        check_laeebly(rep, works=works, assigned_only=assigned or None)

    print("=== 루프 운영 정본 검증 ==="
          + (f" (범위: {scope})" if scope else " (전 머신)"))
    if not rep.rows:
        print("  이상 없음")
        print(f"\n{BLOCK} 0건 · {WARN} 0건")
        return 0
    rep.print()
    blocks, warns = rep.counts()
    return 1 if (blocks or (a.strict and warns)) else 0


if __name__ == "__main__":
    sys.exit(main())
