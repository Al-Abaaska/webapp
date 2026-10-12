(function () {
    var container = document.getElementById('particles');
    if (!container) return;
    for (var i = 0; i < 28; i++) {
        var particle = document.createElement('div');
        particle.className = 'particle';
        particle.style.left = Math.random() * 100 + '%';
        particle.style.animationDuration = 8 + Math.random() * 12 + 's';
        particle.style.animationDelay = Math.random() * 10 + 's';
        particle.style.width = particle.style.height = 1 + Math.random() * 3 + 'px';
        container.appendChild(particle);
    }
})();

document.querySelectorAll('.chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
        document.querySelectorAll('.chip').forEach(function (item) {
            item.classList.remove('active');
        });
        chip.classList.add('active');
    });
});

var demoUses = parseInt(localStorage.getItem('demo_uses') || '0', 10);

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function safeUrl(value) {
    try {
        var parsed = new URL(String(value || ''), window.location.origin);
        return ['http:', 'https:'].indexOf(parsed.protocol) >= 0 ? parsed.href : '#';
    } catch (error) {
        return '#';
    }
}

function setDemoLoading(isLoading) {
    var button = document.getElementById('analyzeBtn');
    button.disabled = isLoading;
    button.innerHTML = isLoading
        ? '<i class="fas fa-spinner fa-spin"></i> Analyzing...'
        : '<i class="fas fa-magnifying-glass"></i> Analyze';
}

function clearOptionalResults() {
    document.getElementById('resClaim').innerHTML = '';
    document.getElementById('resSimilar').innerHTML = '';
    document.getElementById('resModelList').innerHTML = '';
    document.getElementById('resModels').style.display = 'none';
    var risk = document.getElementById('resRisk');
    risk.className = 'result-risk-sign';
    risk.innerHTML = '';
    risk.removeAttribute('title');
}

function showDemoError(message) {
    clearOptionalResults();
    var result = document.getElementById('demoResult');
    var badge = document.getElementById('resBadge');
    badge.textContent = 'Unable to Analyze';
    badge.className = 'result-badge misleading';
    document.getElementById('resConfLabel').textContent = 'Status';
    document.getElementById('resConf').textContent = 'Unavailable';
    document.getElementById('resBars').innerHTML = '';
    document.getElementById('resExplain').textContent = message;
    result.classList.add('show');
}

function showRiskSign(prediction, confidence) {
    var risk = document.getElementById('resRisk');
    var icon = 'fa-circle-question';
    var color = '#64748b';
    var title = 'Uncertain result';
    if (confidence >= 60 && prediction === 'Reliable') {
        icon = 'fa-circle-check';
        color = '#10b981';
        title = confidence >= 80 ? 'Low-risk indication' : 'Review recommended';
    } else if (confidence >= 60 && prediction === 'Misinformation') {
        icon = 'fa-triangle-exclamation';
        color = '#ef4444';
        title = confidence >= 90 ? 'Critical warning indication' : 'High-risk warning indication';
    } else if (confidence >= 60 && prediction === 'Misleading') {
        icon = 'fa-circle-exclamation';
        color = '#f59e0b';
        title = confidence >= 80 ? 'High context-risk indication' : 'Context review recommended';
    }
    risk.className = 'result-risk-sign show';
    risk.style.color = color;
    risk.title = title;
    risk.innerHTML = '<i class="fas ' + icon + '"></i>';
}

function renderModels(data) {
    var details = document.getElementById('resModels');
    var list = document.getElementById('resModelList');
    if (!data.ensemble || !Array.isArray(data.models)) {
        details.style.display = 'none';
        list.innerHTML = '';
        return;
    }
    list.innerHTML = data.models.map(function (model) {
        return '<div class="model-result-row">' +
            '<strong>' + escapeHtml(model.model) + '</strong>' +
            '<span>' + escapeHtml(model.prediction) + '</span>' +
            '<span>' + Number(model.confidence || 0).toFixed(1) + '%</span>' +
            '</div>';
    }).join('');
    details.style.display = 'block';
}

function renderEvidence(data) {
    var container = document.getElementById('resSimilar');
    if (data.evidence_deferred) {
        container.innerHTML = '<div style="margin-top:20px;padding:16px;text-align:center;border:1px solid var(--border);border-radius:12px;color:var(--text-muted);font-size:13px;"><i class="fas fa-magnifying-glass-chart" style="color:var(--primary);"></i> Log in to run the complete Source Evidence Audit and submit the case for human review.</div>';
        return;
    }
    var sources = Array.isArray(data.reported_by) ? data.reported_by : [];
    var similar = Array.isArray(data.similar_content) ? data.similar_content : [];
    if (sources.length) {
        var sourceHtml = '<div style="margin-top:20px;"><p style="font-size:13px;color:var(--text-dim);margin-bottom:10px;font-weight:600;"><i class="fas fa-link" style="color:var(--primary-light);"></i> Related Source Evidence (' + sources.length + ')</p>';
        sources.forEach(function (source) {
            var first = source.first_result || {};
            sourceHtml += '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 0;border-bottom:1px solid var(--border);">' +
                '<div style="flex:1;min-width:0;"><strong style="font-size:13px;color:var(--text);">' + escapeHtml(source.name || source.source || 'Source') + '</strong>' +
                (source.type ? ' <span style="font-size:10px;color:var(--text-dim);">' + escapeHtml(source.type) + '</span>' : '') +
                (first.title ? '<p style="font-size:12px;color:var(--text-muted);margin:4px 0 0;">' + escapeHtml(first.title) + '</p>' : '') + '</div>' +
                '<a href="' + safeUrl(source.url) + '" target="_blank" rel="noopener noreferrer" style="font-size:11px;padding:5px 10px;border:1px solid var(--border);border-radius:6px;color:var(--text-muted);text-decoration:none;">Open</a></div>';
        });
        container.innerHTML = sourceHtml + '</div>';
        return;
    }
    if (similar.length) {
        var similarHtml = '<div style="margin-top:20px;"><p style="font-size:13px;color:var(--text-dim);margin-bottom:10px;font-weight:600;"><i class="fas fa-globe" style="color:var(--primary-light);"></i> Similar Content (' + similar.length + ')</p>';
        similar.forEach(function (item) {
            similarHtml += '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 0;border-bottom:1px solid var(--border);">' +
                '<div style="flex:1;min-width:0;"><strong style="font-size:13px;color:var(--text);">' + escapeHtml(item.source || item.platform || 'Web source') + '</strong>' +
                '<p style="font-size:12px;color:var(--text-muted);margin:4px 0 0;">' + escapeHtml(item.title || '') + '</p></div>' +
                '<a href="' + safeUrl(item.url) + '" target="_blank" rel="noopener noreferrer" style="font-size:11px;padding:5px 10px;border:1px solid var(--border);border-radius:6px;color:var(--text-muted);text-decoration:none;">Open</a></div>';
        });
        container.innerHTML = similarHtml + '</div>';
        return;
    }
    container.innerHTML = '<div style="margin-top:20px;padding:16px;text-align:center;color:var(--text-dim);font-size:13px;"><i class="fas fa-circle-info"></i> No related source evidence was found in this demo search.</div>';
}

function renderPrediction(data) {
    clearOptionalResults();
    var result = document.getElementById('demoResult');
    var badge = document.getElementById('resBadge');
    var confidence = Number(data.confidence || 0);

    if (data.not_political) {
        badge.textContent = 'Not Political';
        badge.className = 'result-badge';
        badge.style.background = '#64748b';
        document.getElementById('resConfLabel').textContent = 'Scope';
        document.getElementById('resConf').textContent = 'Outside PMD';
        document.getElementById('resBars').innerHTML = '';
        document.getElementById('resExplain').textContent = data.analysis || 'This text is outside the political scope of the system.';
        result.classList.add('show');
        return;
    }

    badge.style.background = '';
    badge.textContent = data.ensemble ? 'Final Ensemble Prediction: ' + data.prediction : data.prediction;
    badge.className = 'result-badge ' + String(data.prediction).toLowerCase();
    document.getElementById('resConfLabel').textContent = data.ensemble ? 'Average Winning Confidence' : 'Prediction Confidence';
    document.getElementById('resConf').textContent = confidence.toFixed(1) + '%';
    showRiskSign(data.prediction, confidence);

    var color = data.prediction === 'Reliable' ? 'green' : data.prediction === 'Misinformation' ? 'red' : 'yellow';
    document.getElementById('resBars').innerHTML = '<div class="r-bar"><div class="bar-head"><span class="bar-label ' + color + '">' + escapeHtml(data.prediction) + '</span><span class="bar-val">' + confidence.toFixed(1) + '%</span></div><div class="bar-track"><div class="bar-fill ' + color + '" style="width:' + Math.max(0, Math.min(100, confidence)) + '%"></div></div></div>';
    document.getElementById('resExplain').textContent = 'This is an AI prediction, not a final fact-check. Review the source evidence and use a fact-checker decision for confirmation.';

    if (data.main_claim) {
        document.getElementById('resClaim').innerHTML = '<div style="margin-top:20px;padding:16px;background:var(--input-bg);border:1px solid var(--border);border-radius:12px;"><p style="font-size:12px;color:var(--text-dim);margin-bottom:6px;font-weight:700;"><i class="fas fa-bullseye" style="color:var(--primary-light);"></i> Main Claim</p><p style="font-size:14px;color:var(--text);margin:0;line-height:1.6;">' + escapeHtml(data.main_claim) + '</p></div>';
    }
    renderModels(data);
    renderEvidence(data);
    result.classList.add('show');
}

function runDemo() {
    var text = document.getElementById('demoText').value.trim();
    if (!text) {
        showDemoError('Enter Somali political text before starting the analysis.');
        return;
    }
    if (demoUses >= 3) {
        showDemoError('You have used all three public demonstrations. Log in or register to continue.');
        return;
    }

    var activeChip = document.querySelector('.chip.active');
    var model = activeChip ? activeChip.dataset.model : 'ensemble';
    setDemoLoading(true);
    fetch('/api/demo-predict', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: text, model: model})
    })
        .then(function (response) {
            return response.json().then(function (payload) {
                if (!response.ok) throw new Error(payload.error || 'The prediction request failed.');
                return payload;
            });
        })
        .then(function (data) {
            demoUses += 1;
            localStorage.setItem('demo_uses', String(demoUses));
            renderPrediction(data);
        })
        .catch(function (error) {
            showDemoError(error.message);
        })
        .finally(function () {
            setDemoLoading(false);
        });
}
