from iluminaty.main import build_parser


def test_monitor_default_is_auto_multi_monitor():
    args = build_parser().parse_args([])
    assert int(args.monitor) == 0


def test_monitor_cli_override_keeps_single_monitor_mode():
    args = build_parser().parse_args(["--monitor", "3"])
    assert int(args.monitor) == 3
