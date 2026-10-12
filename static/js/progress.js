(function () {
    'use strict';

    var activeCount = 0;
    var sequence = 0;
    var activeTokens = new Map();
    var buttonCounts = new WeakMap();
    var originalFetch = window.fetch ? window.fetch.bind(window) : null;

    function ensureProgressUi() {
        var existing = document.getElementById('pmdGlobalProgress');
        if (existing) return existing;
        var root = document.createElement('div');
        root.id = 'pmdGlobalProgress';
        root.setAttribute('role', 'status');
        root.setAttribute('aria-live', 'polite');
        root.setAttribute('aria-hidden', 'true');
        root.innerHTML = '<div class="pmd-global-progress-track"><span></span></div>';
        document.body.appendChild(root);
        return root;
    }

    function actionLabel(element, fallback) {
        if (element && element.dataset && element.dataset.progressLabel) {
            return element.dataset.progressLabel;
        }
        var text = element ? String(element.textContent || '').trim().toLowerCase() : '';
        if (text.includes('transcrib')) return 'Transcribing media...';
        if (text.includes('analy')) return 'Analyzing...';
        if (text.includes('predict')) return 'Running prediction...';
        if (text.includes('investigat')) return 'Starting investigation...';
        if (text.includes('download') || text.includes('pdf')) return 'Preparing download...';
        if (text.includes('upload')) return 'Uploading...';
        if (text.includes('save') || text.includes('update')) return 'Saving changes...';
        if (text.includes('approve')) return 'Approving application...';
        if (text.includes('reject')) return 'Rejecting application...';
        if (text.includes('delete')) return 'Deleting...';
        if (text.includes('archive')) return 'Archiving...';
        if (text.includes('restore')) return 'Restoring...';
        if (text.includes('assign')) return 'Updating assignment...';
        if (text.includes('login') || text.includes('sign in')) return 'Signing in...';
        if (text.includes('register') || text.includes('create account')) return 'Creating account...';
        if (text.includes('verify')) return 'Verifying...';
        if (text.includes('send') || text.includes('resend')) return 'Sending...';
        if (text.includes('filter') || text.includes('apply')) return 'Applying filters...';
        if (text.includes('open') || text.includes('view')) return 'Loading...';
        return fallback || 'Processing...';
    }

    function markButton(button, lockButton) {
        if (!button) return;
        var count = buttonCounts.get(button) || 0;
        if (!count) {
            button.dataset.pmdWasDisabled = button.disabled ? 'true' : 'false';
            button.classList.add('pmd-action-loading');
            button.setAttribute('aria-busy', 'true');
        }
        buttonCounts.set(button, count + 1);
        if (lockButton && 'disabled' in button) button.disabled = true;
    }

    function unmarkButton(button) {
        if (!button) return;
        var count = buttonCounts.get(button) || 0;
        if (count > 1) {
            buttonCounts.set(button, count - 1);
            return;
        }
        buttonCounts.delete(button);
        button.classList.remove('pmd-action-loading');
        button.removeAttribute('aria-busy');
        if ('disabled' in button && button.dataset.pmdWasDisabled === 'false') {
            button.disabled = false;
        }
        delete button.dataset.pmdWasDisabled;
    }

    function start(options) {
        options = options || {};
        var root = ensureProgressUi();
        var token = ++sequence;
        var button = options.button || null;
        activeTokens.set(token, {button: button});
        activeCount += 1;
        markButton(button, Boolean(options.lockButton));
        root.classList.add('active');
        root.setAttribute('aria-hidden', 'false');
        return token;
    }

    function finish(token) {
        var record = activeTokens.get(token);
        if (!record) return;
        activeTokens.delete(token);
        activeCount = Math.max(0, activeCount - 1);
        unmarkButton(record.button);
        if (!activeCount) {
            var root = ensureProgressUi();
            root.classList.remove('active');
            root.setAttribute('aria-hidden', 'true');
        }
    }

    function reset() {
        activeTokens.forEach(function (record) {
            unmarkButton(record.button);
        });
        activeTokens.clear();
        activeCount = 0;
        var root = ensureProgressUi();
        root.classList.remove('active');
        root.setAttribute('aria-hidden', 'true');
    }

    window.PMDProgress = {
        start: start,
        finish: finish,
        reset: reset,
        actionLabel: actionLabel,
    };

    if (originalFetch) {
        window.fetch = function (input, options) {
            var requestOptions = options || {};
            var method = String(
                requestOptions.method || (input && input.method) || 'GET'
            ).toUpperCase();
            var showProgress = method !== 'GET' && requestOptions.pmdProgress !== false;
            var nativeOptions = requestOptions;
            if (Object.prototype.hasOwnProperty.call(requestOptions, 'pmdProgress')) {
                nativeOptions = Object.assign({}, requestOptions);
                delete nativeOptions.pmdProgress;
            }
            if (!showProgress) return originalFetch(input, nativeOptions);
            var focused = document.activeElement;
            var button = focused && focused.closest ? focused.closest('button, a') : null;
            var token = start({
                button: button,
                label: actionLabel(button, 'Processing request...'),
                lockButton: false,
            });
            try {
                return originalFetch(input, nativeOptions).finally(function () {
                    finish(token);
                });
            } catch (error) {
                finish(token);
                throw error;
            }
        };
    }

    document.addEventListener('DOMContentLoaded', function () {
        ensureProgressUi();

        document.addEventListener('submit', function (event) {
            var form = event.target;
            window.setTimeout(function () {
                if (event.defaultPrevented || form.dataset.progressManual === 'true') return;
                var submitter = event.submitter || form.querySelector('[type="submit"]');
                start({
                    button: submitter,
                    label: actionLabel(submitter, 'Processing...'),
                    lockButton: true,
                });
            }, 0);
        });

        document.addEventListener('click', function (event) {
            if (event.defaultPrevented || event.button !== 0) return;
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            var link = event.target.closest('a[href]');
            if (!link || link.dataset.noProgress === 'true') return;
            if (link.target && link.target !== '_self') return;
            var rawHref = link.getAttribute('href') || '';
            if (!rawHref || rawHref === '#' || rawHref.startsWith('#') || rawHref.startsWith('javascript:')) return;
            var destination;
            try {
                destination = new URL(link.href, window.location.href);
            } catch (_error) {
                return;
            }
            if (destination.origin !== window.location.origin) return;
            var isDownload = link.hasAttribute('download') || /\/download(?:\?|$)/.test(destination.pathname);
            var token = start({
                button: link,
                label: actionLabel(link, isDownload ? 'Preparing download...' : 'Loading page...'),
                lockButton: false,
            });
            if (isDownload) {
                window.setTimeout(function () { finish(token); }, 1600);
            }
        });
    });

    window.addEventListener('pageshow', reset);
}());
