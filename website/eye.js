/**
 * ILUMINATY — Eye of God Galaxy Animation
 * ========================================
 * A cosmic nebula / galaxy animation that forms an eye shape.
 * Green (#00ff88) particle system with depth, rotation, and glow.
 * Renders on <canvas id="eye-canvas"> in the hero section.
 */

(function () {
  "use strict";

  const canvas = document.getElementById("eye-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  // ─── Config ───
  const CFG = {
    particleCount: 1800,
    nebulaLayers: 5,
    baseHue: 150,          // green
    accentHue: 170,        // teal-green
    bgColor: "rgba(10, 10, 18, 1)",
    mouseInfluence: 0.00015,
    rotationSpeed: 0.0003,
    pulseSpeed: 0.0008,
    eyeAspect: 2.8,        // horizontal stretch for the eye shape
    irisRadius: 0.12,      // relative to canvas size
    pupilRadius: 0.04,
    trailAlpha: 0.08,
  };

  let W, H, cx, cy, minDim;
  let mouse = { x: 0, y: 0, active: false };
  let time = 0;
  let particles = [];
  let nebulaParticles = [];

  // ─── Resize ───
  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.parentElement.getBoundingClientRect();
    W = rect.width;
    H = rect.height;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx = W / 2;
    cy = H / 2;
    minDim = Math.min(W, H);
  }

  // ─── Particle classes ───

  function createParticle(isNebula) {
    const angle = Math.random() * Math.PI * 2;
    const orbitRadius = isNebula
      ? 0.05 + Math.random() * 0.45
      : 0.02 + Math.random() * 0.5;
    const speed = (0.2 + Math.random() * 0.8) * (isNebula ? 0.3 : 1);
    const size = isNebula
      ? 1.5 + Math.random() * 4
      : 0.5 + Math.random() * 2.2;
    const layer = Math.floor(Math.random() * CFG.nebulaLayers);
    const hue = CFG.baseHue + (Math.random() - 0.5) * 40;
    const sat = 70 + Math.random() * 30;
    const light = isNebula ? 30 + Math.random() * 30 : 50 + Math.random() * 40;
    const alpha = isNebula ? 0.03 + Math.random() * 0.12 : 0.3 + Math.random() * 0.7;

    return {
      angle,
      orbitRadius,
      speed,
      size,
      layer,
      hue,
      sat,
      light,
      alpha,
      baseAlpha: alpha,
      isNebula,
      phase: Math.random() * Math.PI * 2,
      eccentricity: 0.3 + Math.random() * 0.7,
      tilt: (Math.random() - 0.5) * 0.6,
      z: Math.random(),
    };
  }

  function initParticles() {
    particles = [];
    nebulaParticles = [];
    const pCount = W < 600 ? CFG.particleCount * 0.4 : CFG.particleCount;
    for (let i = 0; i < pCount; i++) {
      particles.push(createParticle(false));
    }
    const nCount = W < 600 ? 300 : 600;
    for (let i = 0; i < nCount; i++) {
      nebulaParticles.push(createParticle(true));
    }
  }

  // ─── Compute eye shape ───
  // An eye/vesica shape: intersection of two circles
  function eyeEnvelope(angle, radius) {
    // Create an almond/eye shape using cos modulation
    const cos2 = Math.cos(angle);
    const sin2 = Math.sin(angle);
    // Horizontal stretch, vertical squeeze
    const xScale = CFG.eyeAspect;
    const yScale = 1.0;
    // Eye taper: narrow at left/right, wide in center
    const taper = Math.pow(Math.abs(Math.cos(angle)), 0.4);
    return {
      x: cx + cos2 * radius * xScale * minDim * 0.35,
      y: cy + sin2 * radius * yScale * taper * minDim * 0.35,
    };
  }

  // ─── Draw iris glow ───
  function drawIris() {
    const irisR = CFG.irisRadius * minDim;
    const pupilR = CFG.pupilRadius * minDim;

    // Outer iris glow
    const pulse = 1 + 0.08 * Math.sin(time * CFG.pulseSpeed * 3);
    const grad = ctx.createRadialGradient(cx, cy, pupilR * 0.5, cx, cy, irisR * 2.5 * pulse);
    grad.addColorStop(0, "rgba(0, 255, 136, 0.35)");
    grad.addColorStop(0.2, "rgba(0, 255, 136, 0.15)");
    grad.addColorStop(0.5, "rgba(0, 200, 100, 0.05)");
    grad.addColorStop(1, "rgba(0, 200, 100, 0)");

    ctx.beginPath();
    ctx.arc(cx, cy, irisR * 2.5 * pulse, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();

    // Iris ring
    const ringGrad = ctx.createRadialGradient(cx, cy, irisR * 0.6, cx, cy, irisR * 1.2);
    ringGrad.addColorStop(0, "rgba(0, 255, 136, 0)");
    ringGrad.addColorStop(0.5, "rgba(0, 255, 136, 0.12)");
    ringGrad.addColorStop(0.8, "rgba(0, 255, 136, 0.25)");
    ringGrad.addColorStop(1, "rgba(0, 255, 136, 0)");

    ctx.beginPath();
    ctx.arc(cx, cy, irisR, 0, Math.PI * 2);
    ctx.fillStyle = ringGrad;
    ctx.fill();

    // Pupil — dark center
    const pupilGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, pupilR * 1.5);
    pupilGrad.addColorStop(0, "rgba(5, 5, 10, 0.95)");
    pupilGrad.addColorStop(0.6, "rgba(5, 5, 10, 0.7)");
    pupilGrad.addColorStop(1, "rgba(5, 5, 10, 0)");

    ctx.beginPath();
    ctx.arc(cx, cy, pupilR * 1.5, 0, Math.PI * 2);
    ctx.fillStyle = pupilGrad;
    ctx.fill();

    // Pupil highlight (catchlight)
    const hlX = cx - pupilR * 0.3;
    const hlY = cy - pupilR * 0.3;
    const hlGrad = ctx.createRadialGradient(hlX, hlY, 0, hlX, hlY, pupilR * 0.4);
    hlGrad.addColorStop(0, "rgba(255, 255, 255, 0.4)");
    hlGrad.addColorStop(1, "rgba(255, 255, 255, 0)");

    ctx.beginPath();
    ctx.arc(hlX, hlY, pupilR * 0.4, 0, Math.PI * 2);
    ctx.fillStyle = hlGrad;
    ctx.fill();
  }

  // ─── Draw eye contour lines ───
  function drawEyeContour() {
    const pulse = 1 + 0.03 * Math.sin(time * CFG.pulseSpeed * 2);
    const R = minDim * 0.35 * pulse;

    ctx.save();
    ctx.strokeStyle = "rgba(0, 255, 136, 0.08)";
    ctx.lineWidth = 1.5;

    // Upper lid
    ctx.beginPath();
    for (let t = -1; t <= 1; t += 0.01) {
      const x = cx + t * R * CFG.eyeAspect * 0.5;
      const lidY = -Math.sqrt(Math.max(0, 1 - t * t)) * R * 0.5;
      if (t === -1) ctx.moveTo(x, cy + lidY);
      else ctx.lineTo(x, cy + lidY);
    }
    ctx.stroke();

    // Lower lid
    ctx.beginPath();
    for (let t = -1; t <= 1; t += 0.01) {
      const x = cx + t * R * CFG.eyeAspect * 0.5;
      const lidY = Math.sqrt(Math.max(0, 1 - t * t)) * R * 0.5;
      if (t === -1) ctx.moveTo(x, cy + lidY);
      else ctx.lineTo(x, cy + lidY);
    }
    ctx.stroke();

    ctx.restore();
  }

  // ─── Draw spiral arms ───
  function drawSpiralArms() {
    const arms = 2;
    const armPoints = 120;

    ctx.save();
    ctx.globalCompositeOperation = "lighter";

    for (let a = 0; a < arms; a++) {
      const armOffset = (a / arms) * Math.PI * 2;

      for (let i = 0; i < armPoints; i++) {
        const t = i / armPoints;
        const spiralAngle = armOffset + t * Math.PI * 3 + time * CFG.rotationSpeed;
        const spiralRadius = t * 0.45;

        const pos = eyeEnvelope(spiralAngle, spiralRadius);

        const alpha = (1 - t) * 0.15 * (0.5 + 0.5 * Math.sin(time * 0.001 + i * 0.1));
        const size = (1 - t * 0.5) * 3;

        ctx.beginPath();
        ctx.arc(pos.x, pos.y, size, 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${CFG.baseHue + t * 20}, 90%, ${50 + t * 20}%, ${alpha})`;
        ctx.fill();
      }
    }

    ctx.restore();
  }

  // ─── Render particle ───
  function renderParticle(p, dt) {
    p.angle += p.speed * CFG.rotationSpeed * dt * (1 + p.layer * 0.1);

    // Mouse influence
    if (mouse.active) {
      const dx = (mouse.x - cx) * CFG.mouseInfluence;
      const dy = (mouse.y - cy) * CFG.mouseInfluence;
      p.angle += dx * 0.01;
      p.tilt += dy * 0.001;
    }

    // Pulsing
    const pulse = 1 + 0.1 * Math.sin(time * CFG.pulseSpeed + p.phase);
    const radius = p.orbitRadius * pulse;

    // Get position on eye shape
    const pos = eyeEnvelope(p.angle, radius * p.eccentricity);

    // Add some tilt/wobble
    pos.y += Math.sin(p.angle * 2 + p.phase) * p.tilt * minDim * 0.05;

    // Depth-based scaling
    const depthFactor = 0.5 + p.z * 0.5;
    const size = p.size * depthFactor * pulse;

    // Twinkle
    const twinkle = 0.7 + 0.3 * Math.sin(time * 0.002 + p.phase * 10);
    const alpha = p.baseAlpha * twinkle * depthFactor;

    if (p.isNebula) {
      // Nebula: soft large blobs
      const grad = ctx.createRadialGradient(pos.x, pos.y, 0, pos.x, pos.y, size * 3);
      grad.addColorStop(0, `hsla(${p.hue}, ${p.sat}%, ${p.light}%, ${alpha})`);
      grad.addColorStop(1, `hsla(${p.hue}, ${p.sat}%, ${p.light}%, 0)`);
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, size * 3, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    } else {
      // Star particle
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, size, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${p.hue}, ${p.sat}%, ${p.light}%, ${alpha})`;
      ctx.fill();

      // Bright stars get a glow
      if (p.baseAlpha > 0.7 && size > 1.5) {
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, size * 2.5, 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${p.hue}, 100%, 80%, ${alpha * 0.15})`;
        ctx.fill();
      }
    }
  }

  // ─── Background stars (fixed, very far) ───
  let bgStars = [];
  function initBgStars() {
    bgStars = [];
    const count = W < 600 ? 80 : 200;
    for (let i = 0; i < count; i++) {
      bgStars.push({
        x: Math.random() * W,
        y: Math.random() * H,
        size: 0.3 + Math.random() * 1.2,
        alpha: 0.2 + Math.random() * 0.5,
        twinkleSpeed: 0.001 + Math.random() * 0.003,
        phase: Math.random() * Math.PI * 2,
      });
    }
  }

  function drawBgStars() {
    for (const s of bgStars) {
      const a = s.alpha * (0.5 + 0.5 * Math.sin(time * s.twinkleSpeed + s.phase));
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(200, 220, 255, ${a})`;
      ctx.fill();
    }
  }

  // ─── Rays emanating from center ───
  function drawRays() {
    const rayCount = 12;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";

    for (let i = 0; i < rayCount; i++) {
      const angle = (i / rayCount) * Math.PI * 2 + time * CFG.rotationSpeed * 0.5;
      const len = minDim * (0.2 + 0.1 * Math.sin(time * 0.0005 + i));
      const alpha = 0.02 + 0.015 * Math.sin(time * 0.001 + i * 0.8);

      const grad = ctx.createLinearGradient(
        cx, cy,
        cx + Math.cos(angle) * len,
        cy + Math.sin(angle) * len * 0.4
      );
      grad.addColorStop(0, `rgba(0, 255, 136, ${alpha})`);
      grad.addColorStop(1, "rgba(0, 255, 136, 0)");

      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(
        cx + Math.cos(angle - 0.03) * len,
        cy + Math.sin(angle - 0.03) * len * 0.4
      );
      ctx.lineTo(
        cx + Math.cos(angle + 0.03) * len,
        cy + Math.sin(angle + 0.03) * len * 0.4
      );
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();
    }

    ctx.restore();
  }

  // ─── Providence Triangle ───
  // The sacred triangle surrounding the eye — pulsing, glowing, alive

  // Triangle vertices: top-center, bottom-left, bottom-right
  function getTriangleVerts(scale) {
    const pulse = 1 + 0.02 * Math.sin(time * CFG.pulseSpeed * 1.5);
    const R = minDim * 0.42 * scale * pulse;
    // Equilateral triangle, point up, centered on the eye
    // Shift center slightly up so eye sits in upper third (classic providence layout)
    const triCy = cy + R * 0.08;
    return [
      { x: cx, y: triCy - R * 1.0 },                                    // top
      { x: cx - R * Math.sin(Math.PI / 3), y: triCy + R * 0.5 },       // bottom-left
      { x: cx + R * Math.sin(Math.PI / 3), y: triCy + R * 0.5 },       // bottom-right
    ];
  }

  // Interpolate between two points
  function lerp2D(a, b, t) {
    return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  }

  // Edge particles flowing along the triangle
  let triParticles = [];

  function initTriParticles() {
    triParticles = [];
    const count = W < 600 ? 60 : 150;
    for (let i = 0; i < count; i++) {
      triParticles.push({
        edge: Math.floor(Math.random() * 3),   // which edge (0,1,2)
        t: Math.random(),                       // position along edge [0,1]
        speed: 0.0002 + Math.random() * 0.0004, // travel speed
        size: 0.5 + Math.random() * 2.0,
        alpha: 0.3 + Math.random() * 0.7,
        hue: CFG.baseHue + (Math.random() - 0.5) * 30,
        offset: (Math.random() - 0.5) * 8,      // perpendicular offset from edge
        phase: Math.random() * Math.PI * 2,
      });
    }
  }

  function drawTriangle() {
    const verts = getTriangleVerts(1.0);
    const vertsOuter = getTriangleVerts(1.05);

    ctx.save();

    // ── 1. Outer aura / energy field ──
    ctx.globalCompositeOperation = "lighter";
    for (let layer = 3; layer >= 0; layer--) {
      const spread = 1.0 + layer * 0.06;
      const v = getTriangleVerts(spread);
      const auraAlpha = 0.015 - layer * 0.003;

      ctx.beginPath();
      ctx.moveTo(v[0].x, v[0].y);
      ctx.lineTo(v[1].x, v[1].y);
      ctx.lineTo(v[2].x, v[2].y);
      ctx.closePath();
      ctx.strokeStyle = `rgba(0, 255, 136, ${Math.max(auraAlpha, 0.003)})`;
      ctx.lineWidth = 8 + layer * 6;
      ctx.stroke();
    }

    // ── 2. Main triangle lines (double stroke for depth) ──
    // Glow layer
    const glowAlpha = 0.12 + 0.06 * Math.sin(time * CFG.pulseSpeed * 2);
    ctx.beginPath();
    ctx.moveTo(verts[0].x, verts[0].y);
    ctx.lineTo(verts[1].x, verts[1].y);
    ctx.lineTo(verts[2].x, verts[2].y);
    ctx.closePath();
    ctx.strokeStyle = `rgba(0, 255, 136, ${glowAlpha})`;
    ctx.lineWidth = 6;
    ctx.shadowColor = "rgba(0, 255, 136, 0.5)";
    ctx.shadowBlur = 20;
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Sharp inner line
    const lineAlpha = 0.25 + 0.1 * Math.sin(time * CFG.pulseSpeed * 2);
    ctx.beginPath();
    ctx.moveTo(verts[0].x, verts[0].y);
    ctx.lineTo(verts[1].x, verts[1].y);
    ctx.lineTo(verts[2].x, verts[2].y);
    ctx.closePath();
    ctx.strokeStyle = `rgba(0, 255, 136, ${lineAlpha})`;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // ── 3. Edge-flowing particles ──
    const edges = [
      [verts[0], verts[1]],
      [verts[1], verts[2]],
      [verts[2], verts[0]],
    ];

    for (const tp of triParticles) {
      tp.t += tp.speed * 16; // normalized per frame
      if (tp.t > 1) {
        tp.t -= 1;
        tp.edge = (tp.edge + 1) % 3; // flow to next edge
      }

      const [a, b] = edges[tp.edge];
      const pos = lerp2D(a, b, tp.t);

      // Perpendicular offset
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.sqrt(dx * dx + dy * dy) || 1;
      const nx = -dy / len;
      const ny = dx / len;
      const wobble = tp.offset + Math.sin(time * 0.002 + tp.phase) * 3;
      pos.x += nx * wobble;
      pos.y += ny * wobble;

      const twinkle = 0.6 + 0.4 * Math.sin(time * 0.003 + tp.phase * 7);
      const alpha = tp.alpha * twinkle;

      // Particle glow
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, tp.size * 2.5, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${tp.hue}, 100%, 70%, ${alpha * 0.15})`;
      ctx.fill();

      // Particle core
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, tp.size, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${tp.hue}, 90%, 65%, ${alpha})`;
      ctx.fill();
    }

    // ── 4. Vertex flares (bright energy nodes at corners) ──
    for (let i = 0; i < 3; i++) {
      const v = verts[i];
      const flarePhase = time * 0.001 + i * 2.094; // 2π/3 offset per vertex
      const flarePulse = 0.7 + 0.3 * Math.sin(flarePhase);
      const flareR = minDim * 0.025 * flarePulse;

      // Outer glow
      const fGrad = ctx.createRadialGradient(v.x, v.y, 0, v.x, v.y, flareR * 3);
      fGrad.addColorStop(0, `rgba(0, 255, 136, ${0.3 * flarePulse})`);
      fGrad.addColorStop(0.4, `rgba(0, 255, 136, ${0.1 * flarePulse})`);
      fGrad.addColorStop(1, "rgba(0, 255, 136, 0)");
      ctx.beginPath();
      ctx.arc(v.x, v.y, flareR * 3, 0, Math.PI * 2);
      ctx.fillStyle = fGrad;
      ctx.fill();

      // Bright core
      const cGrad = ctx.createRadialGradient(v.x, v.y, 0, v.x, v.y, flareR);
      cGrad.addColorStop(0, `rgba(200, 255, 220, ${0.6 * flarePulse})`);
      cGrad.addColorStop(0.5, `rgba(0, 255, 136, ${0.3 * flarePulse})`);
      cGrad.addColorStop(1, "rgba(0, 255, 136, 0)");
      ctx.beginPath();
      ctx.arc(v.x, v.y, flareR, 0, Math.PI * 2);
      ctx.fillStyle = cGrad;
      ctx.fill();

      // Cross flare (star-like)
      const crossLen = flareR * 2.5;
      const crossAlpha = 0.15 * flarePulse;
      ctx.strokeStyle = `rgba(0, 255, 136, ${crossAlpha})`;
      ctx.lineWidth = 1;
      // Vertical
      ctx.beginPath();
      ctx.moveTo(v.x, v.y - crossLen);
      ctx.lineTo(v.x, v.y + crossLen);
      ctx.stroke();
      // Horizontal
      ctx.beginPath();
      ctx.moveTo(v.x - crossLen, v.y);
      ctx.lineTo(v.x + crossLen, v.y);
      ctx.stroke();
    }

    // ── 5. Sacred geometry — inner triangle (inverted, subtle) ──
    const innerScale = 0.55 + 0.03 * Math.sin(time * CFG.pulseSpeed * 4);
    const innerR = minDim * 0.42 * innerScale;
    const innerCy = cy + innerR * 0.08;
    // Inverted triangle (point down)
    const iv = [
      { x: cx, y: innerCy + innerR * 1.0 },
      { x: cx - innerR * Math.sin(Math.PI / 3), y: innerCy - innerR * 0.5 },
      { x: cx + innerR * Math.sin(Math.PI / 3), y: innerCy - innerR * 0.5 },
    ];

    const innerAlpha = 0.04 + 0.02 * Math.sin(time * CFG.pulseSpeed * 3);
    ctx.beginPath();
    ctx.moveTo(iv[0].x, iv[0].y);
    ctx.lineTo(iv[1].x, iv[1].y);
    ctx.lineTo(iv[2].x, iv[2].y);
    ctx.closePath();
    ctx.strokeStyle = `rgba(0, 255, 136, ${innerAlpha})`;
    ctx.lineWidth = 0.8;
    ctx.stroke();

    ctx.restore();
  }

  // ─── Main loop ───
  let lastTime = 0;
  let rafId = null;
  let isVisible = true;

  function frame(ts) {
    if (!isVisible) { rafId = null; return; }
    const dt = lastTime ? Math.min(ts - lastTime, 50) : 16;
    lastTime = ts;
    time += dt;

    // Trail effect — semi-transparent clear
    ctx.fillStyle = `rgba(10, 10, 18, ${CFG.trailAlpha})`;
    ctx.fillRect(0, 0, W, H);

    // Composite mode for additive glow
    ctx.globalCompositeOperation = "lighter";

    // Background stars
    drawBgStars();

    // Nebula particles (behind everything)
    for (const p of nebulaParticles) {
      renderParticle(p, dt);
    }

    // Spiral arms
    drawSpiralArms();

    // Rays
    drawRays();

    // Star particles
    for (const p of particles) {
      renderParticle(p, dt);
    }

    // Reset composite for iris/contour
    ctx.globalCompositeOperation = "source-over";

    // Providence Triangle (behind the eye center)
    drawTriangle();

    // Eye contour
    drawEyeContour();

    // Iris + pupil (center)
    drawIris();

    rafId = requestAnimationFrame(frame);
  }

  // ─── Visibility observer — pause when off-screen ───
  const canvasObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        if (!isVisible) {
          isVisible = true;
          lastTime = 0;
          rafId = requestAnimationFrame(frame);
        }
      } else {
        isVisible = false;
        if (rafId) {
          cancelAnimationFrame(rafId);
          rafId = null;
        }
      }
    });
  }, { threshold: 0 });

  // ─── Events ───
  function onMouseMove(e) {
    const rect = canvas.getBoundingClientRect();
    mouse.x = e.clientX - rect.left;
    mouse.y = e.clientY - rect.top;
    mouse.active = true;
  }

  function onMouseLeave() {
    mouse.active = false;
  }

  function onTouchMove(e) {
    if (e.touches.length > 0) {
      const rect = canvas.getBoundingClientRect();
      mouse.x = e.touches[0].clientX - rect.left;
      mouse.y = e.touches[0].clientY - rect.top;
      mouse.active = true;
    }
  }

  // ─── Debounce utility ───
  function debounce(fn, ms) {
    let timer;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(fn, ms);
    };
  }

  // ─── Init ───
  function init() {
    resize();
    initParticles();
    initBgStars();
    initTriParticles();

    // Clear fully on first frame
    ctx.fillStyle = CFG.bgColor;
    ctx.fillRect(0, 0, W, H);

    canvas.addEventListener("mousemove", onMouseMove);
    canvas.addEventListener("mouseleave", onMouseLeave);
    canvas.addEventListener("touchmove", onTouchMove, { passive: true });
    window.addEventListener("resize", debounce(() => {
      resize();
      initParticles();
      initBgStars();
      initTriParticles();
    }, 200));

    canvasObserver.observe(canvas);
    rafId = requestAnimationFrame(frame);
  }

  // Start when DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
