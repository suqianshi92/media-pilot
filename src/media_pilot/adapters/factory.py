from media_pilot.adapters.ai import AiFilenameParser, AiParseRequest, AiParseResult, MediaType
from media_pilot.adapters.metadata import MetadataProvider
from media_pilot.adapters.openai_compatible_ai import OpenAICompatibleAiAdapter
from media_pilot.adapters.tmdb import TmdbMovieProvider
from media_pilot.config import AdapterMode, AppConfig, MetadataProviderMode


class _NoneAiParser:
    """ai_adapter=none 时的占位 parser——不调用 AI"""

    def parse_filename(self, request: AiParseRequest) -> AiParseResult:
        return AiParseResult(
            media_type=MediaType.UNKNOWN,
            title=None,
            original_title=request.filename,
            year=None,
            season=None,
            episode=None,
            resolution=None,
            release_group=None,
            language=None,
            confidence=0.5,
            reason="ai disabled",
        )


def create_ai_adapter(config: AppConfig) -> AiFilenameParser:
    if config.ai_adapter == AdapterMode.FAKE:
        raise ValueError("ai_adapter=fake is no longer supported in production")

    if config.ai_adapter == AdapterMode.NONE:
        return _NoneAiParser()

    if config.ai_adapter == AdapterMode.REAL:
        if config.llm_api_key is None:
            raise ValueError("llm_api_key is required for real AI adapter")
        if config.llm_base_url is None:
            raise ValueError("llm_base_url is required for real AI adapter")
        if config.llm_model is None:
            raise ValueError("llm_model is required for real AI adapter")
        return OpenAICompatibleAiAdapter(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            model=config.llm_model,
            timeout_seconds=config.llm_timeout_seconds,
            profile=config.llm_prompt_profile.value,
        )

    raise ValueError(f"ai_adapter is not supported: {config.ai_adapter}")


def create_metadata_provider(config: AppConfig) -> MetadataProvider:
    if config.metadata_provider == MetadataProviderMode.FAKE:
        raise ValueError("metadata_provider=fake is no longer supported in production")

    if config.metadata_provider == MetadataProviderMode.TMDB:
        return TmdbMovieProvider.from_config(config)

    raise ValueError(f"metadata_provider is not supported: {config.metadata_provider}")


def create_metadata_provider_by_name(
    config: AppConfig, provider_name: str
) -> MetadataProvider:
    """按 provider 名称创建 metadata provider — 供 profile-aware 工作流使用"""
    if provider_name == "fake":
        raise ValueError("metadata_provider=fake is no longer supported in production")
    if provider_name == "tmdb":
        return TmdbMovieProvider.from_config(config)
    if provider_name == "tpdb":
        from media_pilot.adapters.tpdb import TpdbAdultProvider
        return TpdbAdultProvider.from_config(config)
    raise ValueError(f"不支持的 metadata provider: {provider_name}")
