"""
Shared exception types for the AI suggestion pipeline. A separate module (not
defined in ai_provider.py) purely to avoid a circular import: claude.py and
gemini.py need to raise these, and ai_provider.py imports both of them --
if the exceptions lived in ai_provider.py, claude.py/gemini.py importing them
back would be a cycle.
"""


class ImageFetchError(Exception):
    """The image behind an image_alt suggestion request could not be fetched
    or was not a supported type (see services/image_fetch.py). Raised instead
    of silently falling back to a text-only guess -- Part 1 of the image_alt
    hardening pass requires that no visual analysis being possible is a
    visible, controlled failure, never a pretended one."""


class AIGenerationError(Exception):
    """The AI provider's response for an image_alt suggestion request could
    not be parsed into the required JSON schema even after one retry (see
    ai_provider.generate_image_alt_suggestions). Raised instead of returning
    an empty suggestion list, so the caller/UI can tell "the model produced
    nothing useful" apart from "there are no suggestions yet"."""
