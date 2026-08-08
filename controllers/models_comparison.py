from flask import Blueprint, render_template, request
from flask_login import login_required

from services.ml_service import MLService, MODEL_NAMES, MODEL_CONFIG
from services.preprocessing import is_somali


models_bp = Blueprint('models_bp', __name__)


@models_bp.route('/models', methods=['GET', 'POST'])
@login_required
def index():
    import time
    from collections import Counter
    ml = MLService()
    text = request.form.get('text', '') if request.method == 'POST' else request.args.get('text', '')
    text = text.strip()
    results = {}
    consensus = None
    distribution = {}
    agreement_pct = 0
    high_disagreement = False

    if text and is_somali(text):
        for key in list(ml.models.keys()) + list(ml.transformers.keys()) + list(ml.keras_models.keys()):
            start = time.time()
            pred, proba = ml.predict(text, key)
            elapsed = round((time.time() - start) * 1000, 1)
            if pred is not None:
                r = ml.build_result(pred, proba, key)
                config = MODEL_CONFIG.get(key, {})
                r['type'] = config.get('type', 'unknown')
                r['f1'] = config.get('f1', 0)
                r['elapsed_ms'] = elapsed
                r['key'] = key
                results[key] = r

        if results:
            labels = [r['prediction'] for r in results.values()]
            counter = Counter(labels)
            distribution = dict(counter)
            total = len(labels)
            most_common_label, most_common_count = counter.most_common(1)[0]
            agreement_pct = round(most_common_count / total * 100, 1)
            consensus = {
                'label': most_common_label,
                'agreement': agreement_pct,
                'count': most_common_count,
                'total': total,
            }
            high_disagreement = agreement_pct < 60 and total >= 3

        # Group by type for template
        groups = [
            ('Traditional ML', 'fas fa-calculator', []),
            ('Transformers', 'fas fa-brain', []),
            ('Deep Learning (Keras)', 'fas fa-layer-group', []),
        ]
        type_index = {'traditional': 0, 'transformer': 1, 'keras': 2}
        for key, r in results.items():
            idx = type_index.get(r.get('type', ''), 0)
            groups[idx][2].append((key, r))
        groups = [(name, icon, items) for name, icon, items in groups if items]

    return render_template('models/index.html',
        text=text, results=results, consensus=consensus,
        distribution=distribution, agreement_pct=agreement_pct,
        high_disagreement=high_disagreement, groups=groups if text and results else [],
    )
