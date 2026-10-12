/* magic.js — subtle premium reveal + count-up animations */
(function() {
    var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function initReveal() {
        var els = document.querySelectorAll('[data-reveal]');
        if (!els.length) return;

        if (reduceMotion || !('IntersectionObserver' in window)) {
            els.forEach(function(el) {
                el.classList.add('revealed');
                el.style.opacity = 1;
                el.style.transform = 'none';
            });
            return;
        }

        var observer = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    var el = entry.target;
                    var delay = parseInt(el.getAttribute('data-reveal-delay'), 10) || 0;
                    el.style.transitionDelay = delay + 'ms';
                    el.classList.add('revealed');
                    observer.unobserve(el);
                }
            });
        }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

        els.forEach(function(el) { observer.observe(el); });
    }

    function initCountUp() {
        var els = document.querySelectorAll('[data-count]');
        if (!els.length) return;

        function run(el) {
            var target = parseFloat(el.getAttribute('data-count'));
            if (isNaN(target)) return;
            var duration = parseInt(el.getAttribute('data-count-duration'), 10) || 1400;
            var start = null;

            function step(ts) {
                if (!start) start = ts;
                var progress = Math.min((ts - start) / duration, 1);
                var eased = 1 - Math.pow(1 - progress, 3);
                var val = target * eased;
                el.textContent = (target % 1 === 0 ? Math.round(val) : val.toFixed(1));
                if (progress < 1) requestAnimationFrame(step);
                else el.textContent = (target % 1 === 0 ? Math.round(target) : target.toFixed(1));
            }
            requestAnimationFrame(step);
        }

        if (reduceMotion || !('IntersectionObserver' in window)) {
            els.forEach(function(el) {
                var target = parseFloat(el.getAttribute('data-count'));
                el.textContent = (target % 1 === 0 ? Math.round(target) : target.toFixed(1));
            });
            return;
        }

        var observer = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    run(entry.target);
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.5 });

        els.forEach(function(el) { observer.observe(el); });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { initReveal(); initCountUp(); });
    } else {
        initReveal();
        initCountUp();
    }
})();
