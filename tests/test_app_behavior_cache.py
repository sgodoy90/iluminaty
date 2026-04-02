from pathlib import Path

from iluminaty.app_behavior_cache import AppBehaviorCache


def test_app_behavior_cache_records_and_suggests(tmp_path: Path):
    db_path = tmp_path / "behavior.sqlite3"
    cache = AppBehaviorCache(db_path=str(db_path))
    try:
        cache.record_outcome(
            app_name="brave",
            window_title="chatgpt",
            action="click",
            params={"x": 100, "y": 200},
            success=False,
            reason="focus mismatch",
            method_used="mouse_click",
            recovery_used=False,
            duration_ms=120.0,
        )
        cache.record_outcome(
            app_name="brave",
            window_title="chatgpt",
            action="click",
            params={"x": 100, "y": 200},
            success=True,
            reason="ok",
            method_used="mouse_click",
            recovery_used=True,
            recovery_strategy="retry",
            duration_ms=80.0,
        )

        hint = cache.suggest(action="click", app_name="brave", window_title="chatgpt")
        assert hint["found"] is True
        assert hint["sample_size"] >= 2
        assert hint["recommended_retries"] in (0, 1, 2)

        recent = cache.recent(limit=5)
        assert len(recent) >= 2
        stats = cache.stats()
        assert stats["entries"] >= 2
        assert stats["distinct_apps"] >= 1
    finally:
        cache.close()
