"""Golden cases against the real Claude extractor. Skipped unless ANTHROPIC_API_KEY is set."""
import os

import pytest

from app.extractor import ClaudeExtractor
from app.pipeline import Pipeline

from .conftest import NOW, load_samples
from .test_golden import EXPECTED

pytestmark = pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")


def test_golden_with_claude(store, pricing, settings):
    p = Pipeline(store, ClaudeExtractor(settings.extraction_model), pricing, settings)
    got = {sid: p.process(m, now=NOW).decision for sid, m in load_samples()}
    assert got == EXPECTED
