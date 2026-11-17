"""Errors shared by Arena extraction and adapter modules."""


class ArenaExtractorError(RuntimeError):
    """Raised when Arena or an Arena artifact cannot be read safely."""


def error_text(error: BaseException) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ").strip()
    return text[:500] or type(error).__name__
