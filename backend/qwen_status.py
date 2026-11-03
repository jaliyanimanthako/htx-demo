"""Typed readiness failure for backend-owned combined demos."""
from core.qwen_assist import first_served_model
from core.models import qwen_repo


class QwenUnavailable(RuntimeError):
    pass


def require_qwen(url):
    if not url:
        raise QwenUnavailable("Qwen service is not configured")
    try:
        model = first_served_model(url)
        if not isinstance(model, str) or not model.strip():
            raise ValueError("No served model")
        from backend import settings
        selected = settings.CONFIG["models"]["qwen_model"]
        if selected and qwen_repo(selected).lower() != model.lower():
            raise ValueError(f"Qwen serves {model}, but config selects {selected}")
        return model
    except Exception as error:
        raise QwenUnavailable("Backend cannot initialize the Qwen service") from error
