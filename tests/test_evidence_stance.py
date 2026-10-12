from services.evidence_service import _classify_stance


def test_stance_is_limited_to_three_supported_values():
    candidates = [
        ({'title': 'War been ah ayaa la beeniyay', 'snippet': '', 'similarity': 98}, 'Reliable'),
        ({'title': 'Warka waa la xaqiijiyay', 'snippet': '', 'similarity': 80}, 'Reliable'),
        ({'title': 'Qoraal la mid ah', 'snippet': '', 'similarity': 70}, 'Misinformation'),
        ({'title': 'War kale', 'snippet': '', 'similarity': 10}, 'Reliable'),
    ]

    stances = {_classify_stance(item, label) for item, label in candidates}

    assert stances <= {'Supports', 'Repeats', 'Unclear'}
    assert 'Refutes' not in stances


def test_contradiction_terms_are_unclear_for_human_review():
    item = {
        'title': 'BBC Somali: warkan waa been abuur',
        'snippet': 'Sheegashada xaqiiqo ma aha.',
        'similarity': 98,
        'evidence_kind': 'trusted',
    }

    assert _classify_stance(item, 'Reliable') == 'Unclear'
