"""어댑터 등록부. models.yaml의 adapter 이름으로 어댑터를 만든다."""


def create_adapter(model, mock_plan=None):
    """ModelEntry -> Adapter.

    mock        외부 호출 없음
    local_vllm  이 서버의 vLLM(localhost)만 호출. 문항이 밖으로 나가지 않는다
    openai · anthropic · gemini  상용 API. 키·D06·D08 확인 전에는 models.yaml에서
                enabled: false로 두어 실행기가 여기까지 오지 않는다.

    어댑터 모듈은 쓸 때만 불러온다(anthropic은 SDK가 필요하고, 판정·집계 단계는 어댑터가 전혀 필요 없다).
    """
    if model.adapter == "mock":
        from .mock import MockAdapter
        return MockAdapter(model, mock_plan or {})
    if model.adapter == "local_vllm":
        from .local_vllm import LocalVllmAdapter
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
    from .base import AdapterSetupError
    raise AdapterSetupError(f"알 수 없는 어댑터 '{model.adapter}' (config/models.yaml {model.model_id})")
