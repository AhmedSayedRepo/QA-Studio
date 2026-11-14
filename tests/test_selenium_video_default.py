"""Selenium videos default to failed tests only: GIF encoding is the slow,
disk-heavy part, and a passed test's recording is rarely needed."""
import engine as E


def test_video_defaults_to_retain_on_failure():
    assert 'Config.setting("VIDEO", "retain-on-failure")' in E._video_recorder("com.x")
    assert "video=retain-on-failure\n" in E._SH_RUN_PROPS


def test_old_default_migrates_but_user_choice_stays(tmp_path):
    old = tmp_path / "config.properties"
    old.write_text("browser=chrome\r\n" + E._OLD_VIDEO_BLOCK.replace("\n", "\r\n") + "headless=false\r\n",
                   encoding="utf-8", newline="")
    assert E._migrate_video_default(str(old))
    assert "video=retain-on-failure" in old.read_text(encoding="utf-8")
    mine = tmp_path / "mine.properties"
    mine.write_text("video=on\n", encoding="utf-8")
    assert not E._migrate_video_default(str(mine))
    assert mine.read_text(encoding="utf-8") == "video=on\n"


def test_migration_runs_on_every_generate():
    import inspect
    assert "_migrate_video_default(path)" in inspect.getsource(E.apply_run_browsers)
