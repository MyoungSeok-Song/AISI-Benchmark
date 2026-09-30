"""어댑터 등록부. models.yaml의 adapter 이름으로 어댑터를 만든다."""
from .local_vllm import LocalVllmAdapter
from .mock import MockAdapter


def create_adapter(model, mock_plan=None):
    """ModelEntry -> Adapter.

    mock        외부 호출 없음
    local_vllm  이 서버의 vLLM(localhost)만 호출. 문항이 밖으로 나가지 않는다
    openai · anthropic · gemini  상용 API. 키·D06·D08 확인 전에는 models.yaml에서
                enabled: false로 두어 실행기가 여기까지 오지 않는다.

    상용 어댑터는 쓸 때만 불러온다(anthropic은 SDK가 필요하고, 모의·로컬 실행에는 필요 없다).
    """
    if model.adapter == "mock":
        return MockAdapter(model, mock_plan or {})
    if model.adapter == "local_vllm":
        return LocalVllmAdapter(model)
    if model.adapter == "openai":
        from .openai import OpenAIAdapter
        return OpenAIAdapter(model)
    if model.adapter == "anthropic":
        from .anthropic import AnthropicAdapter
        return AnthropicAdapter(model)
    if model.adapter == "gemini":
        from .gemini import GeminiAdapter
        return GeminiAdapter(model)
    raise ValueError(f"알 수 없는 어댑터 '{model.adapter}'")
