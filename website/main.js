/**
 * ILUMINATY — Landing Page Interactions
 * ======================================
 * Scroll reveal, stat counters, code tabs, nav behavior.
 */

(function () {
  "use strict";

  // ─── Nav scroll state ───
  const nav = document.getElementById("nav");
  let lastScroll = 0;

  window.addEventListener("scroll", () => {
    const y = window.scrollY;
    if (y > 80) {
      nav.classList.add("scrolled");
    } else {
      nav.classList.remove("scrolled");
    }
    // Hide nav on scroll down, show on scroll up
    if (y > lastScroll && y > 400) {
      nav.style.transform = "translateY(-100%)";
    } else {
      nav.style.transform = "translateY(0)";
    }
    lastScroll = y;
  }, { passive: true });

  // ─── Scroll Reveal (IntersectionObserver) ───
  const revealElements = document.querySelectorAll(
    ".section-badge, .problem-card, .flow-step, .layer, " +
    ".feature-card, .compare-table, .code-tabs, .start-card, " +
    "h2, .hero-stats, .hero-actions"
  );

  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("revealed");
        revealObserver.unobserve(entry.target);
        // Stagger children if it's a grid/row container
        const children = entry.target.parentElement?.querySelectorAll(
          ".problem-card, .feature-card, .start-card, .flow-step"
        );
        if (children && children.length > 1) {
          children.forEach((child, i) => {
            child.style.transitionDelay = `${i * 0.1}s`;
          });
        }
      }
    });
  }, {
    threshold: 0.15,
    rootMargin: "0px 0px -60px 0px",
  });

  revealElements.forEach((el) => {
    el.classList.add("reveal-hidden");
    revealObserver.observe(el);
  });

  // ─── Stat Counter Animation ───
  const statNums = document.querySelectorAll(".stat-num[data-target]");
  let statsCounted = false;

  const statsObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting && !statsCounted) {
        statsCounted = true;
        animateStats();
        statsObserver.disconnect();
      }
    });
  }, { threshold: 0.5 });

  const heroStats = document.querySelector(".hero-stats");
  if (heroStats) statsObserver.observe(heroStats);

  function animateStats() {
    statNums.forEach((el) => {
      const target = parseInt(el.dataset.target, 10);
      const duration = 2000;
      const start = performance.now();

      function tick(now) {
        const elapsed = now - start;
        const progress = Math.min(elapsed / duration, 1);
        // Ease out cubic
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = Math.round(target * eased);
        el.textContent = current;
        if (progress < 1) requestAnimationFrame(tick);
      }

      requestAnimationFrame(tick);
    });
  }

  // ─── Code Tabs ───
  const tabBtns = document.querySelectorAll(".code-tab");
  const tabPanels = document.querySelectorAll(".code-panel");

  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;

      tabBtns.forEach((b) => b.classList.remove("active"));
      tabPanels.forEach((p) => p.classList.remove("active"));

      btn.classList.add("active");
      const panel = document.querySelector(`.code-panel[data-panel="${target}"]`);
      if (panel) panel.classList.add("active");
    });
  });

  // ─── Smooth scroll for anchor links ───
  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", (e) => {
      const target = document.querySelector(link.getAttribute("href"));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });

  // ─── Layer hover glow ───
  document.querySelectorAll(".layer").forEach((row) => {
    row.addEventListener("mouseenter", () => {
      row.style.borderColor = "rgba(0, 255, 136, 0.4)";
    });
    row.addEventListener("mouseleave", () => {
      row.style.borderColor = "";
    });
  });

  // ─── Parallax on hero elements ───
  const heroContent = document.querySelector(".hero-content");

  window.addEventListener("scroll", () => {
    const y = window.scrollY;
    if (y < window.innerHeight && heroContent) {
      heroContent.style.transform = `translateY(${y * 0.3}px)`;
      heroContent.style.opacity = 1 - y / (window.innerHeight * 0.8);
    }
  }, { passive: true });

  // ─── Hamburger menu toggle ───
  const hamburger = document.querySelector(".nav-hamburger");
  const navLinks = document.querySelector(".nav-links");

  if (hamburger && navLinks) {
    hamburger.addEventListener("click", () => {
      const isOpen = navLinks.classList.toggle("open");
      hamburger.classList.toggle("active");
      hamburger.setAttribute("aria-expanded", isOpen);
    });
    // Close menu when a nav link is clicked
    navLinks.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        navLinks.classList.remove("open");
        hamburger.classList.remove("active");
        hamburger.setAttribute("aria-expanded", "false");
      });
    });
  }

  // ─── OS Detection + Download highlight ───
  const platform = navigator.platform?.toLowerCase() || "";
  const ua = navigator.userAgent?.toLowerCase() || "";
  let detectedOS = "windows";
  if (platform.includes("mac") || ua.includes("macintosh")) detectedOS = "mac";
  else if (platform.includes("linux") || ua.includes("linux")) detectedOS = "linux";

  const detectedCard = document.querySelector(`.download-card[data-os="${detectedOS}"]`);
  if (detectedCard) detectedCard.classList.add("detected");

  // Download buttons — TODO: Replace # with GitHub Releases URLs
  document.querySelectorAll(".download-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const href = btn.getAttribute("href");
      if (href === "#" || !href) {
        e.preventDefault();
        // TODO: Replace with actual download URLs from GitHub Releases
        // e.g. https://github.com/sgodoy90/iluminaty/releases/latest/download/ILUMINATY-Setup.exe
        alert("Downloads will be available after the first release build. Run 'npm run tauri build' to generate installers.");
      }
    });
  });

  // ─── Auth Modal ───
  const authModal = document.getElementById("auth-modal");
  const modalClose = document.getElementById("modal-close");
  const navSignIn = document.getElementById("nav-sign-in");
  const navUser = document.getElementById("nav-user");
  const navUserBtn = document.getElementById("nav-user-btn");
  const userDropdown = document.getElementById("user-dropdown");
  const authForm = document.getElementById("auth-form");
  const authSubmit = document.getElementById("auth-submit");
  const authError = document.getElementById("auth-error");
  const modalTitle = document.getElementById("modal-title");
  const authToggleText = document.getElementById("auth-toggle-text");
  const authToggleLink = document.getElementById("auth-toggle-link");
  const formNameGroup = document.getElementById("form-name-group");
  const btnLabel = authSubmit?.querySelector(".btn-label");
  const btnGoogleLogin = document.getElementById("btn-google-login");

  let isSignUp = false;

  function openModal() {
    if (!authModal) return;
    authModal.classList.add("active");
    authModal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
  }

  function closeModal() {
    if (!authModal) return;
    authModal.classList.remove("active");
    authModal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    clearError();
  }

  function showError(msg) {
    if (!authError) return;
    authError.textContent = msg;
    authError.classList.add("visible");
  }

  function clearError() {
    if (!authError) return;
    authError.textContent = "";
    authError.classList.remove("visible");
  }

  function setLoading(loading) {
    if (!authSubmit) return;
    authSubmit.disabled = loading;
    authSubmit.classList.toggle("loading", loading);
  }

  function toggleSignUp() {
    isSignUp = !isSignUp;
    if (modalTitle) modalTitle.textContent = isSignUp ? "Create your account" : "Sign in to ILUMINATY";
    if (formNameGroup) formNameGroup.style.display = isSignUp ? "flex" : "none";
    if (btnLabel) btnLabel.textContent = isSignUp ? "Create Account" : "Sign In";
    if (authToggleText) authToggleText.textContent = isSignUp ? "Already have an account?" : "Don't have an account?";
    if (authToggleLink) authToggleLink.textContent = isSignUp ? "Sign in" : "Sign up";
    clearError();
  }

  // Auth API
  const AUTH_API = "https://api.iluminaty.dev";
  // const AUTH_API = "http://localhost:8787"; // dev

  // Get/set user in localStorage
  function getUser() {
    try { return JSON.parse(localStorage.getItem("iluminaty_user")); } catch { return null; }
  }

  function getSession() {
    try { return JSON.parse(localStorage.getItem("iluminaty_session")); } catch { return null; }
  }

  function setUser(user) {
    localStorage.setItem("iluminaty_user", JSON.stringify(user));
    renderUserState();
  }

  function setSession(data) {
    localStorage.setItem("iluminaty_session", JSON.stringify(data));
    setUser(data.user);
  }

  function removeUser() {
    localStorage.removeItem("iluminaty_user");
    localStorage.removeItem("iluminaty_session");
    renderUserState();
  }

  function renderUserState() {
    const user = getUser();
    if (user) {
      // Logged in — show avatar, hide Sign In
      if (navSignIn) navSignIn.style.display = "none";
      if (navUser) {
        navUser.style.display = "block";
        const avatar = document.getElementById("user-avatar");
        const name = document.getElementById("user-name");
        const email = document.getElementById("dropdown-email");
        const plan = document.getElementById("dropdown-plan");
        const upgrade = document.getElementById("dropdown-upgrade");

        if (avatar) avatar.textContent = (user.name || user.email || "U").charAt(0).toUpperCase();
        if (name) name.textContent = user.name || user.email.split("@")[0];
        if (email) email.textContent = user.email;
        if (plan) plan.textContent = (user.plan || "free") === "pro" ? "Pro Plan" : "Free Plan";
        if (upgrade) upgrade.style.display = (user.plan || "free") === "pro" ? "none" : "flex";
      }
    } else {
      // Logged out — show Sign In, hide avatar
      if (navSignIn) navSignIn.style.display = "";
      if (navUser) navUser.style.display = "none";
    }
  }

  // Open modal
  if (navSignIn) {
    navSignIn.addEventListener("click", (e) => {
      e.preventDefault();
      isSignUp = false;
      toggleSignUp(); // reset to sign-in state
      toggleSignUp(); // toggle back (this resets properly)
      isSignUp = false;
      if (modalTitle) modalTitle.textContent = "Sign in to ILUMINATY";
      if (formNameGroup) formNameGroup.style.display = "none";
      if (btnLabel) btnLabel.textContent = "Sign In";
      if (authToggleText) authToggleText.textContent = "Don't have an account?";
      if (authToggleLink) authToggleLink.textContent = "Sign up";
      openModal();
    });
  }

  // Close modal
  if (modalClose) modalClose.addEventListener("click", closeModal);
  if (authModal) {
    authModal.addEventListener("click", (e) => {
      if (e.target === authModal) closeModal();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });

  // Toggle sign in / sign up
  if (authToggleLink) {
    authToggleLink.addEventListener("click", (e) => {
      e.preventDefault();
      toggleSignUp();
    });
  }

  // Form submit (email/password)
  if (authForm) {
    authForm.addEventListener("submit", (e) => {
      e.preventDefault();
      clearError();

      const email = document.getElementById("auth-email")?.value.trim();
      const password = document.getElementById("auth-password")?.value;
      const name = document.getElementById("auth-name")?.value.trim();

      if (!email || !password) { showError("Please fill in all fields."); return; }
      if (isSignUp && !name) { showError("Please enter your name."); return; }
      if (password.length < 6) { showError("Password must be at least 6 characters."); return; }

      setLoading(true);

      (async () => {
        try {
          const endpoint = isSignUp ? "/auth/register" : "/auth/login";
          const body = isSignUp ? { email, password, name } : { email, password };
          const resp = await fetch(AUTH_API + endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          const data = await resp.json();
          if (!resp.ok) {
            showError(data.error || "Something went wrong");
            return;
          }
          setSession(data);
          closeModal();
          authForm.reset();
          // Redirect to dashboard if they just signed up
          if (isSignUp) {
            window.location.href = "/dashboard.html";
          }
        } catch (err) {
          showError("Could not connect to server. Try again later.");
        } finally {
          setLoading(false);
        }
      })();
    });
  }

  // Google login button
  if (btnGoogleLogin) {
    btnGoogleLogin.addEventListener("click", () => {
      const clientId = btnGoogleLogin.dataset.googleClient;
      if (!clientId) {
        showError("Google OAuth requires a Client ID. Use email/password for now.");
        return;
      }
      // Real Google OAuth — redirect to Google consent screen
      const redirectUri = encodeURIComponent(window.location.origin + "/dashboard.html");
      window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?client_id=${clientId}&redirect_uri=${redirectUri}&response_type=token&scope=email%20profile`;
    });
  }

  // User dropdown toggle
  if (navUserBtn) {
    navUserBtn.addEventListener("click", () => {
      const open = userDropdown.classList.toggle("open");
      navUserBtn.setAttribute("aria-expanded", open);
    });
    // Close dropdown on outside click
    document.addEventListener("click", (e) => {
      if (!navUser.contains(e.target)) {
        userDropdown.classList.remove("open");
        navUserBtn.setAttribute("aria-expanded", "false");
      }
    });
  }

  // Logout
  const logoutBtn = document.getElementById("dropdown-logout");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", () => {
      removeUser();
      userDropdown.classList.remove("open");
    });
  }

  // Pricing "Subscribe" button opens modal if not logged in
  document.querySelectorAll(".pricing-card .btn-primary").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const href = btn.getAttribute("href");
      if (href === "#" || !href) {
        e.preventDefault();
        const user = getUser();
        if (!user) {
          openModal();
        } else {
          // TODO: redirect to Lemon Squeezy checkout with user email prefilled
          // window.location.href = `https://iluminaty.lemonsqueezy.com/checkout?checkout[email]=${encodeURIComponent(user.email)}`;
          showError("Payment integration coming soon. Your account is ready!");
          openModal();
        }
      }
    });
  });

  // Init: render user state on page load
  renderUserState();

})();
