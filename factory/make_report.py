#!/usr/bin/env python
"""테스트 실행 리포트 생성 — eb_shorts_features 데이터 → 자족 HTML.

내용: 실행 요약 · 파이프라인 스테이지 · 쇼츠별 카드(피처·VLM버킷·성과·점수)
      + 작품정보 루프 시나리오 2종(가상 예능 / 가상 드라마 → 참조 인출 → 생성 프롬프트).
사용: python make_report.py   → report.html
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import HERE, load_settings
from db import Pipeline

RUN_ID = "3367af26-0603-4b73-95a9-2052335bd4a4"   # 이번 20개 VLM 실행

BUCKETS = ["hook_0_3s", "build", "payoff", "subtitle_style", "tts_narration",
           "audio_design", "point_elements", "cover_title", "content_context",
           "scene_observation"]
DET_FEATS = ["duration_sec", "cut_count", "avg_shot_len_sec", "cut_rhythm_var",
             "hook_timing_sec", "silence_ratio", "speech_ratio", "loudness_dynamics",
             "video_fill_ratio", "subtitle_density"]
PERF_FEATS = ["views", "valid_views", "watch_time_hours", "impressions", "ctr_pct",
              "kept_watching_rate", "likes", "shares", "comments_added", "n_snapshots"]


def fetch():
    cfg = load_settings()
    pipe = Pipeline(cfg["PIPELINE_URL"], cfg["PIPELINE_SERVICE_KEY"])
    cols = ("shorts_id,video_url,title_text,onscreen_title_text,channel_name,channel_id,"
            "lifecycle_status,publish_time,storage_path,ip:identification_code,"
            "ip_key,origin,"
            "is_laeebly_licensed,has_source_video,cluster_id,genre,video_type,nation,"
            "published_year,description_text,"
            "cast_text,subscriber_count,channel_total_views,hashtags,vlm_model,"
            "overall_confidence,extracted_at,"
            + ",".join(DET_FEATS) + "," + ",".join(PERF_FEATS)
            + ",channel_baseline_views,baseline_cascade,lift_views,performance_score,"
            "perf_label,score_basis,score_version,scored_at,ingested_at,"
            + ",".join(BUCKETS))
    shorts = pipe.select("eb_shorts_features", {"select": cols})
    run = pipe.select("eb_factory_runs", {"select": "*", "run_id": f"eq.{RUN_ID}"})
    items = pipe.select("eb_factory_items",
                        {"select": "shorts_id,stage,status,message,error,finished_at",
                         "run_id": f"eq.{RUN_ID}"})
    return shorts, (run[0] if run else {}), items


# ─────────────────────────────────────────────────────────────────────
# 작품정보 루프 시나리오 — 가상 작품 → 클러스터 참조 인출 → 생성 프롬프트
# ─────────────────────────────────────────────────────────────────────
def salience_max(s):
    vals = []
    for b in BUCKETS:
        v = s.get(b)
        if isinstance(v, dict) and v.get("salience") is not None:
            try:
                vals.append(float(v["salience"]))
            except (TypeError, ValueError):
                pass
    return max(vals) if vals else 0.0


def _has_vlm(s):
    return s.get("vlm_model") is not None


def retrieve(shorts, cluster, k_good=3, holdout=None):
    """클러스터 인출 — good 카드 / 대조쌍 / 탐색 슬롯 + 폴백 여부.
       참조 카드·대조쌍은 VLM 피처가 있는 쇼츠 우선(내용 풍부하도록).
       ★에코챔버 차단(§3-2): origin='ours'(자사 발행분)는 인출 모집단·글로벌 폴백·
       탐색 슬롯 전부에서 하드 제외 — 자기 산출물 재주입 금지.
       holdout: 주입 금지 클러스터 집합(골든 홀드아웃, 기본 config.INJECTION_HOLDOUT_CLUSTERS)."""
    if holdout is None:
        from config import INJECTION_HOLDOUT_CLUSTERS
        holdout = INJECTION_HOLDOUT_CLUSTERS
    if cluster in holdout:
        return {"n_in_cluster": 0, "goods": [], "bads": [], "pair": None,
                "explore": None, "fallback": [], "holdout": True}
    market = [s for s in shorts if s.get("origin") != "ours"]   # 자사 하드 제외
    inpop = [s for s in market if s.get("cluster_id") == cluster
             and s.get("performance_score") is not None
             and s.get("lifecycle_status") == "active"]
    # good: VLM 있는 것 우선, 그다음 점수 — 참조 카드가 알차게
    goods = sorted((s for s in inpop if s.get("perf_label") == "good"),
                   key=lambda s: (_has_vlm(s), s["performance_score"]), reverse=True)
    bads = sorted((s for s in inpop if s.get("perf_label") == "bad"),
                  key=lambda s: (_has_vlm(s), -s["performance_score"]), reverse=True)
    # 대조쌍: VLM 있는 good 최상 vs VLM 있는 bad 최하 (피처 비교가 의미 있게)
    g_pair = next((s for s in goods if _has_vlm(s)), goods[0] if goods else None)
    b_pair = next((s for s in bads if _has_vlm(s)), bads[0] if bads else None)
    pair = (g_pair, b_pair) if g_pair and b_pair else None
    explore = max(inpop, key=salience_max) if inpop else None
    fallback = []
    if len(goods) < k_good:
        pool = sorted((s for s in market if s.get("perf_label") == "good"
                       and s.get("lifecycle_status") == "active" and _has_vlm(s)
                       and s not in goods),
                      key=lambda s: s["performance_score"], reverse=True)
        fallback = pool[:k_good - len(goods)]
    return {"n_in_cluster": len(inpop), "goods": goods[:k_good], "bads": bads,
            "pair": pair, "explore": explore, "fallback": fallback, "holdout": False}


def _feat(s, keys):
    return {k: s.get(k) for k in keys}


def build_prompt(work, ret):
    """러프한 생성 프롬프트 텍스트(§3.3 4조각)."""
    L = []
    L.append(f"# 쇼츠 생성 브리프 — 《{work['title']}》")
    L.append(f"[작품정보] 포맷={work['format']} · 서사구조={work['narrative']} "
             f"· 정서톤={', '.join(work['tone'])} · 장르={work['genre']}")
    L.append(f"[설정] {work['logline']}")
    L.append(f"[매칭 클러스터] {work['cluster']}  "
             f"(예시뱅크 내 {ret['n_in_cluster']}개 참조)")
    L.append("")
    L.append("## ① 참조 사례 카드 (few-shot) — 이 클러스터에서 성과 좋았던 편집들")
    cards = ret["goods"] + [dict(s, _src="global") for s in ret["fallback"]]
    if not cards:
        L.append("  (해당 클러스터 good 사례 없음)")
    for s in cards:
        src = " [출처: 전체 폴백]" if s.get("_src") == "global" else ""
        hook = (s.get("hook_0_3s") or {})
        pts = (s.get("point_elements") or {})
        L.append(f"  · 「{(s.get('title_text') or '')[:30]}」 (점수 {s['performance_score']}"
                 f", lift {s.get('lift_views')}){src}")
        L.append(f"      hook: {hook.get('note','')[:70]}")
        L.append(f"      tags: {', '.join((hook.get('tags') or [])[:4])}"
                 f" | 감정: {(pts.get('ai_features') or {}).get('emotion_category','-')}"
                 f" | 컷 {s.get('cut_count')}개/{s.get('duration_sec')}초")
    L.append("")
    L.append("## ② 대조 쌍 (good vs bad) — 같은 클러스터에서 성과가 갈린 두 편")
    if ret["pair"]:
        g, b = ret["pair"]
        L.append(f"  GOOD 「{(g.get('title_text') or '')[:28]}」 점수 {g['performance_score']}"
                 f" / lift {g.get('lift_views')}")
        L.append(f"  BAD  「{(b.get('title_text') or '')[:28]}」 점수 {b['performance_score']}"
                 f" / lift {b.get('lift_views')}")
        # 갈린 축 — 실제로 유의미하게 다른 것만
        axes = []
        gh = ((g.get('hook_0_3s') or {}).get('ai_features') or {}).get('hook_semantic_strength')
        bh = ((b.get('hook_0_3s') or {}).get('ai_features') or {}).get('hook_semantic_strength')
        if gh is not None and bh is not None and abs(float(gh) - float(bh)) >= 0.1:
            axes.append(f"훅 강도({gh} vs {bh})")
        gc, bc = g.get('cut_count'), b.get('cut_count')
        if gc and bc and abs(gc - bc) >= 3:
            axes.append(f"컷수({gc} vs {bc})")
        gd, bd = g.get('duration_sec'), b.get('duration_sec')
        if gd and bd and abs(float(gd) - float(bd)) >= 8:
            axes.append(f"길이({gd} vs {bd}초)")
        gsl, bsl = g.get('silence_ratio'), b.get('silence_ratio')
        if gsl is not None and bsl is not None and abs(float(gsl) - float(bsl)) >= 0.15:
            axes.append(f"침묵비율({gsl} vs {bsl})")
        ge = ((g.get('point_elements') or {}).get('ai_features') or {}).get('emotion_category')
        be = ((b.get('point_elements') or {}).get('ai_features') or {}).get('emotion_category')
        if ge and be and ge != be:
            axes.append(f"감정소구({ge} vs {be})")
        L.append(f"  → 갈린 축: {' · '.join(axes) if axes else '(주로 lift 격차 — 피처는 유사)'}")
        L.append(f"     지시: 'A(good)처럼 {axes[0].split('(')[0] if axes else '훅·리듬'}을 가져가되, 새 소재로'")
    else:
        L.append("  (good/bad 대조쌍 부족 — 클러스터 표본 필요)")
    L.append("")
    L.append("## ③ 탐색 슬롯 — 관련성 낮아도 특이한(salience↑) 사례 1개 (강제 발산)")
    e = ret["explore"]
    if e:
        L.append(f"  「{(e.get('title_text') or '')[:30]}」 salience {salience_max(e):.2f}"
                 f" — 기본 템플릿에서 벗어난 시도 참고(다양성 목적)")
    L.append("")
    L.append("## ④ 채널 dossier — 타깃 채널 L2 아티팩트")
    L.append("  (eb_channels.dossier 미충전 — 정체성·톤·타깃·대표작은 후속 생성 패스)")
    L.append("")
    L.append("## 생성 지시")
    L.append(f"  위 참조를 '정답'이 아닌 '재료'로 삼아, 《{work['title']}》의 새 원본에서")
    L.append(f"  {work['cluster']} 편집 문법(위 good 카드의 훅·컷 리듬·감정 소구)을 변주해")
    L.append("  쇼츠 1편의 편집 계획(훅 0~3초 / 빌드 / 페이오프 / 자막·TTS)을 제안하라.")
    return "\n".join(L)


SCENARIOS = [
    {"title": "오늘 뭐 먹지? 랜덤 밥상", "format": "예능", "narrative": "에피소드완결",
     "tone": ["코믹", "훈훈", "일상"], "genre": "예능/리얼리티",
     "logline": "매 회 랜덤으로 정해진 재료·상황으로 게스트가 즉석 요리 대결을 벌이는 관찰 버라이어티.",
     "cluster": "예능×에피소드완결"},
    {"title": "스물아홉 번째 여름", "format": "드라마", "narrative": "연속서사",
     "tone": ["멜로", "성장", "잔잔한 긴장"], "genre": "로맨스/드라마",
     "logline": "시한부 선고를 받은 스물아홉 여자가 첫사랑을 다시 만나 마지막 여름을 보내는 연속극.",
     "cluster": "드라마×연속서사"},
]


# ─────────────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────────────
def esc(x):
    if x is None:
        return ""
    return (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_html(shorts, run, items, scenarios_out):
    # 스테이지 집계
    stage_order = ["gate", "classify", "download", "storage_upload", "extract_cheap",
                   "extract_vlm", "perf_aggregate", "ingest", "channel_refresh", "score"]
    stage_ct = {st: {"success": 0, "failed": 0, "skipped": 0} for st in stage_order}
    for it in items:
        d = stage_ct.setdefault(it["stage"], {"success": 0, "failed": 0, "skipped": 0})
        d[it["status"]] = d.get(it["status"], 0) + 1
    # 라벨/클러스터 분포
    active = [s for s in shorts if s.get("lifecycle_status") == "active"]
    data_json = json.dumps(shorts, ensure_ascii=False, default=str)
    run_json = json.dumps(run, ensure_ascii=False, default=str)
    stage_json = json.dumps({"order": stage_order, "ct": stage_ct}, ensure_ascii=False)
    scen_json = json.dumps(scenarios_out, ensure_ascii=False)
    tmpl = REPORT_HTML
    for k, v in {"__DATA__": data_json, "__RUN__": run_json, "__STAGES__": stage_json,
                 "__SCEN__": scen_json, "__NSHORTS__": str(len(shorts)),
                 "__NACTIVE__": str(len(active)),
                 "__GENAT__": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}.items():
        tmpl = tmpl.replace(k, v)
    return tmpl


REPORT_HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>쇼츠 분석 공장 — 테스트 리포트</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--card2:#1e222b;--bd:#2a2f3a;--fg:#e6e9ef;--mut:#9aa4b2;
 --acc:#5b9dff;--good:#3fb56b;--mid:#e0a53b;--bad:#e05b5b;--chip:#232834;}
@media(prefers-color-scheme:light){:root{--bg:#f6f7f9;--card:#fff;--card2:#f0f2f5;--bd:#e2e6ec;
 --fg:#1a1d24;--mut:#5b6472;--acc:#2f6fe0;--chip:#eef1f5;}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Malgun Gothic",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:34px 0 12px;border-bottom:1px solid var(--bd);padding-bottom:6px}
.mut{color:var(--mut)}.row{display:flex;flex-wrap:wrap;gap:10px}
.stat{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px 16px;min-width:96px}
.stat .v{font-size:22px;font-weight:700}.stat .l{font-size:12px;color:var(--mut)}
.stages{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.stg{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px 12px}
.stg .nm{font-size:12px;color:var(--mut)}.stg .n{font-size:18px;font-weight:700}
.ok{color:var(--good)}.fail{color:var(--bad)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.chip{background:var(--chip);border:1px solid var(--bd);border-radius:999px;padding:2px 9px;font-size:12px;color:var(--mut)}
.lbl{font-weight:700;padding:1px 8px;border-radius:6px;font-size:12px}
.lbl.good{background:rgba(63,181,107,.16);color:var(--good)}
.lbl.mid{background:rgba(224,165,59,.16);color:var(--mid)}
.lbl.bad{background:rgba(224,91,91,.16);color:var(--bad)}
.bar{height:8px;border-radius:6px;background:var(--card2);overflow:hidden;margin:8px 0}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--bad),var(--mid),var(--good))}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.filters button{background:var(--card);border:1px solid var(--bd);color:var(--fg);border-radius:8px;
 padding:6px 12px;cursor:pointer;font-size:13px}.filters button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px;margin:12px 0}
.card h3{margin:0;font-size:15px}.card a{color:var(--acc);text-decoration:none}
.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px 14px;margin:10px 0;font-size:13px}
.kv b{color:var(--mut);font-weight:500}
.buckets{margin-top:10px}details{background:var(--card2);border:1px solid var(--bd);border-radius:10px;margin:6px 0;padding:2px 10px}
details summary{cursor:pointer;padding:8px 0;font-weight:600;font-size:13px}
.bkt{font-size:13px;padding:2px 0 10px}.bkt .note{color:var(--fg);margin:4px 0}
.bkt .af{color:var(--mut);font-size:12px;font-family:ui-monospace,monospace;background:var(--bg);padding:6px 8px;border-radius:6px;margin-top:4px;white-space:pre-wrap}
.sal{float:right;font-size:12px;color:var(--mut)}
pre.prompt{background:var(--card2);border:1px solid var(--bd);border-radius:12px;padding:16px;
 overflow-x:auto;font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap}
.scen{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px;margin:14px 0}
.scen .tag{display:inline-block;background:var(--acc);color:#fff;border-radius:6px;padding:2px 8px;font-size:12px;margin-right:6px}
.warn{color:var(--mid)}.small{font-size:12px;color:var(--mut)}
</style></head><body><div class="wrap">

<h1>쇼츠 분석 공장 — 테스트 실행 리포트</h1>
<div class="mut" id="sub"></div>

<h2>1. 실행 요약</h2>
<div class="row" id="summary"></div>

<h2>2. 파이프라인 스테이지 (이번 실행)</h2>
<div class="mut small">각 쇼츠가 gate→classify→download→storage→2트랙 추출→성과집계→적재→채널갱신→채점을 통과했는지.</div>
<div class="stages" id="stages"></div>

<h2>3. 성과 분포</h2>
<div id="dist"></div>

<h2>4. 쇼츠별 데이터 (<span id="cnt"></span>개)</h2>
<div class="filters" id="filters"></div>
<div id="cards"></div>

<h2>5. 작품정보 루프 시나리오 — 뱅크 → 생성 프롬프트</h2>
<div class="mut small">가상 작품을 정의하고, 그 클러스터로 이 뱅크에서 참조를 인출해 생성 프롬프트가 어떻게 짜이는지 시연.
정답 증류가 아니라 <b>참조 재료</b>로 주입(§3.3).</div>
<div id="scenarios"></div>

</div>
<script>
const DATA=__DATA__, RUN=__RUN__, ST=__STAGES__, SCEN=__SCEN__;
const el=(h)=>{const d=document.createElement('div');d.innerHTML=h;return d.firstElementChild;};
document.getElementById('sub').textContent=`run ${RUN.run_id||''} · 생성 __GENAT__`;

// 요약
const secs=RUN.started_at&&RUN.finished_at?((new Date(RUN.finished_at)-new Date(RUN.started_at))/60000).toFixed(1):'-';
const sm=[['게이트 통과',RUN.n_gate_passed],['적재',RUN.n_ingested],['추출',RUN.n_extracted],
 ['채점',RUN.n_scored],['실패',RUN.n_failed],['소요(분)',secs],['뱅크 총쇼츠','__NSHORTS__'],['생존(active)','__NACTIVE__']];
document.getElementById('summary').innerHTML=sm.map(([l,v])=>
 `<div class="stat"><div class="v">${v??'-'}</div><div class="l">${l}</div></div>`).join('');

// 스테이지
document.getElementById('stages').innerHTML=ST.order.map(s=>{
 const c=ST.ct[s]||{};const f=c.failed||0;
 return `<div class="stg"><div class="nm">${s}</div>
  <div class="n"><span class="ok">${c.success||0}✓</span>${f?` <span class="fail">${f}✗</span>`:''}
  ${c.skipped?` <span class="mut">${c.skipped}⤳</span>`:''}</div></div>`}).join('');

// 분포
const act=DATA.filter(s=>s.lifecycle_status==='active');
const clip=act.filter(s=>s.has_source_video===true).length;
const self=act.filter(s=>s.has_source_video===false).length;
const licN=act.filter(s=>s.is_laeebly_licensed===true).length;
const byC={};act.forEach(s=>{const k=s.cluster_id||'(자체제작/미분류)';byC[k]=byC[k]||{n:0,good:0,mid:0,bad:0};
 byC[k].n++;if(s.perf_label)byC[k][s.perf_label]++;});
document.getElementById('dist').innerHTML=
 `<div class="small" style="margin-bottom:10px">원본 편집 클립 <b>${clip}</b> (라이선스 ${licN}·비라이선스 ${clip-licN}) ·
  자체제작 <b class="warn">${self}</b>(원본 없음→클러스터 미부여)</div>`
 +Object.entries(byC).sort((a,b)=>b[1].n-a[1].n).map(([k,v])=>
 `<div style="margin:8px 0"><b>${k}</b> <span class="mut">${v.n}개</span>
  <span class="lbl good">good ${v.good}</span> <span class="lbl mid">mid ${v.mid}</span> <span class="lbl bad">bad ${v.bad}</span></div>`).join('');

// 필터 + 카드
const DET=__DETJSON__, BK=__BKJSON__;
let filt='all';
function labelChip(s){
 const basis=s.score_basis==='reach_only'?' <span class="chip" title="계속시청 없이 도달·참여로만 채점">reach_only</span>':s.score_basis==='full'?' <span class="chip" title="계속시청 포함 4지표 채점">full</span>':'';
 return (s.perf_label?`<span class="lbl ${s.perf_label}">${s.perf_label} ${s.performance_score}</span>`:'<span class="mut">미채점</span>')+basis;}
function card(s){
 const det=DET.map(k=>s[k]!=null?`<div><b>${k}</b> ${s[k]}</div>`:'').join('');
 const perf=[['views',s.views],['watch_h',s.watch_time_hours],['ctr%',s.ctr_pct],['impr',s.impressions],
  ['kwr',s.kept_watching_rate],['likes',s.likes],['snaps',s.n_snapshots]]
  .map(([k,v])=>v!=null?`<div><b>${k}</b> ${v}</div>`:'').join('');
 const buckets=BK.map(b=>{const v=s[b];if(!v||typeof v!=='object')return '';
  const af=v.ai_features?`<div class="af">${JSON.stringify(v.ai_features,null,1)}</div>`:'';
  return `<div class="bkt"><span class="sal">salience ${v.salience??'-'}</span><b>${b}</b>
   <div class="note">${(v.note||'').replace(/</g,'&lt;')}</div>
   <div class="chips">${(v.tags||[]).map(t=>`<span class="chip">${t}</span>`).join('')}</div>${af}</div>`;}).join('');
 const lic=s.is_laeebly_licensed===true?'라이선스':s.is_laeebly_licensed===false?'비라이선스':'-';
 const src=s.has_source_video===true?'<span class="chip" style="border-color:var(--good)">원본 편집 클립</span>'
   :s.has_source_video===false?'<span class="chip" style="border-color:var(--bad)">⚠ 자체제작(원본 없음)</span>':'';
 return `<div class="card" data-l="${s.perf_label||'none'}" data-src="${s.has_source_video}">
  <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
   <div><h3>${(s.title_text||'(제목없음)').replace(/</g,'&lt;')}</h3>
    <div class="mut" style="font-size:12px">${s.channel_name||''} · <a href="${s.video_url}" target="_blank">${s.shorts_id}</a>
    · ${s.lifecycle_status}</div></div>${labelChip(s)}</div>
  <div class="chips">
   <span class="chip">클러스터: ${s.cluster_id||'null'}</span>
   ${src}
   <span class="chip">${lic}</span>
   ${s.genre?`<span class="chip">장르: ${s.genre}</span>`:''}
   ${s.video_type?`<span class="chip">${s.video_type}</span>`:''}
   <span class="chip">lift ${s.lift_views??'-'}</span>
   <span class="chip">기저 ${s.channel_baseline_views??'-'} (${s.baseline_cascade||'-'})</span></div>
  <div class="kv"><b style="grid-column:1/-1;color:var(--acc)">결정론 피처</b>${det}</div>
  <div class="kv"><b style="grid-column:1/-1;color:var(--acc)">+7D 성과</b>${perf}</div>
  <details class="buckets"><summary>VLM 10버킷 (Gemini ${s.vlm_model||''}, conf ${s.overall_confidence??'-'})</summary>${buckets||'<div class="mut">VLM 없음</div>'}</details>
 </div>`;}
function render(){
 const list=DATA.filter(s=>filt==='all'?true:filt==='none'?!s.perf_label:s.perf_label===filt)
  .sort((a,b)=>(b.performance_score??-1)-(a.performance_score??-1));
 document.getElementById('cards').innerHTML=list.map(card).join('');
 document.getElementById('cnt').textContent=DATA.length;
}
const F=[['all','전체'],['good','good'],['mid','mid'],['bad','bad'],['none','미채점']];
document.getElementById('filters').innerHTML=F.map(([k,l])=>`<button data-f="${k}" class="${k==='all'?'on':''}">${l}</button>`).join('');
document.getElementById('filters').onclick=e=>{if(!e.target.dataset.f)return;
 filt=e.target.dataset.f;[...document.querySelectorAll('.filters button')].forEach(b=>b.classList.toggle('on',b.dataset.f===filt));render();};
render();

// 시나리오
document.getElementById('scenarios').innerHTML=SCEN.map(sc=>`
 <div class="scen"><div><span class="tag">${sc.work.format}×${sc.work.narrative}</span>
  <b>《${sc.work.title}》</b> <span class="mut">— ${sc.work.genre} · ${sc.work.tone.join('/')}</span></div>
  <div class="small" style="margin:6px 0">${sc.work.logline}</div>
  <div class="small ${sc.n_in_cluster<3?'warn':''}">매칭 클러스터 <b>${sc.work.cluster}</b> · 뱅크 내 참조 ${sc.n_in_cluster}개
   ${sc.n_in_cluster<3?'→ 부족分은 전체(global) 폴백 + 출처 태그':''}</div>
  <pre class="prompt">${sc.prompt.replace(/</g,'&lt;')}</pre></div>`).join('');
</script></body></html>"""

REPORT_HTML = REPORT_HTML.replace("__DETJSON__", json.dumps(DET_FEATS)) \
                         .replace("__BKJSON__", json.dumps(BUCKETS))


def main():
    shorts, run, items = fetch()
    print(f"[report] shorts={len(shorts)} run={run.get('run_id','?')} items={len(items)}")
    scen_out = []
    for w in SCENARIOS:
        ret = retrieve(shorts, w["cluster"])
        scen_out.append({"work": w, "n_in_cluster": ret["n_in_cluster"],
                         "prompt": build_prompt(w, ret)})
        print(f"  scenario {w['cluster']}: 참조 {ret['n_in_cluster']}개")
    html = build_html(shorts, run, items, scen_out)
    out = HERE / "report.html"
    out.write_text(html, encoding="utf-8")
    print(f"[report] → {out}")


if __name__ == "__main__":
    main()
