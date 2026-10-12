document.addEventListener('DOMContentLoaded', function() {
    // Auto-dismiss flash messages after 5 seconds
    document.querySelectorAll('.flash-msg').forEach(function(msg) {
        setTimeout(function() {
            msg.style.opacity = '0';
            msg.style.transform = 'translateY(-10px)';
            msg.style.transition = 'all 0.3s ease';
            setTimeout(function() { msg.remove(); }, 300);
        }, 5000);
    });

    // Flash close buttons
    document.querySelectorAll('.flash-msg .close-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            this.closest('.flash-msg').remove();
        });
    });

    // Sidebar toggle on mobile
    var hamburger = document.getElementById('hamburgerBtn');
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebarOverlay');

    function setSidebarOpen(open) {
        if (!sidebar) return;
        sidebar.classList.toggle('open', open);
        if (overlay) overlay.classList.toggle('active', open);
        if (hamburger) {
            hamburger.setAttribute('aria-expanded', open ? 'true' : 'false');
            hamburger.setAttribute('aria-label', open ? 'Close page menu' : 'Open page menu');
        }
        document.body.classList.toggle('sidebar-open', open);
    }

    if (hamburger && sidebar) {
        hamburger.addEventListener('click', function() {
            setSidebarOpen(!sidebar.classList.contains('open'));
        });
    }
    if (overlay && sidebar) {
        overlay.addEventListener('click', function() {
            setSidebarOpen(false);
        });
    }
    if (sidebar) {
        sidebar.querySelectorAll('a').forEach(function(link) {
            link.addEventListener('click', function() { setSidebarOpen(false); });
        });
    }
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && sidebar && sidebar.classList.contains('open')) {
            setSidebarOpen(false);
            if (hamburger) hamburger.focus();
        }
    });
    window.addEventListener('resize', function() {
        if (window.innerWidth > 900) setSidebarOpen(false);
    });

    // User dropdown toggle
    var userToggle = document.getElementById('userToggle');
    var userDropdown = document.getElementById('userDropdown');
    if (userToggle && userDropdown) {
        userToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            userDropdown.classList.toggle('open');
        });
        document.addEventListener('click', function() {
            userDropdown.classList.remove('open');
        });
    }

    // Chart.js defaults
    if (typeof Chart !== 'undefined') {
        Chart.defaults.font.family = 'Inter, sans-serif';
        Chart.defaults.font.size = 12;
        Chart.defaults.color = '#64748b';
    }
});
