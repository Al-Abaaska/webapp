"""Prepare long ASR transcripts as exact, auditable claim spans.

Classification text is always copied from the raw transcript. Search queries are
stored separately and must never replace the classification text.
"""
import hashlib
import re
from difflib import SequenceMatcher


WORD_PATTERN = re.compile(r'\S+', flags=re.UNICODE)
SEARCH_STOPWORDS = {
    'ayaa', 'waxaa', 'waxa', 'waxay', 'waxaan', 'iyo', 'ama', 'oo', 'ah',
    'ay', 'uu', 'u', 'ka', 'ku', 'la', 'loo', 'lagu', 'in', 'ee', 'si',
    'aan', 'isla', 'marka', 'sidoo', 'kale', 'inta', 'waxaana', 'iyagoo',
}

INTRODUCTION_CUES = {
    'asalaamu', 'assalaamu', 'salaan', 'bismilaahi', 'bismillah',
    'magacaygu', 'magaceygu', 'barnaamijka',
}
INTRODUCTION_TRANSITIONS = (
    ('waxaa', 'ka', 'mid', 'ah'),
    ('waxa', 'ka', 'mid', 'ah'),
)


def _words_with_offsets(text):
    return [
        {
            'text': match.group(0),
            'start': match.start(),
            'end': match.end(),
        }
        for match in WORD_PATTERN.finditer(text or '')
    ]


def _normalized_word(word):
    return re.sub(r'[^\w-]', '', str(word or ''), flags=re.UNICODE).casefold()


def _find_word_sequence(words, sequence, start=0, stop=None):
    folded = [_normalized_word(word['text']) for word in words]
    stop = min(len(words), stop if stop is not None else len(words))
    size = len(sequence)
    for index in range(max(0, start), max(0, stop - size + 1)):
        if tuple(folded[index:index + size]) == tuple(sequence):
            return index
    return None


def _leading_introduction_end(words):
    """Return the first substantive word after a confident opening frame.

    The raw transcript is never rewritten. This helper only moves the starting
    boundary past greetings, presenter identity, and programme framing when an
    explicit introduction cue and transition are both present.
    """
    if not words:
        return 0
    folded = [_normalized_word(word['text']) for word in words]
    opening = folded[:min(24, len(folded))]
    if not any(token in INTRODUCTION_CUES for token in opening):
        return 0

    search_stop = min(len(words), 120)
    for sequence in INTRODUCTION_TRANSITIONS:
        position = _find_word_sequence(words, sequence, stop=search_stop)
        if position is not None:
            candidate_start = position + len(sequence)
            if candidate_start < len(words):
                return candidate_start
    return 0


def build_search_query(exact_text, max_terms=12):
    """Build a short retrieval query without modifying classification input."""
    selected = []
    seen = set()
    for raw_word in (exact_text or '').split():
        normalized = re.sub(r'[^\w-]', '', raw_word, flags=re.UNICODE).casefold()
        if (
            len(normalized) < 3
            or normalized in SEARCH_STOPWORDS
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        selected.append(re.sub(r'[^\w-]', '', raw_word, flags=re.UNICODE))
        if len(selected) >= max_terms:
            break
    return ' '.join(selected) or ' '.join((exact_text or '').split()[:max_terms])


def _claim_payload(transcript, words, start_index, end_index, claim_number, context_words):
    start_character = words[start_index]['start']
    end_character = words[end_index - 1]['end']
    exact_text = transcript[start_character:end_character]

    context_start_index = max(0, start_index - context_words)
    context_end_index = min(len(words), end_index + context_words)
    context_start = words[context_start_index]['start']
    context_end = words[context_end_index - 1]['end']

    # Fail closed if an implementation change ever stops producing an exact span.
    if transcript[start_character:end_character] != exact_text:
        raise ValueError('The extracted claim is not an exact transcript span.')

    return {
        'claim_number': claim_number,
        'exact_text': exact_text,
        'classification_text': exact_text,
        'search_query': build_search_query(exact_text),
        'start_word': start_index + 1,
        'end_word': end_index,
        'start_character': start_character,
        'end_character': end_character,
        'context_before': transcript[context_start:start_character].strip(),
        'context_after': transcript[end_character:context_end].strip(),
        'word_count': end_index - start_index,
        'verbatim': True,
        'integrity_sha256': hashlib.sha256(
            exact_text.encode('utf-8')
        ).hexdigest(),
    }


def _locate_candidate(transcript, words, candidate, occupied_spans):
    """Map an LLM-selected quote back to exact transcript word boundaries."""
    candidate = re.sub(r'\s+', ' ', str(candidate or '')).strip()
    if not candidate or len(candidate.split()) < 3:
        return None

    search_at = 0
    folded_transcript = transcript.casefold()
    folded_candidate = candidate.casefold()
    while True:
        start_character = folded_transcript.find(folded_candidate, search_at)
        if start_character < 0:
            break
        end_character = start_character + len(candidate)
        exact_text = transcript[start_character:end_character]
        start_index = next(
            (index for index, word in enumerate(words)
             if word['start'] == start_character),
            None,
        )
        end_index = next(
            (index + 1 for index, word in enumerate(words)
             if word['end'] == end_character),
            None,
        )
        span = (start_character, end_character)
        if (
            exact_text.casefold() == folded_candidate
            and start_index is not None
            and end_index is not None
            and span not in occupied_spans
        ):
            return start_index, end_index, span
        search_at = start_character + 1

    # The selector occasionally corrects one misspelled ASR word. Locate such a
    # proposal conservatively, then copy only the original transcript window.
    candidate_words = candidate.casefold().split()
    if len(candidate_words) > 35:
        return None
    best = None
    minimum = max(3, len(candidate_words) - 2)
    maximum = min(35, len(candidate_words) + 2)
    for start_index in range(len(words)):
        for size in range(minimum, maximum + 1):
            end_index = start_index + size
            if end_index > len(words):
                continue
            window_words = [word['text'].casefold() for word in words[start_index:end_index]]
            score = SequenceMatcher(None, candidate_words, window_words).ratio()
            if best is None or score > best[0]:
                best = (score, start_index, end_index)
    if not best or best[0] < 0.88:
        return None
    _, start_index, end_index = best
    span = (words[start_index]['start'], words[end_index - 1]['end'])
    if span in occupied_spans:
        return None
    return start_index, end_index, span


FRAMING_PREFIXES = (
    ('warbixin', 'gaar', 'ah', 'ayaan', 'idiin', 'kaga', 'haynaa'),
    ('wararkii', 'ugu', 'dambeeyay', 'ayaan', 'sidoo', 'kale', 'idiin', 'kaga',
     'haynaa', 'amuurtaasi', 'dhaka', 'qza'),
    ('war', 'baan', 'idn', 'kaga', 'haynaa'),
    ('waxaan', 'kaloo', 'eegi', 'doonaa'),
)


def _trim_framing_prefix(words, start_index, end_index):
    folded = [word['text'].casefold() for word in words[start_index:end_index]]
    for prefix in FRAMING_PREFIXES:
        if tuple(folded[:len(prefix)]) == prefix:
            return start_index + len(prefix), end_index
    return start_index, end_index


def _split_atomic_predicates(words, start_index, end_index):
    """Split a span when a second explicit Somali predicate begins."""
    markers = {'ayaa', 'waxaa', 'waa', 'ayey'}
    marker_indexes = [
        index for index in range(start_index, end_index)
        if words[index]['text'].casefold() in markers
    ]
    if len(marker_indexes) < 2:
        return [(start_index, end_index)]
    second_marker = marker_indexes[1]
    second_start = second_marker - 1
    if second_start - start_index < 3 or end_index - second_start < 3:
        return [(start_index, end_index)]
    return [
        (start_index, second_start),
        (second_start, end_index),
    ]


def _clean_semantic_spans(words, located):
    """Remove presenter framing, split merged predicates, and deduplicate."""
    cleaned = []
    for start_index, end_index in located:
        if start_index == 0:
            start_index = min(end_index, _leading_introduction_end(words))
        start_index, end_index = _trim_framing_prefix(
            words, start_index, end_index
        )
        if start_index < end_index:
            cleaned.extend(_split_atomic_predicates(
                words, start_index, end_index
            ))

    cleaned = sorted(set(cleaned), key=lambda item: (item[0], item[1]))

    # A short leftover after removing presenter framing is usually context for
    # the immediately following claim (for example, "gudaha israail").
    carried = []
    index = 0
    while index < len(cleaned):
        span = cleaned[index]
        if (
            span[1] - span[0] < 3
            and index + 1 < len(cleaned)
            and span[1] == cleaned[index + 1][0]
        ):
            next_span = cleaned[index + 1]
            cleaned[index + 1] = (span[0], next_span[1])
        else:
            carried.append(span)
        index += 1
    cleaned = sorted(set(carried), key=lambda item: (item[0], item[1]))

    # Preserve short location context that belongs to the following assertion.
    expanded = []
    for start_index, end_index in cleaned:
        if (
            start_index >= 2
            and words[start_index - 2]['text'].casefold() == 'gudaha'
            and words[start_index - 1]['text'].casefold() == 'israail'
        ):
            start_index -= 2
        # Keep the last exact ASR word of a comparison when presenter framing
        # begins immediately after it.
        if (
            end_index + 1 < len(words)
            and words[end_index + 1]['text'].casefold() == 'warbixin'
            and words[start_index]['text'].casefold() == 'waa'
        ):
            end_index += 1
        expanded.append((start_index, end_index))
    cleaned = sorted(set(expanded), key=lambda item: (item[0], item[1]))

    # Same start: retain the more complete span. Same end: retain the cleaner,
    # shorter span, which avoids a trailing word from the previous assertion.
    by_start = {}
    for span in cleaned:
        current = by_start.get(span[0])
        if current is None or span[1] > current[1]:
            by_start[span[0]] = span
    by_end = {}
    for span in by_start.values():
        current = by_end.get(span[1])
        if current is None or span[0] > current[0]:
            by_end[span[1]] = span
    deduplicated = []
    for span in sorted(set(by_end.values()), key=lambda item: (item[0], item[1])):
        replaced = False
        for position, existing in enumerate(deduplicated):
            overlap = max(
                0,
                min(span[1], existing[1]) - max(span[0], existing[0]),
            )
            shorter = min(span[1] - span[0], existing[1] - existing[0])
            if shorter and overlap / shorter >= 0.8:
                # These are the same semantic proposal with a slightly shifted
                # boundary. Prefer the later start, which avoids carrying the
                # final word of the preceding claim into this one.
                if span[0] > existing[0]:
                    deduplicated[position] = span
                replaced = True
                break
        if not replaced:
            deduplicated.append(span)
    return sorted(deduplicated, key=lambda item: (item[0], item[1]))


DEPENDENT_LEADERS = {
    'intii', 'halkaas', 'xilli', 'iyadoo', 'isagoo', 'iyagoo',
    'kaasoo', 'taasoo', 'waxaana', 'waxana', 'kadib', 'kaddib', 'oo',
}
FORWARD_CONTEXT_LEADERS = {'markii'}


def _merge_dependent_claim_spans(words, spans):
    """Join subordinate/context clauses with the event they qualify.

    A semantic claim may contain several related clauses. They stay together
    when an intervening clause begins with temporal, locative, relative, or
    attribution language and therefore cannot stand as an independent claim.
    """
    if not spans:
        return []
    spans = sorted(spans, key=lambda item: (item[0], item[1]))
    groups = []
    current_start, current_end = spans[0]
    if current_start <= 8 and words[0]['text'].casefold() in FORWARD_CONTEXT_LEADERS:
        current_start = 0
    for next_start, next_end in spans[1:]:
        gap_words = [
            word['text'].casefold()
            for word in words[current_end:next_start]
        ]
        next_first = words[next_start]['text'].casefold()
        gap_first = gap_words[0] if gap_words else next_first
        short_gap = len(gap_words) <= 8
        dependent = gap_first in DEPENDENT_LEADERS or next_first in DEPENDENT_LEADERS
        waking_action = short_gap and any(
            token.startswith('toosi') for token in gap_words
        )
        if short_gap and gap_first in FORWARD_CONTEXT_LEADERS:
            groups.append((current_start, current_end))
            current_start, current_end = current_end, next_end
            continue
        if short_gap and (
            dependent or waking_action
        ):
            current_end = max(current_end, next_end)
        else:
            groups.append((current_start, current_end))
            current_start, current_end = next_start, next_end
    groups.append((current_start, current_end))
    return groups


def extract_verbatim_claims(
    transcript,
    max_words=110,
    overlap_words=20,
    context_words=30,
    semantic_candidates=None,
):
    """Create auditable claims that remain exact spans of the transcript.

    Semantic candidates are proposed selections only. A candidate is accepted
    only when it maps to unchanged word boundaries in the original transcript.
    Fixed windows remain a conservative fallback when no selector is available.
    """
    transcript = str(transcript or '')
    words = _words_with_offsets(transcript)
    if not words:
        return []
    if max_words < 20:
        raise ValueError('max_words must be at least 20.')
    if overlap_words < 0 or overlap_words >= max_words:
        raise ValueError('overlap_words must be smaller than max_words.')

    if semantic_candidates is not None:
        located = []
        occupied_spans = set()
        for candidate in semantic_candidates:
            match = _locate_candidate(
                transcript,
                words,
                candidate,
                occupied_spans,
            )
            if not match:
                continue
            start_index, end_index, span = match
            occupied_spans.add(span)
            located.append((start_index, end_index))

        located = _clean_semantic_spans(words, located)
        located = _merge_dependent_claim_spans(words, located)
        atomic_claims = []
        for claim_number, (start_index, end_index) in enumerate(located, 1):
            payload = _claim_payload(
                transcript,
                words,
                start_index,
                end_index,
                claim_number,
                context_words,
            )
            payload['extraction_method'] = 'llm_selected_verbatim'
            atomic_claims.append(payload)
        return atomic_claims

    claims = []
    start_index = 0
    claim_number = 1
    minimum_preferred_words = max(20, int(max_words * 0.55))

    while start_index < len(words):
        proposed_end = min(len(words), start_index + max_words)
        end_index = proposed_end

        # Prefer a natural boundary near the end without deleting any words.
        if proposed_end < len(words):
            for candidate in range(proposed_end, start_index + minimum_preferred_words, -1):
                if re.search(r'[.!?…][\"\'’”)]*$', words[candidate - 1]['text']):
                    end_index = candidate
                    break

        payload = _claim_payload(
            transcript,
            words,
            start_index,
            end_index,
            claim_number,
            context_words,
        )
        payload['extraction_method'] = 'fixed_window_fallback'
        claims.append(payload)
        claim_number += 1
        if end_index >= len(words):
            break
        start_index = max(start_index + 1, end_index - overlap_words)

    return claims


def extract_single_verbatim_claim(transcript, candidate, context_words=30):
    """Return one unchanged main-claim span without splitting its clauses."""
    transcript = str(transcript or '')
    words = _words_with_offsets(transcript)
    if not words or not candidate:
        return []
    match = _locate_candidate(transcript, words, candidate, set())
    if not match:
        return []
    start_index, end_index, _ = match
    if start_index == 0:
        start_index = min(end_index, _leading_introduction_end(words))
    if start_index >= end_index:
        return []
    payload = _claim_payload(
        transcript,
        words,
        start_index,
        end_index,
        1,
        context_words,
    )
    payload['extraction_method'] = 'llm_selected_main_verbatim'
    return [payload]


def extract_complete_verbatim_claim(transcript, context_words=30):
    """Return the complete substantive transcript as one unchanged span.

    Short media does not need semantic main-claim selection. A confidently
    detected presenter introduction may be excluded, but every remaining word
    is copied continuously from the raw transcript.
    """
    transcript = str(transcript or '')
    words = _words_with_offsets(transcript)
    if not words:
        return []
    start_index = _leading_introduction_end(words)
    if start_index >= len(words):
        return []
    payload = _claim_payload(
        transcript,
        words,
        start_index,
        len(words),
        1,
        context_words,
    )
    payload['extraction_method'] = 'complete_substantive_transcript_verbatim'
    return [payload]


def validate_claim_span(transcript, claim):
    """Confirm a client-selected claim remains identical to the raw transcript."""
    start = int(claim.get('start_character', -1))
    end = int(claim.get('end_character', -1))
    exact_text = str(claim.get('exact_text') or '')
    if start < 0 or end <= start:
        return False
    if (transcript or '')[start:end] != exact_text:
        return False
    digest = hashlib.sha256(exact_text.encode('utf-8')).hexdigest()
    return digest == claim.get('integrity_sha256')
