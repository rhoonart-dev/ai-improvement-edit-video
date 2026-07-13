#!/usr/bin/env python
"""§3-1④ 재키잉·백필 순수 로직 테스트 (rekey_eb_ip.py). DB 없이 계획 수립부만."""
from rekey_eb_ip import plan_ip_merges, plan_sf_backfill


IP_ROWS = [
    {"ip_key": "CODE1", "identification_code": "CODE1", "title": "맨 끝줄 소년 (2025)",
     "cluster_id": "드라마×연속서사", "is_laeebly_licensed": True},
    {"ip_key": "t:맨끝줄소년", "identification_code": None, "title": "맨 끝줄 소년",
     "cluster_id": "드라마×연속서사", "is_laeebly_licensed": False},
    {"ip_key": "t:유니크작품", "identification_code": None, "title": "유니크 작품",
     "cluster_id": "예능×에피소드완결", "is_laeebly_licensed": False},
    {"ip_key": "CODE2", "identification_code": "CODE2", "title": "다른 드라마",
     "cluster_id": "드라마×연속서사", "is_laeebly_licensed": True},
]


def test_plan_ip_merges_maps_t_rows_to_code_rows_by_norm_title():
    merges = plan_ip_merges(IP_ROWS)
    assert merges == {"t:맨끝줄소년": "CODE1"}   # 유니크작품은 코드 행 없음 → 유지


def test_plan_sf_backfill():
    sf = [
        # (i) 코드 보유 → ip_key=코드
        {"shorts_id": "a", "identification_code": "CODE1",
         "licensed_video_title": None, "ip_key": None},
        # (ii) 제목만 → eb_ip 코드-키 행과 정규화 매칭 → 코드 상속
        {"shorts_id": "b", "identification_code": None,
         "licensed_video_title": "맨 끝줄 소년 (2025)", "ip_key": None},
        # (iii) 매칭 실패 → 건드리지 않음(비라이선스는 backfill_clusters 재실행 대상)
        {"shorts_id": "c", "identification_code": None,
         "licensed_video_title": None, "ip_key": None},
        # 이미 ip_key 있으면 스킵(멱등)
        {"shorts_id": "d", "identification_code": "CODE2",
         "licensed_video_title": None, "ip_key": "CODE2"},
    ]
    updates, unresolved = plan_sf_backfill(sf, IP_ROWS)
    got = {u["shorts_id"]: u["ip_key"] for u in updates}
    assert got == {"a": "CODE1", "b": "CODE1"}
    assert unresolved == ["c"]


def test_plan_sf_backfill_t_key_fallback():
    """코드-키 행이 없으면 t: 행이라도 매칭(모집단은 최소한 안 갈라지게)."""
    sf = [{"shorts_id": "e", "identification_code": None,
           "licensed_video_title": "유니크 작품", "ip_key": None}]
    updates, unresolved = plan_sf_backfill(sf, IP_ROWS)
    assert updates == [{"shorts_id": "e", "ip_key": "t:유니크작품"}]
