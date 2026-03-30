/**
 * ILUMINATY — Splash Screen Galaxy Animation
 * Lightweight Eye of God + Providence Triangle for the boot screen.
 */

(function () {
  "use strict";

  const canvas = document.getElementById("splash-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  let W, H, cx, cy, minDim, time = 0, animId = null;

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx = W / 2;
    cy = H / 2;
    minDim = Math.min(W, H);
  }

  // ─── Particles ───
  const PARTICLE_COUNT = 1200;
  const NEBULA_COUNT = 400;
  const TRI_PARTICLE_COUNT = 100;
  let particles = [], nebulas = [], triParticles = [], bgStars = [];

  function createParticle(isNebula) {
    const hue = 150 + (Math.random() - 0.5) * 40;
    return {
      angle: Math.random() * Math.PI * 2,
      orbit: isNebula ? 0.05 + Math.random() * 0.45 : 0.02 + Math.random() * 0.5,
      speed: (0.2 + Math.random() * 0.8) * (isNebula ? 0.3 : 1),
      size: isNebula ? 1.5 + Math.random() * 4 : 0.5 + Math.random() * 2.2,
      hue, sat: 70 + Math.random() * 30,
      light: isNebula ? 30 + Math.random() * 30 : 50 + Math.random() * 40,
      alpha: isNebula ? 0.03 + Math.random() * 0.12 : 0.3 + Math.random() * 0.7,
      phase: Math.random() * Math.PI * 2,
      ecc: 0.3 + Math.random() * 0.7,
      tilt: (Math.random() - 0.5) * 0.6,
      z: Math.random(),
      isNebula,
    };
  }

  function initAll() {
    particles = []; nebulas = []; triParticles = []; bgStars = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) particles.push(createParticle(false));
    for (let i = 0; i < NEBULA_COUNT; i++) nebulas.push(createParticle(true));
    for (let i = 0; i < TRI_PARTICLE_COUNT; i++) {
      triParticles.push({
        edge: Math.floor(Math.random() * 3), t: Math.random(),
        speed: 0.0002 + Math.random() * 0.0004,
        size: 0.5 + Math.random() * 2,
        alpha: 0.3 + Math.random() * 0.7,
        hue: 150 + (Math.random() - 0.5) * 30,
        offset: (Math.random() - 0.5) * 8,
        phase: Math.random() * Math.PI * 2,
      });
    }
    for (let i = 0; i < 150; i++) {
      bgStars.push({
        x: Math.random() * W, y: Math.random() * H,
        size: 0.3 + Math.random() * 1.2,
        alpha: 0.2 + Math.random() * 0.5,
        speed: 0.001 + Math.random() * 0.003,
        phase: Math.random() * Math.PI * 2,
      });
    }
  }

  // ─── Eye shape ───
  function eyePos(angle, radius) {
    const cos = Math.cos(angle), sin = Math.sin(angle);
    const taper = Math.pow(Math.abs(cos), 0.4);
    return {
      x: cx + cos * radius * 2.8 * minDim * 0.3,
      y: cy + sin * radius * taper * minDim * 0.3,
    };
  }

  // ─── Triangle ───
  function getTriVerts() {
    const pulse = 1 + 0.02 * Math.sin(time * 0.0012);
    const R = minDim * 0.38 * pulse;
    const tcy = cy + R * 0.08;
    return [
      { x: cx, y: tcy - R },
      { x: cx - R * Math.sin(Math.PI / 3), y: tcy + R * 0.5 },
      { x: cx + R * Math.sin(Math.PI / 3), y: tcy + R * 0.5 },
    ];
  }

  function lerp(a, b, t) { return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }; }

  // ─── Render ───
  function drawParticle(p, dt) {
    p.angle += p.speed * 0.0003 * dt;
    const pulse = 1 + 0.1 * Math.sin(time * 0.0008 + p.phase);
    const pos = eyePos(p.angle, p.orbit * p.ecc * pulse);
    pos.y += Math.sin(p.angle * 2 + p.phase) * p.tilt * minDim * 0.04;
    const depth = 0.5 + p.z * 0.5;
    const sz = p.size * depth * pulse;
    const twinkle = 0.7 + 0.3 * Math.sin(time * 0.002 + p.phase * 10);
    const a = p.alpha * twinkle * depth;

    if (p.isNebula) {
      const g = ctx.createRadialGradient(pos.x, pos.y, 0, pos.x, pos.y, sz * 3);
      g.addColorStop(0, `hsla(${p.hue},${p.sat}%,${p.light}%,${a})`);
      g.addColorStop(1, `hsla(${p.hue},${p.sat}%,${p.light}%,0)`);
      ctx.beginPath(); ctx.arc(pos.x, pos.y, sz * 3, 0, Math.PI * 2);
      ctx.fillStyle = g; ctx.fill();
    } else {
      ctx.beginPath(); ctx.arc(pos.x, pos.y, sz, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${p.hue},${p.sat}%,${p.light}%,${a})`; ctx.fill();
      if (p.alpha > 0.7 && sz > 1.5) {
        ctx.beginPath(); ctx.arc(pos.x, pos.y, sz * 2.5, 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${p.hue},100%,80%,${a * 0.15})`; ctx.fill();
      }
    }
  }

  function drawIris() {
    const iR = minDim * 0.1, pR = minDim * 0.035;
    const pulse = 1 + 0.08 * Math.sin(time * 0.0024);
    // Glow
    const g1 = ctx.createRadialGradient(cx, cy, pR * 0.5, cx, cy, iR * 2.5 * pulse);
    g1.addColorStop(0, "rgba(0,255,136,0.35)"); g1.addColorStop(0.2, "rgba(0,255,136,0.15)");
    g1.addColorStop(0.5, "rgba(0,200,100,0.05)"); g1.addColorStop(1, "rgba(0,200,100,0)");
    ctx.beginPath(); ctx.arc(cx, cy, iR * 2.5 * pulse, 0, Math.PI * 2); ctx.fillStyle = g1; ctx.fill();
    // Ring
    const g2 = ctx.createRadialGradient(cx, cy, iR * 0.6, cx, cy, iR * 1.2);
    g2.addColorStop(0, "rgba(0,255,136,0)"); g2.addColorStop(0.5, "rgba(0,255,136,0.12)");
    g2.addColorStop(0.8, "rgba(0,255,136,0.25)"); g2.addColorStop(1, "rgba(0,255,136,0)");
    ctx.beginPath(); ctx.arc(cx, cy, iR, 0, Math.PI * 2); ctx.fillStyle = g2; ctx.fill();
    // Pupil
    const g3 = ctx.createRadialGradient(cx, cy, 0, cx, cy, pR * 1.5);
    g3.addColorStop(0, "rgba(5,5,10,0.95)"); g3.addColorStop(0.6, "rgba(5,5,10,0.7)"); g3.addColorStop(1, "rgba(5,5,10,0)");
    ctx.beginPath(); ctx.arc(cx, cy, pR * 1.5, 0, Math.PI * 2); ctx.fillStyle = g3; ctx.fill();
  }

  function drawTriangle() {
    const v = getTriVerts();
    ctx.save(); ctx.globalCompositeOperation = "lighter";
    // Aura
    for (let l = 3; l >= 0; l--) {
      const s = 1 + l * 0.06;
      const sv = getTriVerts(); // approx
      const a = Math.max(0.015 - l * 0.003, 0.003);
      ctx.beginPath(); ctx.moveTo(cx + (sv[0].x - cx) * s, cy + (sv[0].y - cy) * s);
      ctx.lineTo(cx + (sv[1].x - cx) * s, cy + (sv[1].y - cy) * s);
      ctx.lineTo(cx + (sv[2].x - cx) * s, cy + (sv[2].y - cy) * s);
      ctx.closePath(); ctx.strokeStyle = `rgba(0,255,136,${a})`; ctx.lineWidth = 8 + l * 6; ctx.stroke();
    }
    // Main lines
    const ga = 0.12 + 0.06 * Math.sin(time * 0.0016);
    ctx.beginPath(); ctx.moveTo(v[0].x, v[0].y); ctx.lineTo(v[1].x, v[1].y); ctx.lineTo(v[2].x, v[2].y); ctx.closePath();
    ctx.strokeStyle = `rgba(0,255,136,${ga})`; ctx.lineWidth = 6; ctx.shadowColor = "rgba(0,255,136,0.5)"; ctx.shadowBlur = 20; ctx.stroke(); ctx.shadowBlur = 0;
    const la = 0.25 + 0.1 * Math.sin(time * 0.0016);
    ctx.beginPath(); ctx.moveTo(v[0].x, v[0].y); ctx.lineTo(v[1].x, v[1].y); ctx.lineTo(v[2].x, v[2].y); ctx.closePath();
    ctx.strokeStyle = `rgba(0,255,136,${la})`; ctx.lineWidth = 1.5; ctx.stroke();

    // Edge particles
    const edges = [[v[0], v[1]], [v[1], v[2]], [v[2], v[0]]];
    for (const tp of triParticles) {
      tp.t += tp.speed * 16;
      if (tp.t > 1) { tp.t -= 1; tp.edge = (tp.edge + 1) % 3; }
      const [a, b] = edges[tp.edge];
      const pos = lerp(a, b, tp.t);
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.sqrt(dx * dx + dy * dy) || 1;
      const wobble = tp.offset + Math.sin(time * 0.002 + tp.phase) * 3;
      pos.x += (-dy / len) * wobble; pos.y += (dx / len) * wobble;
      const tw = 0.6 + 0.4 * Math.sin(time * 0.003 + tp.phase * 7);
      const al = tp.alpha * tw;
      ctx.beginPath(); ctx.arc(pos.x, pos.y, tp.size * 2.5, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${tp.hue},100%,70%,${al * 0.15})`; ctx.fill();
      ctx.beginPath(); ctx.arc(pos.x, pos.y, tp.size, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${tp.hue},90%,65%,${al})`; ctx.fill();
    }

    // Vertex flares
    for (let i = 0; i < 3; i++) {
      const p = v[i], fp = 0.7 + 0.3 * Math.sin(time * 0.001 + i * 2.094);
      const fr = minDim * 0.02 * fp;
      const fg = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, fr * 3);
      fg.addColorStop(0, `rgba(0,255,136,${0.3 * fp})`); fg.addColorStop(0.4, `rgba(0,255,136,${0.1 * fp})`); fg.addColorStop(1, "rgba(0,255,136,0)");
      ctx.beginPath(); ctx.arc(p.x, p.y, fr * 3, 0, Math.PI * 2); ctx.fillStyle = fg; ctx.fill();
    }
    ctx.restore();
  }

  // ─── Frame ───
  let lastT = 0;
  function frame(ts) {
    const dt = lastT ? Math.min(ts - lastT, 50) : 16;
    lastT = ts; time += dt;

    ctx.fillStyle = "rgba(10,10,18,0.08)";
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = "lighter";

    // BG stars
    for (const s of bgStars) {
      const a = s.alpha * (0.5 + 0.5 * Math.sin(time * s.speed + s.phase));
      ctx.beginPath(); ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(200,220,255,${a})`; ctx.fill();
    }

    for (const p of nebulas) drawParticle(p, dt);
    for (const p of particles) drawParticle(p, dt);

    ctx.globalCompositeOperation = "source-over";
    drawTriangle();
    drawIris();

    animId = requestAnimationFrame(frame);
  }

  // ─── Init ───
  resize();
  initAll();
  ctx.fillStyle = "rgba(10,10,18,1)";
  ctx.fillRect(0, 0, W, H);
  const resizeHandler = () => { resize(); initAll(); };
  window.addEventListener("resize", resizeHandler);
  animId = requestAnimationFrame(frame);

  const observer = new MutationObserver(() => {
    const splash = document.getElementById("splash");
    if (splash && splash.classList.contains("hidden")) {
      if (animId) cancelAnimationFrame(animId);
      animId = null;
      // S1: Free animation memory
      particles = [];
      nebulas = [];
      triParticles = [];
      bgStars = [];
      canvas.width = 0;
      canvas.height = 0;
      window.removeEventListener("resize", resizeHandler);
      observer.disconnect();
    }
  });
  observer.observe(document.getElementById("splash"), { attributes: true, attributeFilter: ["class"] });
})();
