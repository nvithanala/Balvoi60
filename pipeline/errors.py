"""Typed pipeline failures used to prevent unsafe publication."""


class PipelineError(RuntimeError):
    """Base class for expected, safely reportable pipeline failures."""


class ConfigurationError(PipelineError):
    """Required configuration is missing or invalid."""


class LocalizationError(PipelineError):
    """A non-English edition could not be localized safely."""


class MergeError(PipelineError):
    """Audio could not be merged or probed reliably."""


class AudioValidationError(PipelineError):
    """Merged audio does not meet publication requirements."""


class PublishRejectedError(PipelineError):
    """Publication was rejected before public metadata was updated."""


class DuplicateEditionError(PipelineError):
    """The boundary/language edition is already published or being processed."""
