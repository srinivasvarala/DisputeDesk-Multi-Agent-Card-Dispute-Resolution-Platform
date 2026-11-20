"""Presidio redaction for inputs, outputs and logs. All data is synthetic; this proves the
control, it is not a PII program."""

from __future__ import annotations

import logging
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# PERSON is left out on purpose: names are everywhere in dispute text (merchants, customers)
# and the NER model flags too many false positives. Add it when real data is in play.
ENTITIES = ("CREDIT_CARD", "US_SSN", "EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN_CODE")
SPACY_MODEL = "en_core_web_sm"
MIN_SCORE = 0.4


class Redactor:
    def __init__(self) -> None:
        nlp = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
            }
        ).create_engine()
        self._analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
        self._anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]  # presidio ships no annotations

    def find(self, text: str) -> list[RecognizerResult]:
        results = self._analyzer.analyze(text=text, language="en", entities=list(ENTITIES))
        return [r for r in results if r.score >= MIN_SCORE]

    def redact(self, text: str) -> tuple[str, list[str]]:
        """Return the text with each PII span replaced by `<ENTITY_TYPE>`, and the entity types."""
        found = self.find(text)
        if not found:
            return text, []
        operators = {"DEFAULT": OperatorConfig("replace")}
        redacted = self._anonymizer.anonymize(
            text=text,
            # presidio has two RecognizerResult classes with the same fields
            analyzer_results=found,  # type: ignore[arg-type]
            operators=operators,
        ).text
        return redacted, sorted({r.entity_type for r in found})


@lru_cache(maxsize=1)
def get_redactor() -> Redactor:
    return Redactor()


class RedactingFilter(logging.Filter):
    """Logging filter that redacts PII from the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = get_redactor().redact(record.getMessage())[0]
        record.args = None
        return True
