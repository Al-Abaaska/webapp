"""
LLM Service — Cerebras (primary) + Groq (fallback) for Claim Extraction, Query Generation, Semantic Matching.
Uses OpenAI Python SDK for both providers.
"""
import os
import json
import re
import time
import requests
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

CEREBRAS_KEY = os.getenv('CEREBRAS_API_KEY', '')
GROQ_KEY = os.getenv('GROQ_API_KEY', '')

cerebras_client = (
    OpenAI(api_key=CEREBRAS_KEY, base_url='https://api.cerebras.ai/v1')
    if OpenAI and CEREBRAS_KEY else None
)
groq_client = (
    OpenAI(api_key=GROQ_KEY, base_url='https://api.groq.com/openai/v1')
    if OpenAI and GROQ_KEY else None
)

CEREBRAS_MODELS = ['gpt-oss-120b']
GROQ_MODEL = 'qwen/qwen3.6-27b'

_cerebras_dead = False


def _chat_completion(
    client, base_url, api_key, model, messages, max_tokens,
    temperature=0.3, extra_body=None,
):
    """Call an OpenAI-compatible provider with SDK or direct HTTP fallback."""
    if client is not None:
        request_options = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if extra_body:
            request_options['extra_body'] = extra_body
        response = client.chat.completions.create(
            **request_options
        )
        return response.choices[0].message.content

    response = requests.post(
        f'{base_url}/chat/completions',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json={
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
            **(extra_body or {}),
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f'HTTP {response.status_code}: {response.text[:200]}'
        )
    return response.json()['choices'][0]['message']['content']


def _call_llm(system_prompt, user_prompt, max_tokens=1024, direct_answer=False):
    """Call LLM with Cerebras -> Groq fallback chain."""
    global _cerebras_dead
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt}
    ]

    if CEREBRAS_KEY and not _cerebras_dead:
        for model in CEREBRAS_MODELS:
            try:
                text = _chat_completion(
                    cerebras_client,
                    'https://api.cerebras.ai/v1',
                    CEREBRAS_KEY,
                    model,
                    messages,
                    max_tokens,
                    temperature=0.0 if direct_answer else 0.3,
                    extra_body=(
                        {'reasoning_effort': 'low'} if direct_answer else None
                    ),
                )
                if not text:
                    continue
                text = text.strip()
                text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
                text = re.sub(r'<think>.*', '', text, flags=re.DOTALL).strip()
                if text:
                    return text
            except Exception as e:
                print(f'[Cerebras:{model}] {str(e)[:80]}')
                if '402' in str(e) or 'billing' in str(e).lower():
                    _cerebras_dead = True
                    print('[Cerebras] Billing issue — switching to Groq permanently')
                continue

    if not GROQ_KEY:
        return None

    for attempt in range(2):
        try:
            text = _chat_completion(
                groq_client,
                'https://api.groq.com/openai/v1',
                GROQ_KEY,
                GROQ_MODEL,
                messages,
                max_tokens,
                temperature=0.0 if direct_answer else 0.3,
                extra_body=(
                    {'reasoning_effort': 'none'} if direct_answer else None
                ),
            )
            if not text:
                return None
            text = text.strip()
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            text = re.sub(r'<think>.*', '', text, flags=re.DOTALL).strip()
            return text if text else None
        except Exception as e:
            if '429' in str(e) and attempt < 1:
                print(f'[Groq] Rate limited, waiting 5s...')
                time.sleep(5)
            else:
                print(f'[Groq] Failed: {str(e)[:80]}')
                return None

    print('[LLM] All providers exhausted')
    return None


def _normalize_scope_text(text):
    """Expand common Somali political abbreviations before LLM classification."""
    normalized = text or ''
    replacements = (
        (r'\bM\s*/?\s*W\b', 'Madaxweyne'),
        (r'\bR\s*/?\s*W\b', "Ra'iisul Wasaare"),
        (r'\bXIL\.?\b', 'Xildhibaan'),
    )
    for pattern, replacement in replacements:
        normalized = re.sub(
            pattern, replacement, normalized, flags=re.IGNORECASE
        )
    return normalized


def _has_explicit_security_scope(text):
    """Detect unambiguous government defence/security content.

    The LLM still handles ordinary and ambiguous scope decisions. This narrow
    guard prevents false negatives when ceremonial wording (for example,
    closing a training course) describes an armed-forces activity.
    """
    normalized = re.sub(r'\s+', ' ', (text or '').casefold()).strip()
    strong_security_phrases = (
        'xoogga dalka',
        'ciidanka dhulka',
        'ciidanka badda',
        'ciidanka cirka',
        'ciidanka qalabka sida',
        'ciidamada qaranka',
        'ciidanka qaranka',
        'ciidamada dowladda',
        'ciidanka dowladda',
        'taliyaha ciidanka',
        'taliska ciidanka',
        'ciidanka booliska',
        'nabad sugidda',
        'wasaaradda gaashaandhigga',
        'wasaaradda difaaca',
        'kumaandooska',
        'kumaandoos',
        'aussom',
        'amisom',
        'atmis',
    )
    if any(phrase in normalized for phrase in strong_security_phrases):
        return True

    force_terms = (
        'ciidan', 'ciidamo', 'taliye', 'militari', 'askari', 'sarkaal',
        'guuto', 'cutub', 'maleeshiyaad',
    )
    security_actions = (
        'tababar', 'howlgal', 'hawlgal', 'dagaal', 'weerar', 'qarax',
        'isku dhac', 'xabbad', 'hub', 'saldhig', 'talis', 'difaac',
        'amniga', 'argagixiso', 'al-shabaab', 'la dagaallanka',
    )
    return (
        any(term in normalized for term in force_terms)
        and any(term in normalized for term in security_actions)
    )


def is_political(text):
    """Return True/False for Somali political scope, or None if the LLM fails."""
    system_prompt = (
        'You are a strict Somali-language scope classifier. '
        'Return exactly one token: POLITICAL or NOT_POLITICAL.'
    )
    scope_text = _normalize_scope_text(text)
    if _has_explicit_security_scope(scope_text):
        print('[PoliticalScope] Explicit defence/security content classified as POLITICAL')
        return True
    user_prompt = f"""Go'aami in qoraalkan Af-Soomaaliga ahi yahay SIYAASADEED ama AAN SIYAASADEED AHAYN.

SIYAASADEED waxaa ka mid ah:
- Sharci, siyaasad dadweyne, miisaaniyad, canshuur, maalgelin dowladeed ama adeeg bulsho oo dowladeed.
- Go'aan rasmi ah, magacaabis, xil-ka-qaadis, isla-xisaabtan, musuqmaasuq ama khilaaf maamul.
- Doorasho, xisbi siyaasadeed, mucaarad, baarlamaan ama awood-qaybsi.
- Amni, ciidan, dagaal ama khilaaf ay dowladda ama maamulku dhinac ka tahay.
- Hoggaanka ciidamada, talis ciidan, guuto, kumaandoos, tababar ciidan, hawlgal, hub, saldhig, abaabul ama iskaashi militari oo dowlado dhex mara. Arrimahan waa POLITICAL xitaa haddii ficilka lagu sheegay xaflad, booqasho ama "tababar ayaa loo soo xiray".
- Heshiis dowladeed, xiriir diblomaasiyadeed ama arrin dibadeed.
- Kulan masuul dowladeed keliya marka ujeeddada kulanku khusayso mid ka mid ah arrimahaas.
- Madaxweyne ama Ra'iisul Wasaare loogu yeeray kulan rasmi ah, xasaasi ah ama albaabadu u xiran yihiin, gaar ahaan goob dowladeed, amni ama diblomaasiyadeed sida Xalane.

AAN SIYAASADEED AHAYN waxaa ka mid ah:
- Ciyaaro, madadaalo, cimilo, gaadiid ama sheeko nololeed qofeed.
- Ganacsi, caafimaad, waxbarasho ama diin aan la xiriirin go'aan ama siyaasad dowladeed.
- Dembi ama dhacdo bulsho oo aan lahayn dhinac dowladeed, maamul ama siyaasadeed.
- Kulan, booqasho, hambalyo ama xaflad caadi ah oo masuul ka qayb galay, laakiin aan lahayn go'aan, siyaasad, miisaaniyad, amni ama maamul.
- Wasiir ciyaaraha la kulmay koox ciyaareed, daawaday ciyaar ama hambalyeeyay ciyaartoy, haddii aan la sheegin siyaasad ama go'aan dowladeed.
- Hawl-maalmeed wasaaradeed sida kormeer shaqo, booqasho xarun, daahfur adeeg ama la socod imtixaan, haddii aan la sheegin siyaasad, miisaaniyad, go'aan ama khilaaf dowladeed.

XEERAR:
- Jagada qofka sida "wasiir" ama "madaxweyne" KELIGEED qoraalka siyaasad kama dhigeyso.
- Marka hore aqoonso FICILKA ama ARRINTA ugu weyn. Ku saley go'aanka ficilkaas, hana ku saleyn magaca ama jagada qofka.
- Kulan caadi ah siyaasad ma aha. Kulan wuxuu noqdaa siyaasadeed keliya marka ujeeddadiisu tahay siyaasad, go'aan dowladeed, doorasho, maamul, amni, miisaaniyad ama xiriir dibadeed.
- "Looga yeeray", "kulan xasaasi ah", "kulan albaabadu u xiran yihiin", "wadahadal" ama "wadaxaajood" oo lala xiriiriyo Madaxweyne/Ra'iisul Wasaare iyo goob rasmi ah waa calaamad siyaasadeed, xitaa haddii faahfaahinta ajandaha aan la sheegin.
- Aqoonso soo-gaabinta: MW ama M/W = Madaxweyne; RW ama R/W = Ra'iisul Wasaare; Xil. = Xildhibaan.
- Weedhaha dareen-kicinta ah sida "maxaa kaa qarsoon" iyo "waxba la iskuma reeban" iska dhaaf; qiimee sheegashada ugu weyn ee ka horreysa.
- "Maamul dowladeed" halkan waxaa loola jeedaa go'aan awood leh, isla-xisaabtan, magacaabis, xil-ka-qaadis ama khilaaf maamul; LOOGAMA jeedo hawl-maalmeed caadi ah oo wasaarad fulineyso.
- Erayada "Dowlad", "Wasaarad", "Wasiir", "Agaasime" iyo "kormeer shaqo" kelidood ma dhigayaan qoraalka siyaasadeed.
- Kormeer xarun imtixaan, dugsi, isbitaal ama goob ciyaareed waa NOT_POLITICAL haddii uusan qoraalku sheegin siyaasad, miisaaniyad, go'aan, musuqmaasuq, amni ama khilaaf.
- Xaflad ama munaasabad ay ku lug leeyihiin ciidamada qalabka sida waa POLITICAL marka arrinta ugu weyn tahay tababar, talis, hawlgal, difaac, amni ama iskaashi militari; ha u kala saarin xaflad caadi ah.
- Haddii qoraalku isku dhafan yahay, dooro POLITICAL keliya haddii sheegashada ugu weyn leedahay dhinac dowladeed ama siyaasadeed.
- Ha qiimeyn runnimada qoraalka. Keliya go'aami mawduuciisa.
- Soo saar POLITICAL ama NOT_POLITICAL oo keliya.

TUSAALEYAAL:
- "Wasiirka ciyaaraha ayaa la kulmay kooxda Horseed." -> NOT_POLITICAL
- "Madaxweynaha ayaa hambalyo u diray xulka kubadda cagta." -> NOT_POLITICAL
- "Wasiiru Dowlaha Waxbarashada iyo Agaasimayaal ayaa kormeer shaqo ku tagay xarunta lagu saxayo imtixaanka Fasalka 8-aad." -> NOT_POLITICAL
- "Wasiirka Caafimaadka ayaa booqday isbitaal si uu u arko adeegga bukaannada." -> NOT_POLITICAL
- "MW Xasan Sheekh oo looga yeeray Xalane kulan xasaasi ah." -> POLITICAL
- "Madaxweynaha ayaa galay kulan albaabadu u xiran yihiin oo looga hadlay amniga." -> POLITICAL
- "Wasiirka ciyaaraha ayaa ku dhawaaqay siyaasad cusub iyo miisaaniyad lagu taageerayo kooxaha." -> POLITICAL
- "Baarlamaanka ayaa ansixiyay sharciga doorashooyinka." -> POLITICAL
- "Wasiirka amniga ayaa ka hadlay howlgal ciidan." -> POLITICAL
- "Taliyeyaasha Ciidanka Dhulka Soomaaliya iyo Turkiga ayaa tababar u soo xiray guuto kumaandoos ah." -> POLITICAL

Qoraalka:
{scope_text[:1200]}

Jawaabta:"""

    result = _call_llm(
        system_prompt, user_prompt, max_tokens=8, direct_answer=True
    )
    if not result:
        return None

    token = re.sub(r'[^A-Z_]', '', result.upper())
    if token == 'NOT_POLITICAL':
        return False
    if token == 'POLITICAL':
        return True

    normalized = result.strip().casefold()
    if normalized in ('aan siyaasadeed ahayn', 'aan siyaasad ahayn', 'maya', 'no'):
        return False
    if normalized in ('siyaasadeed', 'haa', 'yes'):
        return True
    print(f'[PoliticalScope] Unrecognized LLM response: {result[:80]}')
    return None


def extract_main_claim(text):
    """Select the main factual claim as an exact span of the submitted text."""
    original_text = str(text or '').strip()
    system_prompt = (
        'Select one exact continuous quote from Somali text. Output only words copied '
        'verbatim from the input. Never rewrite, correct, summarize, or delete words '
        'inside the selected span.'
    )
    user_prompt = f"""Select the single main factual claim from the following Somali text.

Rules:
- Copy one CONTINUOUS span exactly as it appears in the input
- Do not paraphrase, correct spelling, summarize, or replace any word
- Do not join words from different locations
- Output only the exact copied span, with no quotation marks or explanation

Text:
{original_text}

Main Claim:"""

    result = _call_llm(system_prompt, user_prompt, max_tokens=4096)
    if result:
        result = result.strip().strip('"').strip("'")
        if result.lower().startswith('main claim:'):
            result = result[len('main claim:'):].strip()
        if result in original_text:
            return result
        # Case-only changes are still mapped back to the original characters.
        start = original_text.casefold().find(result.casefold())
        if start >= 0:
            return original_text[start:start + len(result)]
        print('[ClaimExtraction] Rejected non-verbatim LLM output; using original text.')
    return original_text


def extract_verbatim_claim_candidates(text):
    """Ask two semantic passes for atomic claims; local code copies final text."""
    original_text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not original_text:
        return []
    system_prompt = (
        'You extract independently checkable factual claims from Somali ASR '
        'transcripts. Return only a JSON array of continuous quotes. Never '
        'summarize, translate, combine distant words, or explain your answer.'
    )
    rules = """Mandatory rules:
- Return every distinct factual assertion as a separate string.
- One assertion per string. Split adjacent assertions even without punctuation.
- Copy the smallest useful continuous quote containing its subject, action, and
  necessary object, place, or time.
- A claim should normally contain 3-25 words.
- Preserve the transcript spelling and word order; do not improve the Somali.
- Include comparison or timing claims such as "waa markii ugu horeysay ...".
- Exclude greetings, prayers, presenter names, programme introductions,
  transitions, promises of a later report, questions, and promotional wording.
- Exclude framing equivalent to "warbixin ayaan idiin kaga haynaa",
  "wararkii ugu dambeeyay", "sidoo kale", "amuurtaasi", "waxaan eegi doonaa",
  or "barnaamijka wararka".
- Inspect every Somali predicate marker (ayaa, waxaa, waa, ayey) because it may
  begin a separate assertion.
- Return valid JSON only: ["exact continuous claim", "exact continuous claim"]
- If no factual claim exists, return [].
"""
    prompts = [
        f"""Extract the atomic factual claims from this transcript.

{rules}

Transcript:
{original_text}

JSON:"""
        ,
        f"""Perform a coverage audit of this transcript. Identify factual claims
that another extractor might miss, especially short adjacent assertions. Return
the complete replacement list, not commentary.

{rules}

Transcript:
{original_text}

JSON:""",
    ]

    candidates = []
    seen = set()
    successful_response = False
    for prompt in prompts:
        result = _call_llm(
            system_prompt,
            prompt,
            max_tokens=4096,
            direct_answer=True,
        )
        if not result:
            continue
        cleaned = result.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        try:
            parsed = json.loads(cleaned)
        except (TypeError, ValueError, json.JSONDecodeError):
            print('[TranscriptClaims] Rejected non-JSON LLM response.')
            continue
        if isinstance(parsed, dict):
            parsed = parsed.get('claims')
        if not isinstance(parsed, list):
            continue
        successful_response = True
        for value in parsed:
            if not isinstance(value, str):
                continue
            candidate = re.sub(r'\s+', ' ', value).strip()
            folded = candidate.casefold()
            if candidate and folded not in seen:
                candidates.append(candidate)
                seen.add(folded)
    return candidates[:80] if successful_response else None


def extract_main_verbatim_claim_candidate(text):
    """Select one complete central claim from an ASR transcript."""
    original_text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not original_text:
        return None
    system_prompt = (
        'Select the single central factual claim from a Somali ASR transcript. '
        'Return valid JSON only. The claim must be one continuous quote copied '
        'from the transcript without correcting or rewriting any word.'
    )
    user_prompt = f"""Select only the MAIN factual claim from this transcript.

Rules:
- Return one continuous exact quote from the transcript.
- Keep all words needed to make the main claim self-contained: subject, action,
  object, reason, time, place, and dependent context when relevant.
- The quote may be long when those details belong to the same central event.
- Exclude greetings, presenter introductions, transitions, reactions, opinions,
  supporting commentary, and secondary claims.
- Do not correct ASR spelling, paraphrase, summarize, or combine distant spans.
- Return JSON only in this form: {{"claim":"exact transcript quote"}}
- If no factual claim exists, return {{"claim":""}}.

Transcript:
{original_text}

JSON:"""
    result = _call_llm(
        system_prompt,
        user_prompt,
        max_tokens=512,
        direct_answer=True,
    )
    if not result:
        return None
    cleaned = result.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError, json.JSONDecodeError):
        print('[TranscriptClaims] Main-claim response was not valid JSON.')
        return None
    if not isinstance(parsed, dict):
        return None
    candidate = re.sub(r'\s+', ' ', str(parsed.get('claim') or '')).strip()
    return candidate or None


def generate_search_queries(original_text, main_claim):
    """Generate 3-5 optimized search queries from the claim."""

    system_prompt = 'Output ONLY search queries in Somali. Nothing else.'
    user_prompt = f"""Write 3-5 SHORT web search queries in Somali about this claim.

Original text:
{original_text}

Claim:
{main_claim}

RULES:
- Each query: MAX 4-6 words
- Focus on: person names, place names, event type
- One query per line
- No numbering, no bullets, no explanations

Your output:"""

    result = _call_llm(system_prompt, user_prompt, max_tokens=256)

    queries = []
    if result:
        skip = ['think', 'analysis', 'analyze', 'here', 'let me', 'output', 'format',
                'example', 'rule', '**', 'input', 'claim:', 'language:', 'step',
                'person:', 'location:', 'event', 'group:', 'role:', 'context:',
                'purpose:', 'based on', 'the claim', 'the query', 'query type',
                'max', 'mix', 'one query', 'specific', 'broader', 'query types',
                'good queries', 'bad queries', 'your output', 'short queries',
                'write', 'about this', 'focus on', 'each query', 'no numbering',
                'keyword', 'queries per', 'include', 'should']
        for line in result.strip().split('\n'):
            q = line.strip().lstrip('0123456789.-) ').strip()
            if q and len(q) > 3:
                if any(s in q.lower() for s in skip):
                    continue
                if q.startswith('"') and q.endswith('"'):
                    q = q[1:-1]
                if q.startswith('\u201c') and q.endswith('\u201d'):
                    q = q[1:-1]
                words = q.split()
                if len(words) > 8:
                    q = ' '.join(words[:8])
                if len(q) > 3:
                    queries.append(q)

    # If LLM failed or produced garbage, extract keywords from text
    if len(queries) < 2:
        print('[QueryGen] LLM queries insufficient, using keyword extraction fallback')
        queries = _extract_search_keywords(original_text, main_claim)

    return queries[:5] if queries else [main_claim[:200]]


def _extract_search_keywords(original_text, main_claim):
    """Extract search keywords from text when LLM fails."""
    import re
    text = original_text.lower()

    # Somali event keywords
    event_words = {
        'iska horimaad': 'iska horimaad',
        'dagaal': 'dagaal',
        'hub': 'hub',
        'maleeshiyaad': 'maleeshiyaad',
        'dhacdo': 'dhacdo',
        'qarax': 'qarax',
        'isqarxinta': 'isqarxinta',
        'banaanbax': 'banaanbax',
        'shir': 'shir',
        'heshiis': 'heshiis',
        'doorasho': 'doorasho',
        'diyaarad': 'diyaarad',
        'ciidamada': 'ciidamada',
        'wasiirka': 'wasiirka',
        'madaxweyne': 'madaxweyne',
        'deegaan': 'deegaan',
    }

    # Find event type
    event = ''
    for kw, val in event_words.items():
        if kw in text:
            event = val
            break

    # Find capitalized entities (names, places)
    words = original_text.split()
    entities = []
    for w in words:
        w_clean = w.strip('.,;:!?()[]{}"\'')
        if len(w_clean) > 3 and w_clean[0].isupper():
            entities.append(w_clean)

    queries = []
    # Combine event + top entities
    if event and entities:
        queries.append(f'{event} {" ".join(entities[:2])}')
    if entities:
        queries.append(' '.join(entities[:3]))
    if event:
        queries.append(event)

    # Add "wararka [first entity] maanta"
    if entities:
        queries.append(f'wararka {entities[0]} maanta')

    # If still nothing, use first 6 words of original text
    if not queries:
        first_words = original_text.split()[:6]
        queries.append(' '.join(first_words))

    return queries


MATCH_STOPWORDS = {
    'ayaa', 'ah', 'aan', 'iyo', 'ama', 'waxaa', 'waxa', 'lagu', 'loo',
    'ugu', 'kala', 'kuso', 'kusoo', 'kasoo', 'dhinac', 'hore', 'maanta',
    'this', 'that', 'with', 'from', 'into', 'about', 'after', 'before',
}


def _match_words(text):
    normalized = re.sub(r'[^\w\s]', ' ', (text or '').casefold())
    return {
        word for word in normalized.split()
        if len(word) > 3 and word not in MATCH_STOPWORDS
    }


def _match_diagnostics(original_text, item, key_phrases=None):
    claim_words = _match_words(original_text)
    title = item.get('title', '')
    snippet = item.get('snippet', '')
    title_words = _match_words(title)
    snippet_words = _match_words(snippet)
    phrases = key_phrases or []
    normalized_title = re.sub(r'[^\w\s]', ' ', title.casefold())
    normalized_snippet = re.sub(r'[^\w\s]', ' ', snippet.casefold())
    exact_in_title = any(phrase and phrase in normalized_title for phrase in phrases)
    exact_in_snippet = any(phrase and phrase in normalized_snippet for phrase in phrases)
    title_shared = len(claim_words & title_words)
    snippet_shared = len(claim_words & snippet_words)
    title_overlap = title_shared / max(len(title_words), 1)
    snippet_overlap = snippet_shared / max(len(claim_words), 1)
    origin = item.get('content_origin', 'search_snippet')
    query_echo = (
        origin == 'search_snippet'
        and exact_in_snippet
        and not exact_in_title
        and (title_shared < 2 or title_overlap < 0.25)
    )
    verified_exact = exact_in_title or (
        origin == 'platform' and exact_in_snippet
    )
    return {
        'claim_words': claim_words,
        'title_shared': title_shared,
        'snippet_shared': snippet_shared,
        'title_overlap': title_overlap,
        'snippet_overlap': snippet_overlap,
        'exact_in_title': exact_in_title,
        'exact_in_snippet': exact_in_snippet,
        'query_echo': query_echo,
        'verified_exact': verified_exact,
        'origin': origin,
    }


def _lexical_match_results(original_text, search_results):
    """Deterministic fallback used when semantic scoring is unavailable."""
    normalized_claim = re.sub(r'[^\w\s]', ' ', original_text.lower())
    claim_words = _match_words(original_text)
    phrases = []
    words = normalized_claim.split()
    for length in (8, 6, 4):
        if len(words) >= length:
            phrases.append(' '.join(words[:length]))

    scored = []
    for result in search_results:
        item = result.copy()
        combined = ' '.join([item.get('title', ''), item.get('snippet', '')])
        result_words = _match_words(combined)
        overlap = len(claim_words & result_words) / max(len(claim_words), 1)
        diagnostics = _match_diagnostics(original_text, item, phrases)
        if diagnostics['query_echo']:
            score = min(10, round(overlap * 100))
            reason = 'Search snippet echoed the submitted claim, but the source title does not match the event.'
        elif diagnostics['verified_exact']:
            score = 95
            reason = 'Distinctive wording appears in verified source content.'
        else:
            score = min(80, round(overlap * 100))
            if diagnostics['origin'] == 'search_snippet' and diagnostics['title_shared'] < 2:
                score = min(score, 35)
            reason = f'Deterministic keyword overlap: {round(overlap * 100)}%. Semantic scoring was unavailable.'
        item['similarity'] = score
        item['scoring_method'] = 'lexical_fallback'
        item['reason'] = reason
        scored.append(item)
    scored.sort(key=lambda value: value.get('similarity', 0), reverse=True)
    return scored


def semantic_match(original_text, search_results):
    """Compare original text against search results. Returns scored results with reasons."""
    if not search_results:
        return []

    # Use original text for matching
    claim_words = _match_words(original_text)

    # Extract key phrases from original text (first 5-10 words)
    key_phrases = []
    words = original_text.split()
    for length in [8, 6, 4]:
        phrase = ' '.join(words[:length])
        if len(phrase) > 10:
            key_phrases.append(phrase.lower())

    results_text = ""
    for i, r in enumerate(search_results[:15]):
        url = r.get('url', '')
        snippet = r.get('snippet', '')
        title = r.get('title', '')
        
        diagnostics = _match_diagnostics(original_text, r, key_phrases)
        
        results_text += f"""
Result {i+1}:
Title: {title}
Snippet: {snippet}
URL: {url}
Content origin: {diagnostics['origin']}
Exact phrase in title: {"YES" if diagnostics['exact_in_title'] else "NO"}
Exact phrase in snippet: {"YES" if diagnostics['exact_in_snippet'] else "NO"}
Likely query echo: {"YES" if diagnostics['query_echo'] else "NO"}
Shared important title words: {diagnostics['title_shared']}
Snippet overlap: {round(diagnostics['snippet_overlap'] * 100)}%
Source: {r.get('source', 'N/A')}
Date: {r.get('date', 'N/A')}
"""

    system_prompt = 'You are a fact-checking assistant. Output ONLY valid JSON arrays. No markdown, no thinking, no explanation.'
    user_prompt = f"""Compare the original text below against each search result. Be strict but fair.

Original Text:
{original_text}

Search Results:
{results_text}

For EACH result, determine:
1. Similarity percentage (0-100)
2. A one-sentence reason

RULES:
- Treat search-engine snippets as unverified summaries, not as source content.
- If a snippet repeats the submitted text but the title describes another subject, score 0-10% as a likely query echo.
- Give 95-100% only when distinctive wording appears in the source title or verified platform content.
- Same event, different wording/angle = 80-95%
- Same event, partial match (mentions key numbers/entities) = 70-85%
- Shared names or locations are not enough. The person, action, place, and event must agree.
- Same actors or region but a different action/event = 15-45%.
- Different event entirely = 0-20%
- Same place but different event = 10-30%

Return ONLY a JSON array:
[
  {{"index": 1, "similarity": 85, "reason": "Same event, different wording."}},
  {{"index": 2, "similarity": 10, "reason": "Completely different topic."}}
]

Output ONLY the JSON array, nothing else"""

    result = _call_llm(system_prompt, user_prompt, max_tokens=4096)
    if not result:
        return _lexical_match_results(original_text, search_results)

    try:
        result = result.strip()
        start = result.find('[')
        end = result.rfind(']')
        if start != -1 and end != -1 and end > start:
            result = result[start:end + 1]
        scores = json.loads(result)
        scored_results = []
        for s in scores:
            idx = s.get('index', 0) - 1
            if 0 <= idx < len(search_results):
                item = search_results[idx].copy()
                try:
                    llm_score = max(0, min(100, float(s.get('similarity', 0))))
                except (TypeError, ValueError):
                    llm_score = 0

                diagnostics = _match_diagnostics(original_text, item, key_phrases)
                if diagnostics['query_echo']:
                    item['similarity'] = min(llm_score, 10)
                    item['reason'] = 'Search snippet echoed the submitted claim, but the source title does not match the event.'
                elif diagnostics['verified_exact']:
                    item['similarity'] = max(llm_score, 95)
                    item['reason'] = f'Exact wording appears in verified source content. {s.get("reason", "")}'
                else:
                    item['similarity'] = llm_score
                    item['reason'] = s.get('reason', '')
                    if (
                        diagnostics['origin'] == 'search_snippet'
                        and diagnostics['title_shared'] < 2
                    ):
                        item['similarity'] = min(item['similarity'], 45)
                item['scoring_method'] = 'semantic_llm'
                scored_results.append(item)

        # Never discard a retrieved source because the LLM omitted its index.
        scored_urls = {item.get('url', '') for item in scored_results}
        missing = [item for item in search_results if item.get('url', '') not in scored_urls]
        scored_results.extend(_lexical_match_results(original_text, missing))
        scored_results.sort(key=lambda x: x['similarity'], reverse=True)
        return scored_results
    except Exception as e:
        print(f'[LLM] Failed to parse match scores: {e}')
        print(f'[LLM] Raw: {result[:300]}')
        return _lexical_match_results(original_text, search_results)


def verify_same_event(main_claim, articles):
    """Use LLM to verify if articles report the SAME EVENT as the main claim."""
    if not articles:
        return []

    articles_text = ""
    for i, a in enumerate(articles[:7]):
        title = a.get('title', '')
        snippet = a.get('snippet', '')
        if not title:
            fr = a.get('first_result', {})
            title = fr.get('title', 'N/A')
            snippet = fr.get('snippet', 'N/A')
        articles_text += f"""
Article {i+1}:
Title: {title}
Snippet: {snippet}
URL: {a.get('url', 'N/A')}
"""

    system_prompt = 'You are a fact-checking assistant. Output ONLY valid JSON arrays. No markdown, no thinking, no explanation.'
    user_prompt = f"""For each article below, determine if it reports about the SAME EVENT or SAME TOPIC as the main claim.

Main Claim:
{main_claim}

Articles:
{articles_text}

For EACH article, answer:
1. "same_event": true or false
2. "reason": One sentence explaining why

Rules:
- If the article is about the SAME PERSON and SAME PLACE, it counts as same_event = true
- If the article mentions the same topic even from a different angle, it counts as true
- Only mark false if completely unrelated
- Be LENIENT — same topic from different perspective = true

Return ONLY a JSON array:
[
  {{"index": 1, "same_event": true, "reason": "Reports the same visit."}},
  {{"index": 2, "same_event": false, "reason": "Completely different topic."}}
]

Output ONLY the JSON array, nothing else"""

    result = _call_llm(system_prompt, user_prompt, max_tokens=4096)
    if not result:
        return articles

    try:
        result = result.strip()
        start = result.find('[')
        end = result.rfind(']')
        if start != -1 and end != -1 and end > start:
            result = result[start:end + 1]
        checks = json.loads(result)
        verified = []
        for c in checks:
            idx = c.get('index', 0) - 1
            if 0 <= idx < len(articles):
                item = articles[idx].copy()
                item['same_event'] = c.get('same_event', False)
                item['verify_reason'] = c.get('reason', '')
                if item['same_event']:
                    verified.append(item)

        if not verified:
            return articles

        return verified
    except Exception as e:
        print(f'[LLM] Failed to parse verification: {e}')
        print(f'[LLM] Raw: {result[:300]}')
        return articles
