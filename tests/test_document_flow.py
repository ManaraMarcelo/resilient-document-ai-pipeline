import unittest

from document_flow import PreparedDocument, prepare_with_recovery

LONG_TEXT = "x" * 120


class FakeNative:
    def __init__(self, document: PreparedDocument) -> None:
        self.document = document

    def extract(self, pdf_bytes: bytes) -> PreparedDocument:
        return self.document


class FakeVisual:
    def __init__(self, pages: list[tuple[int, str]]) -> None:
        self.pages = pages
        self.calls = 0

    def read_pages(self, pdf_bytes: bytes, page_count: int) -> list[tuple[int, str]]:
        self.calls += 1
        return self.pages


class PrepareWithRecoveryTests(unittest.TestCase):
    def test_usable_native_text_skips_visual_reading(self) -> None:
        native = FakeNative(PreparedDocument(page_text=(LONG_TEXT, LONG_TEXT), content_hash="h"))
        visual = FakeVisual([])
        document, audit = prepare_with_recovery(b"%PDF", native, visual)
        self.assertEqual(audit["text_source"], "native")
        self.assertFalse(audit["visual_fallback"])
        self.assertEqual(visual.calls, 0)
        self.assertEqual(document.page_text, (LONG_TEXT, LONG_TEXT))

    def test_empty_page_triggers_visual_recovery(self) -> None:
        native = FakeNative(PreparedDocument(page_text=(LONG_TEXT, ""), content_hash="h"))
        visual = FakeVisual([(1, "page one"), (2, "page two")])
        document, audit = prepare_with_recovery(b"%PDF", native, visual)
        self.assertTrue(audit["visual_fallback"])
        self.assertEqual(audit["page_count"], 2)
        self.assertEqual(document.page_text, ("page one", "page two"))
        self.assertEqual(document.content_hash, "h")

    def test_incomplete_visual_recovery_is_rejected(self) -> None:
        native = FakeNative(PreparedDocument(page_text=("", ""), content_hash="h"))
        visual = FakeVisual([(1, "page one"), (2, "  ")])
        with self.assertRaises(ValueError):
            prepare_with_recovery(b"%PDF", native, visual)

    def test_out_of_order_visual_pages_are_rejected(self) -> None:
        native = FakeNative(PreparedDocument(page_text=("", ""), content_hash="h"))
        visual = FakeVisual([(2, "page two"), (1, "page one")])
        with self.assertRaises(ValueError):
            prepare_with_recovery(b"%PDF", native, visual)


if __name__ == "__main__":
    unittest.main()

