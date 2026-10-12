"""File inspection, validation, and risk labels for batch jobs."""

import math
import os
import re
from collections import Counter

import pandas as pd

from services.claim_service import normalize_claim
from services.preprocessing import is_somali


MAX_BATCH_ROWS = 10000
ALLOWED_EXTENSIONS = {'.csv', '.xlsx', '.xls'}

COLUMN_HINTS = {
    'text': (
        'text', 'claim', 'content', 'post', 'message', 'article', 'news',
        'headline', 'title', 'qoraal', 'war', 'cinwaan',
    ),
    'source_url': ('source_url', 'url', 'link', 'permalink', 'source link'),
    'source_name': ('source_name', 'source', 'page', 'channel', 'publisher', 'account'),
    'date': ('published_at', 'published_date', 'date', 'timestamp', 'created_at', 'time'),
}


def read_batch_file(path):
    extension = os.path.splitext(path)[1].casefold()
    if extension == '.csv':
        try:
            frame = pd.read_csv(path)
        except UnicodeDecodeError:
            frame = pd.read_csv(path, encoding='utf-8-sig')
    elif extension in {'.xlsx', '.xls'}:
        frame = pd.read_excel(path)
    else:
        raise ValueError('Only CSV, XLSX, and XLS files are supported.')
    if len(frame) > MAX_BATCH_ROWS:
        raise ValueError(f'Files are limited to {MAX_BATCH_ROWS:,} rows per batch.')
    return frame


def clean_cell(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ''
    if pd.isna(value):
        return ''
    return str(value).strip()


def detect_columns(columns):
    normalized = {column: re.sub(r'[^a-z0-9]+', '_', str(column).casefold()).strip('_') for column in columns}
    detected = {}
    for role, hints in COLUMN_HINTS.items():
        best = ''
        for column, value in normalized.items():
            if value in hints:
                best = column
                break
        if not best:
            for column, value in normalized.items():
                if any(hint in value for hint in hints):
                    best = column
                    break
        detected[role] = best
    # A one-column file is unambiguous even when its heading is unfamiliar.
    if len(columns) == 1 and not detected.get('text'):
        detected['text'] = columns[0]
    return detected


def preview_rows(frame, limit=8):
    return [
        {str(column): clean_cell(value)[:220] for column, value in row.items()}
        for row in frame.head(limit).to_dict(orient='records')
    ]


def validate_frame(frame, text_column):
    if not text_column or text_column not in frame.columns:
        return {
            'total_rows': len(frame), 'empty_rows': 0, 'duplicate_rows': 0,
            'non_somali_rows': 0, 'invalid_rows': len(frame), 'valid_rows': 0,
        }
    values = [clean_cell(value) for value in frame[text_column].tolist()]
    empty = sum(not value for value in values)
    invalid = sum(bool(value) and len(value) < 5 for value in values)
    normalized = [normalize_claim(value) for value in values]
    duplicate = sum(
        count - 1 for value, count in Counter(value for value in normalized if value).items()
        if count > 1
    )
    non_somali = sum(
        1 for value in values if value and len(value) >= 5 and not is_somali(value)
    )
    valid = len(values) - empty - invalid - non_somali
    return {
        'total_rows': len(values), 'empty_rows': empty, 'duplicate_rows': duplicate,
        'non_somali_rows': non_somali, 'invalid_rows': empty + invalid,
        'valid_rows': max(valid, 0),
    }


def risk_label(prediction, confidence):
    confidence = float(confidence or 0)
    if prediction == 'Misinformation':
        if confidence >= 90:
            return 'Critical'
        if confidence >= 75:
            return 'High'
        if confidence >= 60:
            return 'Medium'
        return 'Uncertain'
    if prediction == 'Misleading':
        if confidence >= 80:
            return 'High'
        if confidence >= 60:
            return 'Medium'
        return 'Uncertain'
    if prediction == 'Reliable':
        return 'Low' if confidence >= 80 else 'Uncertain'
    return 'Invalid'


def build_summary(items):
    labels = Counter(item.prediction for item in items)
    return {
        'total_analyzed': len(items),
        'reliable': labels['Reliable'],
        'misinformation': labels['Misinformation'],
        'misleading': labels['Misleading'],
        'high_risk': sum(item.risk in {'Critical', 'High'} for item in items),
        'not_political': labels['Not Political'],
        'failed': sum(
            item.prediction in {'Error', 'Invalid', 'Not Somali', 'Scope Unavailable'}
            for item in items
        ),
    }
