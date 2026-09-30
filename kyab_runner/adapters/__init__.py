"""어댑터 등록부. models.yaml의 adapter 이름으로 어댑터를 만든다."""
from .local_vllm import LocalVllmAdapter
from .mock import MockAdapter


def create_adapter(model, mock_plan=None):
    """ModelEntry -> Adapter.

    mock        외부 호출 없음
    local_vllm  이 서버의 vLLM(localhost)만 호출. 문항이 밖으로 나가지 않는다
    상용 API 어댑터는 없다. 키·D06·D08 확인 전까지 호출하지 않는다.
    """
    if model.adapter == "mock":
        return MockAdapter(model, mock_plan or {})
    if model.adapter == "local_vllm":
        return LocalVllmAdapter(model)
    raise ValueError(f"어댑터 '{model.adapter}'는 아직 구현되지 않았습니다 (현재: mock, local_vllm)")
