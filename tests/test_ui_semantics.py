from iluminaty.ui_semantics import UISemanticsEngine


class _UITreeStub:
    available = True

    def __init__(self, elements):
        self._elements = list(elements)

    def get_elements(self, max_depth: int = 5):
        _ = max_depth
        return list(self._elements)


def test_ocr_policy_scales_for_critical_loading_phase():
    sem = UISemanticsEngine()
    policy = sem.ocr_policy(task_phase="loading", criticality="critical", action="click")
    payload = policy.to_dict()
    assert payload["zoom_factor"] >= 2.5
    assert payload["native_preferred"] is True


def test_evaluate_target_detects_overlay_block():
    sem = UISemanticsEngine()
    sem.set_layers(
        ui_tree=_UITreeStub(
            [
                {"name": "Dialog", "role": "dialog", "x": 0, "y": 0, "width": 500, "height": 400, "is_enabled": True},
                {"name": "Disabled", "role": "text", "x": 100, "y": 100, "width": 80, "height": 25, "is_enabled": True},
            ]
        )
    )

    check = sem.evaluate_target(
        x=110,
        y=110,
        monitor_id=1,
        action="click",
        mode="SAFE",
        task_phase="interaction",
    )
    assert check["allowed"] is False
    assert check["reason"] == "blocked_by_overlay"


def test_evaluate_target_accepts_interactable_button():
    sem = UISemanticsEngine()
    sem.set_layers(
        ui_tree=_UITreeStub(
            [
                {"name": "Save", "role": "button", "x": 90, "y": 90, "width": 120, "height": 40, "is_enabled": True},
                {"name": "MainWindow", "role": "window", "x": 0, "y": 0, "width": 900, "height": 700, "is_enabled": True},
            ]
        )
    )
    check = sem.evaluate_target(
        x=120,
        y=105,
        monitor_id=1,
        action="click",
        mode="SAFE",
        task_phase="interaction",
    )
    assert check["allowed"] is True
    assert check["interactable"] is True
