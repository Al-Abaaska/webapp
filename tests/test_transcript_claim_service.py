import unittest

from services.transcript_claim_service import (
    extract_complete_verbatim_claim,
    extract_single_verbatim_claim,
    extract_verbatim_claims,
    validate_claim_span,
)


class TranscriptClaimServiceTests(unittest.TestCase):
    def test_short_transcript_is_unchanged(self):
        transcript = 'Madaxweynaha ayaa maanta saxiixay sharciga doorashooyinka dalka.'
        claims = extract_verbatim_claims(transcript)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['exact_text'], transcript)
        self.assertEqual(claims[0]['classification_text'], transcript)
        self.assertTrue(validate_claim_span(transcript, claims[0]))

    def test_long_transcript_uses_overlapping_exact_spans(self):
        transcript = ' '.join(f'eray{i}' for i in range(1, 261))
        claims = extract_verbatim_claims(
            transcript,
            max_words=100,
            overlap_words=20,
            context_words=10,
        )
        self.assertGreater(len(claims), 1)
        for claim in claims:
            start = claim['start_character']
            end = claim['end_character']
            self.assertEqual(claim['exact_text'], transcript[start:end])
            self.assertTrue(validate_claim_span(transcript, claim))
        self.assertEqual(claims[0]['start_word'], 1)
        self.assertEqual(claims[-1]['end_word'], 260)

    def test_changed_claim_fails_integrity_check(self):
        transcript = 'Baarlamaanka ayaa ansixiyay miisaaniyadda dowladda.'
        claim = extract_verbatim_claims(transcript)[0]
        claim['exact_text'] = claim['exact_text'].replace('ansixiyay', 'diiday')
        self.assertFalse(validate_claim_span(transcript, claim))

    def test_search_query_is_separate_from_classification_text(self):
        transcript = (
            'Wasaaradda qorsheynta ayaa ansixisay warbixinta waxqabadka '
            'xukuumadda dan qaran.'
        )
        claim = extract_verbatim_claims(transcript)[0]
        self.assertEqual(claim['classification_text'], transcript)
        self.assertNotEqual(claim['search_query'], claim['classification_text'])

    def test_semantic_candidates_become_separate_exact_claims(self):
        transcript = (
            'asalaamu caleekum barnaamijka wararka maanta '
            'huburursi ayaa socda waddamada caaramka ayaa waxa ay dhisanayaan '
            'cuduuddooda militariga dabkan ayaa la xakameyn layahay'
        )
        candidates = [
            'huburursi ayaa socda',
            'waddamada caaramka ayaa waxa ay dhisanayaan cuduuddooda militariga',
            'dabkan ayaa la xakameyn layahay',
        ]
        claims = extract_verbatim_claims(
            transcript,
            semantic_candidates=candidates,
        )
        self.assertEqual([claim['exact_text'] for claim in claims], candidates)
        self.assertTrue(all(validate_claim_span(transcript, claim) for claim in claims))
        self.assertNotIn('asalaamu caleekum', ' '.join(c['exact_text'] for c in claims))

    def test_non_verbatim_semantic_candidate_is_rejected(self):
        transcript = 'israil duqeynta ayey sii waddaa'
        claims = extract_verbatim_claims(
            transcript,
            semantic_candidates=['Israel waxay sii waddaa duqeynta'],
        )
        self.assertEqual(claims, [])

    def test_presenter_framing_is_removed_and_location_is_carried(self):
        transcript = (
            'warbixin gaar ah ayaan idiin kaga haynaa gudaha israail '
            'dab xoogan ayaa ka qaxmaya'
        )
        claims = extract_verbatim_claims(
            transcript,
            semantic_candidates=[
                'warbixin gaar ah ayaan idiin kaga haynaa gudaha israail',
                'dab xoogan ayaa ka qaxmaya',
            ],
        )
        self.assertEqual(
            [claim['exact_text'] for claim in claims],
            ['gudaha israail dab xoogan ayaa ka qaxmaya'],
        )

    def test_two_predicates_are_split_without_rewriting(self):
        transcript = (
            'duonida waxaa xoogeysanaya tartanka hubka '
            'huburursi ayaa socda'
        )
        claims = extract_verbatim_claims(
            transcript,
            semantic_candidates=[transcript],
        )
        self.assertEqual(
            [claim['exact_text'] for claim in claims],
            [
                'duonida waxaa xoogeysanaya tartanka hubka',
                'huburursi ayaa socda',
            ],
        )

    def test_timing_claim_keeps_word_before_report_transition(self):
        transcript = (
            'waa markii ugu horeysay ee sidan ay dhacdo tan iyo dagaalkii '
            'qaboogaan aboobaa warbixin gaar ah ayaan idiin kaga haynaa'
        )
        selected = (
            'waa markii ugu horeysay ee sidan ay dhacdo tan iyo '
            'dagaalkii qaboogaan'
        )
        claims = extract_verbatim_claims(
            transcript,
            semantic_candidates=[selected],
        )
        self.assertEqual(
            claims[0]['exact_text'],
            'waa markii ugu horeysay ee sidan ay dhacdo tan iyo '
            'dagaalkii qaboogaan aboobaa',
        )

    def test_shifted_near_duplicate_keeps_cleaner_boundary(self):
        transcript = (
            'iyadoo xoojinaya agminatar waa markii ugu horeysay ee sidan '
            'ay dhacdo tan iyo dagaalkii qaboogaan aboobaa warbixin'
        )
        claims = extract_verbatim_claims(
            transcript,
            semantic_candidates=[
                'agminatar waa markii ugu horeysay ee sidan ay dhacdo tan '
                'iyo dagaalkii qaboogaan',
                'waa markii ugu horeysay ee sidan ay dhacdo tan iyo '
                'dagaalkii qaboogaan',
            ],
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(
            claims[0]['exact_text'],
            'waa markii ugu horeysay ee sidan ay dhacdo tan iyo '
            'dagaalkii qaboogaan aboobaa',
        )

    def test_related_meeting_context_stays_with_parent_claim(self):
        transcript = (
            'xildhibaan ka tirsan coongreska mareykanka ayaa dhex seexday '
            'baarlamaanka intii uu socday kulan ay lahaayeen guddiga '
            'habraacyada iyo misaaniyadda ee aqalka wakiillada ist mo '
            'halkaas oo siyaasiyiinta mareykanka ay kaga doodayeen hindiisa '
            'sharciyeed lagu fiirinayay dhimista canshuuraha trump'
        )
        candidates = [
            'xildhibaan ka tirsan coongreska mareykanka ayaa dhex seexday '
            'baarlamaanka',
            'intii uu socday kulan ay lahaayeen guddiga habraacyada iyo '
            'misaaniyadda ee aqalka wakiillada ist mo',
            'siyaasiyiinta mareykanka ay kaga doodayeen hindiisa sharciyeed '
            'lagu fiirinayay dhimista canshuuraha trump',
        ]
        claims = extract_verbatim_claims(
            transcript,
            semantic_candidates=candidates,
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['exact_text'], transcript)

    def test_waking_action_and_person_identity_stay_together(self):
        transcript = (
            'markii la gaaray waqtigii loo odeyn lahaa waxkabadalka ayaa '
            'lagu baraarugay in xildhibaan bleyc mur uu kursigiisa ku gamcay '
            'iste mo nowaxaa too siiyay waxaa toosiiyay xildhibaanad mishel '
            'fisbaj oo ah republikaanka soo jeedda gobolka minsota'
        )
        candidates = [
            'waxkabadalka ayaa lagu baraarugay in xildhibaan bleyc mur uu '
            'kursigiisa ku gamcay',
            'xildhibaanad mishel fisbaj oo ah republikaanka soo jeedda '
            'gobolka minsota',
        ]
        claims = extract_verbatim_claims(
            transcript,
            semantic_candidates=candidates,
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['exact_text'], transcript)

    def test_single_main_claim_keeps_related_predicates_together(self):
        transcript = (
            'garsooraha ayaa loo diiday inuu galo mareykanka taasoo sababtay '
            'inuu ka baaqdo ka qeybgalka koobka adduunka'
        )
        claims = extract_single_verbatim_claim(transcript, transcript)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['exact_text'], transcript)
        self.assertEqual(claims[0]['claim_number'], 1)
        self.assertTrue(validate_claim_span(transcript, claims[0]))

    def test_complete_claim_removes_confident_presenter_introduction(self):
        transcript = (
            'asalaamu caleekum bismilaahi raxmaanin raxim magacaygu cabdi '
            'barnaamijka wararka maanta qodobada aan idiin hayno waxaa ka mid '
            'ah dowladda ayaa ansixisay qorshaha cusub'
        )
        claims = extract_complete_verbatim_claim(transcript)
        self.assertEqual(len(claims), 1)
        self.assertEqual(
            claims[0]['exact_text'],
            'dowladda ayaa ansixisay qorshaha cusub',
        )
        self.assertTrue(validate_claim_span(transcript, claims[0]))


if __name__ == '__main__':
    unittest.main()
