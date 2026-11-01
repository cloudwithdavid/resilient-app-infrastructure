/* ======================================================
   CloudWithDavid — JavaScript
   ====================================================== */

(function () {
    'use strict';

    // ===========================
    // DOM References
    // ===========================
    const $ = (sel, ctx = document) => ctx.querySelector(sel);
    const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

    const navbar = $('#navbar');
    const navToggle = $('#navToggle');
    const navLinks = $('#navLinks');
    const navAnchors = $$('.nav-links a');
    const sections = $$('.page-section[id]');
    const themeToggles = $$('[data-theme-toggle]');
    const themeToggleIcons = $$('[data-theme-toggle-icon]');
    const scrollProgress = $('#scrollProgress');
    const contactForm = $('#contactForm');
    const THEME_STORAGE_KEY = 'cwd-theme';
    const STATS_ANIMATION_STORAGE_KEY = 'cwd-stats-animation-at';
    const STATS_ANIMATION_TTL_MS = 5 * 60 * 1000;
    const SKILLS_SPOTLIGHT_CLASS = 'skills-card--spotlight';
    const SKILLS_SPOTLIGHT_DURATION = 2800;
    const skillsSpotlightTimers = new WeakMap();
    const viewportHandlers = { scroll: [], resize: [] };

    function registerViewportHandler(handler, { scroll = false, resize = false, run = false } = {}) {
        if (scroll) viewportHandlers.scroll.push(handler);
        if (resize) viewportHandlers.resize.push(handler);
        if (run) handler();
    }

    Object.entries(viewportHandlers).forEach(([event, handlers]) => {
        let frameQueued = false;
        window.addEventListener(event, () => {
            if (frameQueued) return;
            frameQueued = true;
            requestAnimationFrame(() => {
                frameQueued = false;
                handlers.forEach(handler => handler());
            });
        }, { passive: true });
    });

    // ===========================
    // 1. Theme Toggle (Dark/Light)
    // ===========================
    const systemThemeMedia = window.matchMedia('(prefers-color-scheme: dark)');
    const reducedMotionMedia = window.matchMedia('(prefers-reduced-motion: reduce)');
    let themePreference = '';

    function getThemePreviewOverride() {
        const theme = new URLSearchParams(window.location.search).get('theme');
        return theme === 'dark' || theme === 'light' ? theme : '';
    }

    function getStoredTheme() {
        if (themePreference) return themePreference;
        try {
            const stored = localStorage.getItem(THEME_STORAGE_KEY);
            return stored === 'dark' || stored === 'light' ? stored : '';
        } catch {
            return '';
        }
    }

    function getSystemTheme() {
        return systemThemeMedia.matches ? 'dark' : 'light';
    }

    function updateThemeControlState(theme) {
        const nextThemeLabel = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
        themeToggles.forEach(toggle => {
            toggle.setAttribute('aria-label', nextThemeLabel);
            toggle.setAttribute('title', nextThemeLabel);
        });

        themeToggleIcons.forEach(icon => {
            const showsDarkModeSymbol = theme === 'light';
            icon.classList.toggle('fa-moon', showsDarkModeSymbol);
            icon.classList.toggle('fa-sun', !showsDarkModeSymbol);
        });
    }

    function applyTheme(theme, { persist = true } = {}) {
        const nextTheme = theme === 'light' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', nextTheme);
        document.documentElement.style.colorScheme = nextTheme;
        updateThemeControlState(nextTheme);
        if (persist) {
            themePreference = nextTheme;
            try {
                localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
            } catch {
                // Keep the preference for this visit when storage is unavailable.
            }
        }
    }

    function initTheme() {
        applyTheme(getThemePreviewOverride() || getStoredTheme() || getSystemTheme(), { persist: false });
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme') || getSystemTheme();
        applyTheme(current === 'dark' ? 'light' : 'dark');
    }

    themeToggles.forEach(toggle => toggle.addEventListener('click', toggleTheme));

    const syncWithSystemTheme = (event) => {
        if (getThemePreviewOverride()) return;
        if (getStoredTheme()) return;
        applyTheme(event.matches ? 'dark' : 'light', { persist: false });
    };

    if (typeof systemThemeMedia.addEventListener === 'function') {
        systemThemeMedia.addEventListener('change', syncWithSystemTheme);
    } else if (typeof systemThemeMedia.addListener === 'function') {
        systemThemeMedia.addListener(syncWithSystemTheme);
    }

    initTheme();

    const POSITIONING_LINE_WIDTH_RATIO = 0.99;

    function fitSingleLineToReferenceWidth(reference, line, widthProperty, fontSizeProperty, widthRatio) {
        const referenceWidth = reference.getBoundingClientRect().width * widthRatio;
        if (!referenceWidth) return;

        line.style.setProperty(widthProperty, `${referenceWidth}px`);
        line.style.removeProperty(fontSizeProperty);

        const textRange = document.createRange();
        textRange.selectNodeContents(line);
        const naturalLineWidth = textRange.getBoundingClientRect().width;
        const baseFontSize = parseFloat(window.getComputedStyle(line).fontSize);
        if (naturalLineWidth > referenceWidth && baseFontSize) {
            line.style.setProperty(fontSizeProperty, `${baseFontSize * (referenceWidth / naturalLineWidth)}px`);
        }
    }

    // ===========================
    // 1b. Hero Subtitle Width
    // ===========================
    function initHeroSubtitleWidth() {
        const heroTitleText = $('.hero-title > span');
        const heroSubtitle = $('.hero-subtitle');
        if (!heroTitleText || !heroSubtitle) return;

        function syncWidth() {
            fitSingleLineToReferenceWidth(
                heroTitleText,
                heroSubtitle,
                '--hero-title-width',
                '--hero-subtitle-font-size',
                POSITIONING_LINE_WIDTH_RATIO
            );
        }

        registerViewportHandler(syncWidth, { resize: true, run: true });
        document.fonts?.ready.then(syncWidth);
    }

    initHeroSubtitleWidth();

    // ===========================
    // 1c. Footer Positioning Width
    // ===========================
    function initFooterPositioningWidth() {
        const footerBrandText = $('.footer-brand .brand-text');
        const footerPositioning = $('.footer-positioning');
        const footerPositioningText = $('.footer-positioning-text');
        if (!footerBrandText || !footerPositioning || !footerPositioningText) return;

        function syncWidth() {
            const width = footerBrandText.getBoundingClientRect().width * POSITIONING_LINE_WIDTH_RATIO;
            if (!width) return;
            footerPositioning.style.setProperty('--footer-brand-text-width', `${width}px`);
            // Measure at the browser's actual font size, including any minimum-font setting.
            footerPositioning.style.removeProperty('--footer-positioning-scale');
            const textWidth = footerPositioningText.getBoundingClientRect().width;
            if (textWidth) {
                footerPositioning.style.setProperty('--footer-positioning-scale', Math.min(1, width / textWidth));
            }
        }

        registerViewportHandler(syncWidth, { resize: true, run: true });
        document.fonts?.ready.then(syncWidth);
    }

    initFooterPositioningWidth();

    // ===========================
    // 2. Mobile Navigation
    // ===========================
    function setMobileMenuOpen(isOpen) {
        if (!navLinks || !navToggle) return;
        navLinks.classList.toggle('active', isOpen);
        navToggle.classList.toggle('active', isOpen);
        navToggle.setAttribute('aria-expanded', String(isOpen));
    }

    function closeMobileMenu() {
        setMobileMenuOpen(false);
    }

    if (navToggle && navLinks) {
        navToggle.addEventListener('click', () => {
            setMobileMenuOpen(!navLinks.classList.contains('active'));
        });
    }

    // Close on link click
    navAnchors.forEach(link => {
        link.addEventListener('click', closeMobileMenu);
    });

    // Close on outside click
    document.addEventListener('click', (e) => {
        if (!navLinks || !navLinks.classList.contains('active')) return;
        if (!navLinks.contains(e.target) && !navToggle.contains(e.target)) {
            closeMobileMenu();
        }
    });

    // Close on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && navLinks?.classList.contains('active')) {
            closeMobileMenu();
            navToggle?.focus({ preventScroll: true });
        }
    });

    // ===========================
    // 3. Navbar Scroll Effects
    // ===========================
    function updateScrollState() {
        const scrollY = window.pageYOffset;

        // Navbar shadow
        if (navbar) {
            navbar.classList.toggle('scrolled', scrollY > 50);
        }

        // Scroll progress bar
        if (scrollProgress) {
            const docHeight = document.documentElement.scrollHeight - window.innerHeight;
            const progress = docHeight > 0
                ? Math.min(1, Math.max(0, scrollY / docHeight))
                : 0;
            scrollProgress.style.transform = `scaleX(${progress})`;
        }

    }

    registerViewportHandler(updateScrollState, { scroll: true, resize: true, run: true });

    // ===========================
    // 3c. Contact Link Scroll Activation (Mobile + Tablet)
    // ===========================
    function initContactLinkScrollActivation() {
        const contactLinks = $$('.contact-link');
        if (!contactLinks.length) return;

        const MOBILE_TABLET_MAX_WIDTH = 1024;
        const ACTIVE_BAND_CENTER_RATIO = 0.55;
        const ACTIVE_BAND_HALF_HEIGHT_RATIO = 0.06;
        const ACTIVE_BAND_MIN_HALF_HEIGHT = 32;
        const ACTIVE_BAND_MAX_HALF_HEIGHT = 50;

        function getActiveBand() {
            if (window.innerWidth > MOBILE_TABLET_MAX_WIDTH) return null;

            const center = window.innerHeight * ACTIVE_BAND_CENTER_RATIO;
            const halfHeight = Math.min(
                ACTIVE_BAND_MAX_HALF_HEIGHT,
                Math.max(ACTIVE_BAND_MIN_HALF_HEIGHT, window.innerHeight * ACTIVE_BAND_HALF_HEIGHT_RATIO)
            );
            return { topPx: center - halfHeight, bottomPx: center + halfHeight };
        }

        function update() {
            const activeBand = getActiveBand();

            contactLinks.forEach((link) => {
                if (!activeBand) {
                    link.classList.remove('contact-link--scroll-active');
                    return;
                }

                const rect = link.getBoundingClientRect();
                const shouldActivate =
                    rect.bottom > activeBand.topPx &&
                    rect.top < activeBand.bottomPx;

                link.classList.toggle('contact-link--scroll-active', shouldActivate);
            });
        }

        registerViewportHandler(update, { scroll: true, resize: true, run: true });
    }

    initContactLinkScrollActivation();

    // ===========================
    // 4. Active Nav Link Tracking
    // ===========================
    const ACTIVE_NAV_SCROLL_OFFSET = 24;
    const NAV_SCROLL_IDLE_MS = 150;
    let navScrollEndTimer = null;

    function setActiveNav(currentId) {
        navAnchors.forEach(link => {
            link.classList.toggle('active', link.getAttribute('href') === `#${currentId}`);
        });
    }

    function holdActiveNavUntilScrollStops() {
        clearTimeout(navScrollEndTimer);
        navScrollEndTimer = setTimeout(() => {
            navScrollEndTimer = null;
            updateActiveNav();
        }, NAV_SCROLL_IDLE_MS);
    }

    function updateActiveNav() {
        if (navScrollEndTimer !== null) {
            holdActiveNavUntilScrollStops();
            return;
        }

        const navHeight = navbar ? navbar.offsetHeight : 72;
        const scrollY = window.pageYOffset + navHeight + ACTIVE_NAV_SCROLL_OFFSET;

        let currentId = '';

        sections.forEach(section => {
            const top = section.getBoundingClientRect().top + window.pageYOffset;
            if (scrollY >= top) {
                currentId = section.id;
            }
        });

        setActiveNav(currentId);
    }

    registerViewportHandler(updateActiveNav, { scroll: true, resize: true, run: true });

    // ===========================
    // 5. Smooth Scroll
    // ===========================
    function getElementDocumentTop(element) {
        let top = 0;
        let current = element;

        while (current) {
            top += current.offsetTop;
            current = current.offsetParent;
        }

        return top;
    }

    const ANCHOR_SCROLL_GAP = -20;

    function scrollToAnchorTarget(target, { behavior = 'smooth', extraOffset = 0 } = {}) {
        const navHeight = navbar ? navbar.offsetHeight : 72;
        const top = getElementDocumentTop(target) - navHeight - ANCHOR_SCROLL_GAP + extraOffset;
        window.scrollTo({ top, behavior: reducedMotionMedia.matches ? 'auto' : behavior });
    }

    function getHashTarget(hash) {
        if (!hash || hash === '#') return null;
        try {
            return document.getElementById(decodeURIComponent(hash.slice(1)));
        } catch {
            return null;
        }
    }

    function scrollToHashTarget(hash, options) {
        const target = getHashTarget(hash);
        if (!target) return;

        scrollToAnchorTarget(target, options);
    }

    function runSkillsCardSpotlight(target) {
        if (!target || !target.classList.contains('skills-card')) return;

        const activeTimer = skillsSpotlightTimers.get(target);
        if (activeTimer) {
            clearTimeout(activeTimer);
        }

        target.classList.remove(SKILLS_SPOTLIGHT_CLASS);
        void target.offsetWidth;
        target.classList.add(SKILLS_SPOTLIGHT_CLASS);

        const cleanupTimer = setTimeout(() => {
            target.classList.remove(SKILLS_SPOTLIGHT_CLASS);
            skillsSpotlightTimers.delete(target);
        }, SKILLS_SPOTLIGHT_DURATION);
        skillsSpotlightTimers.set(target, cleanupTimer);
    }

    function spotlightSkillsCardWhenVisible(target) {
        if (!target || !target.classList.contains('skills-card')) return;

        if (!('IntersectionObserver' in window)) {
            setTimeout(() => runSkillsCardSpotlight(target), 650);
            return;
        }

        let didSpotlight = false;
        const observer = new IntersectionObserver((entries) => {
            const isVisible = entries.some(entry => entry.isIntersecting);
            if (!isVisible || didSpotlight) return;

            didSpotlight = true;
            observer.disconnect();
            runSkillsCardSpotlight(target);
        }, { threshold: 0.45 });

        observer.observe(target);

        setTimeout(() => {
            if (didSpotlight) return;
            didSpotlight = true;
            observer.disconnect();
            runSkillsCardSpotlight(target);
        }, 1400);
    }

    $$('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            if (e.button > 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
            const href = this.getAttribute('href');
            if (href === '#') return;
            const target = getHashTarget(href);
            if (!target) return;
            e.preventDefault();

            if (navAnchors.includes(this)) {
                setActiveNav(target.id);
                holdActiveNavUntilScrollStops();
            }

            if (this.classList.contains('floating-card') && href.startsWith('#skills')) {
                const floatingCardOffset = -40;
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        if (window.location.hash !== href) {
                            history.pushState(null, '', href);
                        }
                        scrollToAnchorTarget(target, { extraOffset: floatingCardOffset });
                        spotlightSkillsCardWhenVisible(target);
                    });
                });
                return;
            }

            if (window.location.hash !== href) {
                history.pushState(null, '', href);
            }

            scrollToAnchorTarget(target);
        });
    });

    window.addEventListener('load', () => {
        if (!window.location.hash) return;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => scrollToHashTarget(window.location.hash, { behavior: 'auto' }));
        });
    });

    window.addEventListener('hashchange', () => {
        scrollToHashTarget(window.location.hash);
    });

    // ===========================
    // 6. Credential Modal
    // ===========================
    function initCredentialModal() {
        const modal = $('#certModal');
        const modalClose = $('#certModalClose');
        const modalContent = $('#certModalContent');
        const pills = $$('.about-cert-item[role="button"][data-modal-type][data-modal-src]');
        if (!modal || !modalClose || !modalContent || !pills.length) return;

        let previousBodyOverflow = '';
        let previousFocus = null;

        function normalizeModalSrc(raw) {
            if (!raw) return '';

            let value = String(raw).trim();
            if (!value) return '';

            value = value.replace(/^['"]+|['"]+$/g, '').trim();
            if (!value || /^javascript:/i.test(value)) return '';
            return value;
        }

        const clearModalContent = () => {
            modalContent.innerHTML = '';
        };

        const closeModal = () => {
            if (!modal.classList.contains('is-open')) return;
            modal.classList.remove('is-open', 'cert-modal--image');
            modal.setAttribute('aria-hidden', 'true');
            clearModalContent();
            document.body.style.overflow = previousBodyOverflow;
            previousFocus?.focus({ preventScroll: true });
            previousFocus = null;
        };

        const openModal = (pill) => {
            const modalType = (pill.dataset.modalType || '').toLowerCase();
            const rawModalSrc = pill.dataset.modalSrc || '';
            const src = normalizeModalSrc(rawModalSrc);
            if (modalType !== 'image') return;
            if (!src) {
                console.error('Invalid modal src:', rawModalSrc);
                return;
            }

            clearModalContent();

            modal.classList.add('cert-modal--image');

            const img = document.createElement('img');
            img.src = src;
            img.alt = `${pill.textContent.trim()} credential`;
            img.loading = 'lazy';
            modalContent.appendChild(img);

            if (!modal.classList.contains('is-open')) {
                previousBodyOverflow = document.body.style.overflow;
                previousFocus = pill;
            }
            document.body.style.overflow = 'hidden';
            modal.classList.add('is-open');
            modal.setAttribute('aria-hidden', 'false');
            modalClose.focus({ preventScroll: true });
        };

        pills.forEach(pill => {
            pill.addEventListener('click', () => openModal(pill));
            pill.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter' && e.key !== ' ') return;
                e.preventDefault();
                openModal(pill);
            });
        });

        modalClose.addEventListener('click', closeModal);

        modal.addEventListener('click', (e) => {
            if (e.target === modal || modal.classList.contains('cert-modal--image')) {
                closeModal();
            }
        });

        document.addEventListener('keydown', (e) => {
            if (!modal.classList.contains('is-open')) return;
            if (e.key === 'Escape') {
                closeModal();
            } else if (e.key === 'Tab') {
                // The close button is the image preview's only interactive control.
                e.preventDefault();
                modalClose.focus({ preventScroll: true });
            }
        });
    }

    initCredentialModal();

    // ===========================
    // 7. Stats Counter Animation
    // ===========================
    function animateCounter(el, target, suffix = '') {
        const duration = 2000;
        const startTime = performance.now();

        function update(currentTime) {
            if (reducedMotionMedia.matches) {
                el.textContent = target + suffix;
                return;
            }
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease out cubic
            const ease = 1 - Math.pow(1 - progress, 3);
            const current = Math.round(target * ease);

            el.textContent = current + suffix;

            if (progress < 1) {
                requestAnimationFrame(update);
            }
        }

        requestAnimationFrame(update);
    }

    function initCounters() {
        const counters = $$('.stats-number[data-count]');
        if (!counters.length) return;
        let lastAnimationAt = 0;
        try {
            lastAnimationAt = Number(localStorage.getItem(STATS_ANIMATION_STORAGE_KEY) || 0);
        } catch (_) {}

        const animationRecentlyRan = lastAnimationAt > 0 && Date.now() - lastAnimationAt < STATS_ANIMATION_TTL_MS;
        if (reducedMotionMedia.matches || !('IntersectionObserver' in window) || animationRecentlyRan) {
            counters.forEach(el => {
                el.textContent = parseInt(el.dataset.count, 10) + (el.dataset.suffix || '');
            });
            return;
        }

        let animationWindowStarted = false;
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const el = entry.target;
                    const target = parseInt(el.dataset.count, 10);
                    const suffix = el.dataset.suffix || '';
                    if (!animationWindowStarted) {
                        animationWindowStarted = true;
                        try {
                            localStorage.setItem(STATS_ANIMATION_STORAGE_KEY, String(Date.now()));
                        } catch (_) {}
                    }
                    animateCounter(el, target, suffix);
                    observer.unobserve(el);
                }
            });
        }, { threshold: 0.5 });

        counters.forEach(el => observer.observe(el));
    }

    initCounters();

    // ===========================
    // 8. Hero Visual Parallax
    // ===========================
    function initHeroVisualParallax() {
        const hero = $('#hero');
        const about = $('#about');
        const aboutHeader = about ? $('.section-header', about) : null;
        const heroVisual = $('.hero-visual');
        const cloudWrapper = $('.hero-clouds-wrapper');
        const cardsLayer = $('.hero-cards-layer');
        if (!hero || !heroVisual || !cloudWrapper || !cardsLayer) return;

        const updateParallax = () => {
            const scrolled = reducedMotionMedia.matches ? 0 : window.pageYOffset;
            const isMobile = window.innerWidth <= 768;
            const isTablet = window.innerWidth <= 1024;
            const cloudFactor = isMobile ? 0.1 : isTablet ? 0.16 : 0.2;
            const cardsFactor = isMobile ? 0.12 : isTablet ? 0.18 : 0.2;
            const cardClampOffset = isMobile ? 96 : isTablet ? 84 : 0;
            const heroBottom = hero.offsetTop + hero.offsetHeight;
            const visualBottom = heroVisual.offsetTop + heroVisual.offsetHeight;
            const headerLimit = aboutHeader
                ? (about.offsetTop + aboutHeader.offsetTop)
                : (heroBottom + 140);
            const overlapBuffer = isMobile ? 72 : 96;
            const maxVisibleOffset = Math.max(0, headerLimit - overlapBuffer - visualBottom);
            const maxOffset = Math.max(0, maxVisibleOffset - cardClampOffset);
            const cloudOffset = Math.min(scrolled * cloudFactor, maxOffset);
            const cardsOffset = Math.min(scrolled * cardsFactor, maxOffset);
            const cloudOffsetValue = `${cloudOffset.toFixed(2)}px`;
            const cardsOffsetValue = `${cardsOffset.toFixed(2)}px`;

            cloudWrapper.style.transform =
                `translate3d(-50%, -50%, 0) translate3d(0, ${cloudOffsetValue}, 0)`;
            cardsLayer.style.transform = `translate3d(0, ${cardsOffsetValue}, 0)`;
        };

        registerViewportHandler(updateParallax, { scroll: true, resize: true, run: true });
        if (typeof reducedMotionMedia.addEventListener === 'function') {
            reducedMotionMedia.addEventListener('change', updateParallax);
        } else if (typeof reducedMotionMedia.addListener === 'function') {
            reducedMotionMedia.addListener(updateParallax);
        }
    }

    initHeroVisualParallax();

    // ===========================
    // 10. Contact Form
    // ===========================
    function initContactForm() {
        if (!contactForm) return;

        const submitBtn = contactForm.querySelector('.btn-submit');
        const formStatus = $('#formStatus', contactForm);
        const honeypotField = $('#bot-field', contactForm);
        if (!submitBtn) return;

        const originalHTML = submitBtn.innerHTML;
        const FORM_SUBMIT_COOLDOWN_MS = 15000;
        // Allow time for the body-read deadline and both server-side provider calls.
        const CONTACT_REQUEST_TIMEOUT_MS = 40000;
        const CONTACT_COOLDOWN_STORAGE_KEY = 'cwd-contact-next-submit-at';
        const EMAIL_RE = /^[^\s@]+@example\.(com|org|net)$/i;
        const CONTACT_ERROR_MESSAGE = 'The test submission could not be saved. Please try again.';
        const MESSAGE_MAX_HEIGHT_PX = 360;
        let nextContactSubmitAt = 0;
        let successResetTimer = null;
        let isSubmitting = false;

        const fields = {
            name: $('#name', contactForm),
            email: $('#email', contactForm),
            subject: $('#subject', contactForm),
            message: $('#message', contactForm)
        };
        const fieldEntries = Object.entries(fields);
        const getFieldValue = field => field ? field.value.trim() : '';

        function autoResizeTextarea(textarea) {
            if (!textarea) return;
            textarea.style.height = 'auto';
            const nextHeight = Math.min(textarea.scrollHeight, MESSAGE_MAX_HEIGHT_PX);
            textarea.style.height = `${nextHeight}px`;
            textarea.style.overflowY = textarea.scrollHeight > MESSAGE_MAX_HEIGHT_PX ? 'auto' : 'hidden';
        }

        function setFormStatus(message = '', type = 'info') {
            if (!formStatus) return;
            formStatus.textContent = message;
            formStatus.className = `form-status form-status--${type}`;
        }

        function getPersistedCooldown() {
            try {
                const value = Number(localStorage.getItem(CONTACT_COOLDOWN_STORAGE_KEY) || 0);
                return Number.isFinite(value) && value > 0 ? value : 0;
            } catch {
                return 0;
            }
        }

        function setPersistedCooldown(value) {
            try {
                if (value > 0) {
                    localStorage.setItem(CONTACT_COOLDOWN_STORAGE_KEY, String(value));
                } else {
                    localStorage.removeItem(CONTACT_COOLDOWN_STORAGE_KEY);
                }
            } catch {
                // Ignore storage failures.
            }
        }

        function clearFieldErrors() {
            fieldEntries.forEach(([, field]) => field?.removeAttribute('aria-invalid'));
        }

        function validateContactForm() {
            clearFieldErrors();
            setFormStatus('', 'info');

            let firstInvalid = null;
            for (const [name, field] of fieldEntries) {
                const value = getFieldValue(field);
                const isWithinLength = !field || field.maxLength < 0 || value.length <= field.maxLength;
                const isValid = isWithinLength && (name === 'email' ? EMAIL_RE.test(value) : Boolean(value));
                if (!isValid) {
                    field?.setAttribute('aria-invalid', 'true');
                    firstInvalid = firstInvalid || field;
                }
            }

            if (firstInvalid) {
                firstInvalid.focus();
                setFormStatus('Complete all fields and use an example.com, example.org, or example.net email address.', 'error');
                return false;
            }

            return true;
        }

        if (fields.message) {
            fields.message.addEventListener('input', () => autoResizeTextarea(fields.message));
            // Covers form-value restore/autofill on reload.
            autoResizeTextarea(fields.message);
        }

        function resetSubmitButton() {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHTML;
            submitBtn.classList.remove('btn-submit--success');
            successResetTimer = null;
        }

        contactForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (isSubmitting) return;
            if (!window.fetch) {
                setFormStatus(CONTACT_ERROR_MESSAGE, 'error');
                return;
            }

            if (!validateContactForm()) {
                return;
            }

            const now = Date.now();
            nextContactSubmitAt = Math.max(nextContactSubmitAt, getPersistedCooldown());

            if (now < nextContactSubmitAt) {
                const waitSeconds = Math.ceil((nextContactSubmitAt - now) / 1000);
                const cooldownMessage = `Please wait ${waitSeconds}s before sending another message.`;
                setFormStatus(cooldownMessage, 'error');
                return;
            }
            nextContactSubmitAt = now + FORM_SUBMIT_COOLDOWN_MS;
            setPersistedCooldown(nextContactSubmitAt);
            isSubmitting = true;
            // Send the same values that passed validation, even if edited during verification.
            const payload = {
                ...Object.fromEntries(fieldEntries.map(([name, field]) => [name, getFieldValue(field)])),
                honeypot: getFieldValue(honeypotField)
            };

            // Loading state
            if (successResetTimer) {
                clearTimeout(successResetTimer);
                successResetTimer = null;
            }
            submitBtn.classList.remove('btn-submit--success');
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span>Sending...</span><i class="fas fa-spinner fa-spin"></i>';
            contactForm.setAttribute('aria-busy', 'true');
            setFormStatus('Sending your message...', 'info');

            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), CONTACT_REQUEST_TIMEOUT_MS);
                try {
                    const response = await fetch('/api/contact', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                        signal: controller.signal
                    });
                    const result = await response.json();
                    if (!response.ok || result?.ok !== true) {
                        const error = new Error(`Contact API request failed (${response.status})`);
                        error.apiError = String(result?.error || '').trim();
                        throw error;
                    }
                } finally {
                    clearTimeout(timeoutId);
                }

                submitBtn.innerHTML = '<span>Submission saved</span><i class="fas fa-check"></i>';
                submitBtn.classList.add('btn-submit--success');
                // Preserve a new draft entered while the previous message was sending.
                if (fieldEntries.every(([name, field]) => getFieldValue(field) === payload[name])) {
                    contactForm.reset();
                }
                autoResizeTextarea(fields.message);
                clearFieldErrors();
                setFormStatus('Test submission saved to the database. No message was sent.', 'success');

                const cooldownRemaining = Math.max(0, nextContactSubmitAt - Date.now());
                successResetTimer = setTimeout(resetSubmitButton, cooldownRemaining);
            } catch (err) {
                nextContactSubmitAt = 0;
                setPersistedCooldown(0);

                setFormStatus(CONTACT_ERROR_MESSAGE, 'error');
                resetSubmitButton();
            } finally {
                isSubmitting = false;
                contactForm.removeAttribute('aria-busy');
            }
        });
    }

    initContactForm();

})();
