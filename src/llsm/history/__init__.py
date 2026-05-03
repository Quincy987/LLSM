"""LLSM conversation history + model usage persistence.

Pure data layer: no Tk imports, no llm/ imports. The panel and conversation
controllers depend on this; this module never depends on them. ModelCatalog
takes a providers map at construction time so the catalog can query each
backend's static model list without history/ ever importing llm/.
"""
from .models import Conversation, Message, ModelRef
from .store import ConversationStore
from .model_usage import ModelUsage
from .model_catalog import ModelCatalog

__all__ = [
    "Conversation",
    "Message",
    "ModelRef",
    "ConversationStore",
    "ModelUsage",
    "ModelCatalog",
]
