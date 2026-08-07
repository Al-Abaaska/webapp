"""Run every available classifier and calculate a transparent ensemble vote."""
from collections import Counter

from ml_service import MODEL_CONFIG


LABELS = ('Reliable', 'Misinformation', 'Misleading')
LABEL_COLORS = {
    'Reliable': '#10b981',
    'Misinformation': '#ef4444',
    'Misleading': '#f59e0b',
}


def run_ensemble(ml_service, text):
    model_results = []
    unavailable = []
    for key, config in sorted(MODEL_CONFIG.items(), key=lambda item: item[1]['rank']):
        if key not in ml_service.available_models:
            unavailable.append(config['name'])
            continue
        try:
            prediction, probabilities = ml_service.predict(text, key)
            if prediction is None:
                unavailable.append(config['name'])
                continue
            result = ml_service.build_result(prediction, probabilities, key)
        except Exception as exc:
            print(f'[Ensemble:{key}] Prediction failed: {str(exc)[:120]}')
            unavailable.append(config['name'])
            continue
        model_results.append({
            'key': key,
            'model': result['model'],
            'prediction': result['prediction'],
            'confidence': result['confidence'],
            'probabilities': result.get('probabilities', {}),
            'rank': config['rank'],
            'type': config['type'],
        })

    if len(model_results) < 2:
        return None

    vote_counts = Counter(result['prediction'] for result in model_results)
    for label in LABELS:
        vote_counts.setdefault(label, 0)
    highest_vote = max(vote_counts.values())
    tied_labels = [label for label in LABELS if vote_counts[label] == highest_vote]

    confidence_totals = {
        label: round(sum(
            result['confidence'] for result in model_results
            if result['prediction'] == label
        ), 2)
        for label in LABELS
    }
    probability_totals = {
        label: round(sum(
            result.get('probabilities', {}).get(label, 0)
            for result in model_results
        ), 2)
        for label in LABELS
    }

    tie_break = None
    if len(tied_labels) == 1:
        winner = tied_labels[0]
    else:
        highest_combined = max(confidence_totals[label] for label in tied_labels)
        confidence_winners = [
            label for label in tied_labels
            if confidence_totals[label] == highest_combined
        ]
        if len(confidence_winners) == 1:
            winner = confidence_winners[0]
            scores = ', '.join(
                f'{label} {confidence_totals[label]:.1f}' for label in tied_labels
            )
            tie_break = (
                f'Vote tie among {", ".join(tied_labels)}. {winner} was selected '
                f'by higher combined model confidence ({scores}).'
            )
        else:
            highest_probability = max(
                probability_totals[label] for label in confidence_winners
            )
            probability_winners = [
                label for label in confidence_winners
                if probability_totals[label] == highest_probability
            ]
            if len(probability_winners) == 1:
                winner = probability_winners[0]
                tie_break = (
                    f'Vote and voter-confidence tie. {winner} was selected by '
                    'higher total class probability across all models.'
                )
            else:
                winner = next(
                    result['prediction']
                    for result in sorted(model_results, key=lambda item: item['rank'])
                    if result['prediction'] in probability_winners
                )
                tie_break = (
                    f'Vote, confidence, and class-probability tie. {winner} was '
                    'selected by the highest-ranked participating model.'
                )

    winning_results = [
        result for result in model_results if result['prediction'] == winner
    ]
    winning_count = len(winning_results)
    total_models = len(model_results)
    agreement = round(winning_count / total_models * 100, 1)
    average_confidence = round(
        sum(result['confidence'] for result in winning_results) / winning_count, 2
    )

    return {
        'prediction': winner,
        'agreement': agreement,
        'average_confidence': average_confidence,
        'winning_count': winning_count,
        'total_models': total_models,
        'expected_models': len(MODEL_CONFIG),
        'vote_counts': {label: vote_counts[label] for label in LABELS},
        'combined_confidence': confidence_totals,
        'tie_break': tie_break,
        'models': model_results,
        'unavailable_models': unavailable,
        'all_models_available': total_models == len(MODEL_CONFIG),
        'badge_color': LABEL_COLORS[winner],
    }
