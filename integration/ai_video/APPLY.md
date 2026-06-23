# T0-2 적용 가이드 — ai-video run provenance 스탬핑

기획서 Phase 0 / T0-2. ai-video가 run마다 `run_log["provenance"]`에 git_sha·모델명·config 스냅샷·
프롬프트 해시를 기록하게 해서, 격리 DB `clip_metadata`로 인제스트될 때 `config_hash`/`prompt_versions`가
채워지도록 한다(= "두 run의 config diff 가능").

> **왜 이 repo에 패치로 두는가**: 적용 시점 ai-video가 `feat/loudness-normalization` 브랜치에서
> `app/modules/renderer.py` 미커밋 수정 중이었다. 그 WIP과 섞이지 않도록, 깨끗한 브랜치에서 적용하라.

## 변경 1 — 새 파일 (그대로 복사)
`integration/ai_video/provenance.py` → **`ai-video/app/modules/provenance.py`**

## 변경 2 — `ai-video/app/pipeline.py` 2곳 (가산적, 비파괴)

**(a) import 추가** — 기존 app.modules import 블록에 한 줄:
```python
from app.modules.provenance import build_provenance
```
앵커: `from app.config import AppConfig, Paths, DesignConfig, get_font_path` 근처(같은 import 영역).

**(b) run_log 확정 직후 provenance 스탬프** — `run_pipeline()` 안에서 `run_log`가 만들어지는
`if job_id: ... else: ...` 블록이 끝나고 `print("[OK] 초기화 완료")` 가 나오기 **직전**에 한 줄 삽입:
```python
    run_log.setdefault("provenance", build_provenance(config))
```
- `config`는 같은 함수 상단 `config = AppConfig()` 로 이미 정의돼 있다.
- `setdefault` → 기존 run(이미 provenance 있는 run_log를 디스크에서 재개)은 원본 보존, 신규/누락 시에만 생성(멱등).
- `_slim_run_log()`는 `steps`만 슬림하고 다른 top-level 키는 보존하므로 `provenance`는 그대로 `run_log.json`에 남는다.

## 적용 절차 (WIP과 분리)
```bash
cd /Users/gimsewon/rhoonart/ai-video
# 깨끗한 베이스에서(기획서 기준 feat/cut-4, 또는 합의된 베이스). 현재 WIP은 건드리지 않는다.
git worktree add ../ai-video-t0-2 feat/cut-4      # 또는: git stash 후 git switch -c feat/run-provenance
cp /Users/gimsewon/rhoonart/ai-improve-edit-video/integration/ai_video/provenance.py app/modules/provenance.py
# pipeline.py 변경 1·2 적용 후
python -m py_compile app/modules/provenance.py app/pipeline.py
```

## 검증 (라운드트립)
```bash
# 1) 파이프라인 1회 실행 후 run_log 확인
python -c "import json;d=json.load(open('outputs/<job_id>/run_log.json'));print(json.dumps(d['provenance'],ensure_ascii=False,indent=2))"
#   → git_sha, models{pro,flash}, config.app{...}, prompt_versions{...}, prompt_set_hash 확인

# 2) 격리 DB로 인제스트 → clip_metadata 채워짐
PIPELINE_DB_URL=... python /Users/gimsewon/rhoonart/ai-improve-edit-video/scripts/ingest_aivideo_run.py \
    --run-dir /Users/gimsewon/rhoonart/ai-video/outputs/<job_id>
#   → "provenance 불완전" 경고가 사라지고 config_hash/prompt_versions 가 적재됨
```

## provenance 계약 (인제스트와 1:1)
```jsonc
run_log["provenance"] = {
  "git_sha": "…",
  "models": {"pro": "gemini-3.1-pro-preview", "flash": "gemini-3-flash-preview"},
  "config": {"app": { …asdict(AppConfig)… }},      // design은 후속
  "prompt_set_hash": "…",
  "prompt_versions": {"gemini_template": "…", "story_composition": "…", "relationship_extraction": "…"},
  "reranker_version": null
}
```
