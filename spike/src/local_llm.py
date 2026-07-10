"""Strict local structured-output adapter for Ollama-compatible endpoints."""
import json
import re
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

class LocalStructuredOutputError(ValueError): pass

def clean_json_content(content: str) -> str:
    """Remove exactly one outer Markdown fence; do not repair JSON otherwise."""
    if not content or not content.strip():
        raise LocalStructuredOutputError('local_structured_output_empty')
    text=content.strip()
    match=re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n?```[ \t]*',text,re.DOTALL|re.IGNORECASE)
    if match: text=match.group(1).strip()
    try: json.loads(text)
    except json.JSONDecodeError as exc: raise LocalStructuredOutputError(f'local_structured_output_invalid_json: {exc}') from exc
    return text

class LocalOpenAIGenericClient(OpenAIGenericClient):
    """Local-only client; parent calls this hook immediately before json.loads."""
    @staticmethod
    def _strip_code_fences(text: str) -> str:
        return clean_json_content(text)
