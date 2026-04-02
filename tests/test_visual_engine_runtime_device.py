from iluminaty.visual_engine import LocalSmolVLMProvider


class _FakeCuda:
    def __init__(self, available: bool, bf16_supported: bool):
        self._available = bool(available)
        self._bf16_supported = bool(bf16_supported)

    def is_available(self) -> bool:
        return self._available

    def is_bf16_supported(self) -> bool:
        return self._bf16_supported


class _FakeTorch:
    float16 = "float16"
    float32 = "float32"
    bfloat16 = "bfloat16"

    def __init__(self, cuda_available: bool, bf16_supported: bool):
        self.cuda = _FakeCuda(cuda_available, bf16_supported)


def test_runtime_auto_uses_cuda_when_available():
    fake_torch = _FakeTorch(cuda_available=True, bf16_supported=False)
    runtime = LocalSmolVLMProvider._resolve_torch_runtime(fake_torch, "auto", "auto")

    assert runtime["use_cuda"] is True
    assert runtime["device"] == "cuda:0"
    assert runtime["dtype_label"] == "fp16"


def test_runtime_cpu_policy_disables_cuda_even_if_available():
    fake_torch = _FakeTorch(cuda_available=True, bf16_supported=True)
    runtime = LocalSmolVLMProvider._resolve_torch_runtime(fake_torch, "cpu", "bf16")

    assert runtime["use_cuda"] is False
    assert runtime["device"] == "cpu"
    assert runtime["dtype_label"] == "fp32"


def test_runtime_bf16_falls_back_to_fp16_if_unsupported():
    fake_torch = _FakeTorch(cuda_available=True, bf16_supported=False)
    runtime = LocalSmolVLMProvider._resolve_torch_runtime(fake_torch, "cuda", "bf16")

    assert runtime["use_cuda"] is True
    assert runtime["device"] == "cuda:0"
    assert runtime["dtype_label"] == "fp16"
