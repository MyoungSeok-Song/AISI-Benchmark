"""시각 읽기의 주인. 기록에 남는 시각은 모두 설정 시간대(runner.yaml timezone)로 여기서 만든다.

표기 규칙
  iso(dt)             ISO 8601 + 시간대, 밀리초   예: 2026-10-05T19:46:29.123+09:00   (04·05·06 시각 열, 보조 로그 ts)
  compact_date(dt)    YYYYMMDD                     RBATCH·RUN·RESULTS 이름의 날짜 부분
  compact_stamp(dt)   YYYYMMDDTHHMMSS              백업 파일 이름(.bak-…)
시험에서는 now()를 치환해 시각을 고정할 수 있다.
"""
import functools
from datetime import datetime
from zoneinfo import ZoneInfo


@functools.lru_cache(maxsize=None)
def _zone(name):
    return ZoneInfo(name)


def now(config):
    """설정 시간대의 현재 시각(aware datetime)."""
    return datetime.now(_zone(config["timezone"]))


def iso(dt, timespec="milliseconds"):
    return dt.isoformat(timespec=timespec)


def compact_date(dt):
    return dt.strftime("%Y%m%d")


def compact_stamp(dt):
    return dt.strftime("%Y%m%dT%H%M%S")
