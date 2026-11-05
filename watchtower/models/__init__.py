from watchtower.models.client import ChatOptions, ChatResponse, ModelClient, ModelError, ToolCall
from watchtower.models.config import ModelConfig, from_env
from watchtower.models.ollama import OllamaClient


def client_from_env(**overrides) -> OllamaClient:
    """The configured client. Only Ollama exists today; P5 adds a provider switch here."""
    return OllamaClient.from_config(from_env(**overrides))


__all__ = [
    "ChatOptions",
    "ChatResponse",
    "ModelClient",
    "ModelConfig",
    "ModelError",
    "OllamaClient",
    "ToolCall",
    "client_from_env",
    "from_env",
]
