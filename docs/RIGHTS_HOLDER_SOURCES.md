# 권리사별 소스 드라이브 — 로고·마스터 찾는 곳

작품의 **로고**나 **영상 마스터**를 찾을 때 보는 정본 링크. 머신이 바뀌어도 여기만 보면 된다.

## 왜 이 문서가 있나

로고를 찾을 때 laeebly `licensed_video` 만 보면 **놓친다**. 실제 사례(2026-07-29):

| 작품 | `download_link` | guide 인라인 이미지 | 권리사 폴더 |
|---|---|---|---|
| 도깨비 10주년 여행 | 없음 | ✅ 화이트 로고 PNG | ✅ 있음 |
| 스트릿 레스토랑 파이터 | ✅ 로고 6종 | 없음 | ✅ 있음 |
| **언니네 산지직송 in 칼라페** | **없음** | **없음** | **✅ 있음** |

칼라페는 앞의 두 경로로는 전혀 안 잡혀서 "로고 없음"으로 잘못 결론냈다가, 권리사 폴더에서 찾았다.
**세 곳을 모두 확인할 것.**

## 링크

| # | 권리사 | 담당 머신(assignments.json) | 폴더 |
|---|---|---|---|
| 1 | 쿠팡플레이 | macmini-luna1 | [1aHiLGC2…](https://drive.google.com/drive/folders/1aHiLGC2jeAOgeDyZvCJir70L34DToTHT) |
| 2 | CJ ENM | macmini-luna2 | [1xYbRNQ2…](https://drive.google.com/drive/folders/1xYbRNQ2V3OthYNbgE5QX9YKm4QYHbobA) |
| 3 | 웨이브 | — | [1A47eWW-…](https://drive.google.com/drive/folders/1A47eWW-I-XQ5uB3WSX4vV7r_tD_iN3TU) |
| 4 | ENA | — | [1lkYtg3L…](https://drive.google.com/drive/folders/1lkYtg3LX-AvSxvzfKynTodWbQgcWmSuX) |
| 5 | 티빙 | macmini-luna5(유미의 세포들) | [1muArjEc…](https://drive.google.com/drive/folders/1muArjEcK5kg2m65Zw1WxelU5jif-jD9b) |

머신↔권리사 대응은 `config/assignments.json` 의 `label` 이 정본이다(맥3=그외 레이블리, 맥4=외부 협력).
웨이브·ENA 는 현재 배정 라벨에 직접 대응하는 머신이 없다 — 작품 단위로 흩어져 있을 수 있으니
`config/channels.json` 에서 작품명으로 확인할 것.

## 접근 방법 — 두 가지이고 **보이는 게 다르다**

```bash
# ① rclone (권장) — 위 5개 폴더 전부 접근 확인됨(2026-07-29)
rclone lsf "gdrive:" --drive-root-folder-id <폴더ID> -R --files-only

# ② Drive MCP 커넥터 — search_files 로 parentId = '<폴더ID>'
```

⚠️ **한쪽이 실패해도 다른 쪽을 시도할 것.** 자격증명이 달라 결과가 갈린다 — 스트릿 로고 폴더는
MCP 커넥터로는 빈 결과였는데 rclone 으로는 6종이 모두 보였다(실측).

⚠️ **rclone 이 공용 client_id 를 쓰고 있어 2026년 중 중단 예정**이다(실행할 때마다 경고). 끊기면
드라이브 소스 작품 전체의 다운로드가 막힌다 — 별도 client_id 발급이 필요하다.
https://rclone.org/drive/#making-your-own-client-id

## 로고를 찾은 뒤

1. **어두운 밴드용 버전**을 고른다. 쇼츠 하단 밴드가 검정(`#0D0011`)이라 블랙 버전은 안 보인다.
   - 화이트본이 있으면 그것(도깨비·스트릿)
   - 컬러본이면 배경이 투명한 쪽(칼라페: '투명백' ○ / '화이트백' ✗ — 흰 판이 깔려 있다)
2. **트림**한다. 파일 여백이 권리사마다 제각각이라 원본 그대로 쓰면 크기가 어긋난다.
   ```bash
   cd ~/ves/ai-video
   python scripts/normalize_logo.py --code <laeebly 식별코드> --input "<원본>"
   ```
3. 출력된 "박스 적용 시" 크기가 이상하면 컨택트시트로 확인한다.
   ```bash
   python scripts/logo_contact_sheet.py --job-dir <완성된 job> --logo app/assets/logos/<코드>.png \
       --heights 150,200,250,280 --align center
   ```
4. `config/works.json` 의 그 작품에 `branding.logo` 를 적는다. 크기가 전역 기본값
   (`config/loop_policy.json` 의 `logo_box`)과 달라야 하면 `branding.box` 로 예외를 준다.

## 확보 현황 (macmini-luna2 담당분, 2026-07-29)

| 작품 | 로고 | 정규화본 | 박스 적용 |
|---|---|---|---|
| 언니네 산지직송 in 칼라페 | ✅ 권리사 폴더(투명백) | `Rf2Bi.png` | 386x280 |
| 도깨비 10주년 여행 | ✅ guide 첨부(화이트) | `RZsv4.png` | 144x280 |
| 스트릿 레스토랑 파이터 | ✅ 작품 폴더(부서진질감_화이트) | `lt0JP.png` | 262x280 |
| 놀라운 토요일 | ❌ 세 경로 모두 없음 | — | 작품명 텍스트 |
