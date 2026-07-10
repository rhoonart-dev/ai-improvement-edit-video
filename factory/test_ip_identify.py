#!/usr/bin/env python
"""비라이선스 쇼츠 원작 식별력 테스트 — Gemini가 영상에서 원작(제목·포맷·서사·장르)을
   구조화 필드로 뽑을 수 있는지 검증. 로컬 downloads/ 영상 사용(재다운로드 없음)."""
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DOWNLOADS, load_settings

IDS = ["KB4GyQCTiOM", "MUc9HW-VOcg", "HJAxffcCe78", "BvJEKu48qME", "n3L3OOZcp0A",
       "OYw-DACFs0s", "H97iDMhCsGI", "AjOZ1-mW_TA", "mONPNCu6Mj4"]

PROMPT = """이 쇼츠가 어떤 '원작'에서 나왔는지 식별하라.
화면 자막·하단 로고·캡션·등장 배우/인물·상황·맥락을 근거로 판단한다.
원작 클립이 아니라 채널이 직접 만든 콘텐츠(이슈정리·게임플레이·밈·자작 논평 등)이면 is_original_content=true.

출력은 JSON 하나. 설명/마크다운/코드펜스 금지:
{
  "source_title": "원작 제목 (정확히. 모르거나 자체제작이면 null)",
  "title_confidence": 0~1,
  "is_original_content": true 또는 false,
  "format_axis": "드라마|숏드라마|영화|예능|애니메이션|논픽션 중 하나",
  "narrative_axis": "연속서사|에피소드완결|단편완결|비서사 중 하나",
  "genre": ["장르 멀티라벨"],
  "evidence": "판단 근거 1문장(로고/자막/배우 등 무엇을 보고 판단했는지)"
}"""


def identify(client, path, model):
    up = client.files.upload(file=str(path))
    for _ in range(90):
        st = getattr(up.state, "name", str(up.state))
        if st == "ACTIVE":
            break
        if st == "FAILED":
            raise RuntimeError("file FAILED")
        time.sleep(1)
        up = client.files.get(name=up.name)
    resp = client.models.generate_content(
        model=model, contents=[up, PROMPT],
        config={"response_mime_type": "application/json", "temperature": 0.1})
    try:
        client.files.delete(name=up.name)
    except Exception:
        pass
    txt = (resp.text or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt[4:].strip() if txt.lower().startswith("json") else txt
    return json.loads(txt)


def main():
    cfg = load_settings()
    from google import genai
    client = genai.Client(api_key=cfg["GEMINI_API_KEY"])
    model = cfg["GEMINI_MODEL"]
    print(f"[test] model={model} | {len(IDS)}개 비라이선스 쇼츠 원작 식별\n")
    out = []
    for sid in IDS:
        vid = next((DOWNLOADS / f"{sid}{e}" for e in (".mp4", ".webm", ".mkv")
                    if (DOWNLOADS / f"{sid}{e}").exists()), None)
        if not vid:
            print(f"  {sid}: 로컬 영상 없음 skip")
            continue
        try:
            r = identify(client, vid, model)
            r["shorts_id"] = sid
            out.append(r)
            orig = "자체제작" if r.get("is_original_content") else "원작有"
            print(f"  {sid} [{orig}]")
            print(f"     원작: {r.get('source_title')}  (conf {r.get('title_confidence')})")
            print(f"     {r.get('format_axis')} × {r.get('narrative_axis')} | 장르 {r.get('genre')}")
            print(f"     근거: {r.get('evidence','')[:90]}\n")
            time.sleep(2)
        except Exception as e:
            print(f"  {sid}: 실패 {str(e)[:100]}\n")
    (DOWNLOADS.parent / "ip_identify_test.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[test] {len(out)}개 결과 → factory/ip_identify_test.json")


if __name__ == "__main__":
    main()
