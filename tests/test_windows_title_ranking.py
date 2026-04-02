from iluminaty.windows import WindowInfo, WindowManager


def _make_window(
    *,
    handle: int,
    title: str,
    x: int = 0,
    y: int = 0,
    width: int = 1200,
    height: int = 800,
    is_visible: bool = True,
    is_minimized: bool = False,
    is_maximized: bool = False,
) -> WindowInfo:
    return WindowInfo(
        handle=handle,
        title=title,
        pid=1,
        x=x,
        y=y,
        width=width,
        height=height,
        is_visible=is_visible,
        is_minimized=is_minimized,
        is_maximized=is_maximized,
    )


def test_find_by_title_prefers_non_minimized_window(monkeypatch):
    mgr = WindowManager()
    windows = [
        _make_window(
            handle=101,
            title="ILUMINATY - Brave",
            x=-32000,
            y=-32000,
            width=160,
            height=28,
            is_minimized=True,
        ),
        _make_window(
            handle=202,
            title="BTCUSD 66,576 - Brave",
            x=100,
            y=100,
            width=1600,
            height=900,
            is_minimized=False,
            is_maximized=True,
        ),
    ]
    monkeypatch.setattr(mgr, "list_windows", lambda: windows)

    selected = mgr._find_by_title("Brave")
    assert selected == 202


def test_find_by_title_prefers_exact_title(monkeypatch):
    mgr = WindowManager()
    windows = [
        _make_window(handle=111, title="Claude - Logs"),
        _make_window(handle=222, title="Claude"),
    ]
    monkeypatch.setattr(mgr, "list_windows", lambda: windows)

    selected = mgr._find_by_title("Claude")
    assert selected == 222
