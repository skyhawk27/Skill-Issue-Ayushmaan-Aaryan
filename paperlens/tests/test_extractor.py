"""
tests/test_extractor.py — Unit tests for reference extraction.
"""

import pytest

from citations.extractor import extract_references
from citations.models import RawReference


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_REFS_BRACKETED = """
[1] Vaswani, A., Shazeer, N., Parmar, N., et al. (2017). "Attention Is All You Need." Advances in Neural Information Processing Systems, 30.

[2] Devlin, J., Chang, M.W., Lee, K., & Toutanova, K. (2019). "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding." Proceedings of NAACL-HLT.

[3] Liu, Y., Ott, M., Goyal, N., et al. (2019). "RoBERTa: A Robustly Optimized BERT Pretraining Approach." arXiv preprint arXiv:1907.11692.
"""

SAMPLE_REFS_DOTTED = """
1. Brown, T., Mann, B., et al. (2020). "Language Models are Few-Shot Learners." NeurIPS 2020.

2. Radford, A., Wu, J., Child, R., Luan, D., Amodei, D., & Sutskever, I. (2019). "Language Models are Unsupervised Multitask Learners."
"""


# ---------------------------------------------------------------------------
# Tests: Shape A — sections with bracket-numbered references
# ---------------------------------------------------------------------------


class TestBracketedReferences:
    def test_extracts_correct_count(self):
        doc = {"sections": [{"section": "References", "text": SAMPLE_REFS_BRACKETED}]}
        refs = extract_references(doc)
        assert len(refs) == 3

    def test_ref_ids_are_correct(self):
        doc = {"sections": [{"section": "References", "text": SAMPLE_REFS_BRACKETED}]}
        refs = extract_references(doc)
        ids = [r.ref_id for r in refs]
        assert ids == ["1", "2", "3"]

    def test_year_extraction(self):
        doc = {"sections": [{"section": "References", "text": SAMPLE_REFS_BRACKETED}]}
        refs = extract_references(doc)
        assert refs[0].year == 2017
        assert refs[1].year == 2019

    def test_title_extraction(self):
        doc = {"sections": [{"section": "References", "text": SAMPLE_REFS_BRACKETED}]}
        refs = extract_references(doc)
        assert refs[0].title is not None
        assert "Attention" in refs[0].title

    def test_returns_raw_reference_type(self):
        doc = {"sections": [{"section": "References", "text": SAMPLE_REFS_BRACKETED}]}
        refs = extract_references(doc)
        assert all(isinstance(r, RawReference) for r in refs)


# ---------------------------------------------------------------------------
# Tests: Shape B — references_text key
# ---------------------------------------------------------------------------


class TestDottedReferences:
    def test_extracts_dotted_format(self):
        doc = {"references_text": SAMPLE_REFS_DOTTED}
        refs = extract_references(doc)
        assert len(refs) == 2

    def test_year_for_dotted(self):
        doc = {"references_text": SAMPLE_REFS_DOTTED}
        refs = extract_references(doc)
        assert refs[0].year == 2020


# ---------------------------------------------------------------------------
# Tests: Shape C — pre-extracted list
# ---------------------------------------------------------------------------


class TestPreExtracted:
    def test_pre_extracted_refs(self):
        doc = {
            "references": [
                {"ref_id": "1", "raw_text": "Some paper 2020", "title": "Some paper", "year": 2020},
                {"ref_id": "2", "raw_text": "Another paper 2021", "title": "Another paper", "year": "2021"},
            ]
        }
        refs = extract_references(doc)
        assert len(refs) == 2
        assert refs[0].title == "Some paper"
        assert refs[1].year == 2021  # string coerced to int


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_doc(self):
        refs = extract_references({})
        assert refs == []

    def test_no_references_section(self):
        doc = {"sections": [{"section": "Introduction", "text": "Blah blah."}]}
        refs = extract_references(doc)
        assert refs == []

    def test_invalid_year_ignored(self):
        doc = {
            "references": [
                {"ref_id": "1", "raw_text": "Bad year", "year": "not_a_year"},
            ]
        }
        refs = extract_references(doc)
        assert refs[0].year is None
