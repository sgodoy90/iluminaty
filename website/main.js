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
    ".section-badge, .problem-card, .solution-step, .layer-row, " +
    ".feature-card, .compare-table, .code-tabs, .start-card, " +
    "h2, .hero-stats, .hero-actions"
  );

  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("revealed");
        // Stagger children if it's a grid/row container
        const children = entry.target.parentElement?.querySelectorAll(
          ".problem-card, .feature-card, .start-card, .solution-step"
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

  // ─── Layer rows hover glow ───
  document.querySelectorAll(".layer-row").forEach((row) => {
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

  // ─── CSS for reveal animations (inject once) ───
  const style = document.createElement("style");
  style.textContent = `
    .reveal-hidden {
      opacity: 0;
      transform: translateY(30px);
      transition: opacity 0.6s ease, transform 0.6s ease;
    }
    .reveal-hidden.revealed {
      opacity: 1;
      transform: translateY(0);
    }
  `;
  document.head.appendChild(style);

})();
