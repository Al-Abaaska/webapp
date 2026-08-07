"""
Text Preprocessing — Cleans input text for the ML models.
This must match the same steps used in the training notebook.
"""
import re


# Common Somali stop words (removed during training, so we remove them here too)
SOMALI_STOP_WORDS = {
    'iyo', 'oo', 'ay', 'uu', 'ku', 'ka', 'la', 'waa', 'ayaa', 'ah',
    'in', 'is', 'maxaa', 'sidee', 'yahay', 'tahay', 'leen', 'tee',
    'kan', 'tan', 'wan', 'wax', 'waxaa', 'waxay', 'waxaan',
    'ma', 'hee', 'baa', 'ee', 'ada', 'igu', 'isaga', 'iyada',
    'nahay', 'noo', 'naga', 'idiin', 'ayagu', 'kuwa', 'kii',
    'tii', 'kee', 'taa', 'wad', 'ayiyo',
}


def clean_text(text):
    """Remove links, mentions, hashtags, punctuation, extra spaces."""
    text = str(text).lower()
    text = re.sub(r'http\S+|www\S+|https\S+', '', text)   # remove URLs
    text = re.sub(r'@\w+|#\w+', '', text)                  # remove @mentions and #hashtags
    text = re.sub(r'[^a-zA-Z\s]', '', text)                # keep letters only
    text = re.sub(r'\s+', ' ', text).strip()                # remove extra spaces
    return text


def remove_stopwords(text):
    """Remove common Somali words that add no meaning for classification."""
    tokens = text.split()
    return ' '.join(w for w in tokens if w not in SOMALI_STOP_WORDS)


def preprocess(text):
    """Full pipeline: clean text + remove stop words."""
    return remove_stopwords(clean_text(text))


def is_somali(text):
    """Return True if the text is in Somali language."""
    from langdetect import detect, LangDetectException
    try:
        return detect(text) == 'so'
    except LangDetectException:
        return False
