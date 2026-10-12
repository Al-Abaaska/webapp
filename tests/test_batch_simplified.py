import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.batch_service import build_summary, detect_columns
from services.gemini_service import is_political


class SimplifiedBatchTests(unittest.TestCase):
    def test_single_headline_column_is_selected_as_text(self):
        detected = detect_columns(['Headline'])
        self.assertEqual(detected['text'], 'Headline')
        self.assertEqual(detected['source_url'], '')
        self.assertEqual(detected['source_name'], '')
        self.assertEqual(detected['date'], '')

    def test_summary_contains_scope_results_without_clusters(self):
        items = [
            SimpleNamespace(prediction='Reliable', risk='Low'),
            SimpleNamespace(prediction='Not Political', risk='Invalid'),
            SimpleNamespace(prediction='Scope Unavailable', risk='Invalid'),
        ]
        summary = build_summary(items)
        self.assertEqual(summary['not_political'], 1)
        self.assertEqual(summary['failed'], 1)
        self.assertNotIn('duplicate_or_similar', summary)
        self.assertNotIn('previously_checked', summary)

    def test_security_content_is_political_without_llm_call(self):
        text = (
            'Kooxda Al-Shabaab ayaa weerar ku qaaday saldhig ciidamada '
            'dowladda ay ku leeyihiin Ceeldheer.'
        )
        with patch('services.gemini_service._call_llm') as llm:
            self.assertTrue(is_political(text))
        llm.assert_not_called()

    def test_routine_sports_meeting_can_be_rejected_by_scope_llm(self):
        with patch(
            'services.gemini_service._call_llm', return_value='NOT_POLITICAL'
        ) as llm:
            result = is_political(
                'Wasiirka ciyaaraha ayaa la kulmay kooxda Horseed.'
            )
        self.assertFalse(result)
        self.assertEqual(llm.call_args.kwargs['max_tokens'], 8)


if __name__ == '__main__':
    unittest.main()
