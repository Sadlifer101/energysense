/* =====================================================================
   EnergySense — motion runtime
   One small, dependency-free motion system shared by the landing page and
   the dashboard. It exists so every state change has a single, consistent
   owner: no element is animated by CSS, Web Animations and a script at the
   same time.

   Patterns adapted from the UI Forge motion recipes that matched this task:
     motion/layout ....................... shared-layout indicator: the marker
                                           is moved, never destroyed and rebuilt
     motion-primitives/animated-background  gliding active pill (transform only)
     motion-primitives/carousel ......... direction-aware out/in content swap
     motion-primitives/accordion ........ measured height + opacity disclosure
     motion-primitives/animated-group ... short, capped stagger for entries

   The Forge entries are React/Motion recipes with no retrievable vanilla
   source, so the mechanisms below are native equivalents: one spring
   integrator (interruptible, velocity carrying), one tween driver and WAAPI
   for compositor-friendly opacity/transform work.

   Accessibility: every primitive collapses to an instant state change when
   (prefers-reduced-motion: reduce) matches. Nothing here delays input: a
   new call always cancels and re-targets the previous one.
   ===================================================================== */
(() => {
  "use strict";

  const win = window;
  const doc = document;
  const raf = win.requestAnimationFrame.bind(win);
  const caf = win.cancelAnimationFrame.bind(win);
  const reduceMQ = win.matchMedia("(prefers-reduced-motion: reduce)");

  /* --------------------------------------------------------------- tokens
     Durations/easings line up with --mo-* in both stylesheets. */
  const TOKENS = {
    exit: 110,
    content: 260,
    macro: 320,
    spring: { stiffness: 240, damping: 28, mass: 1 },
    springSoft: { stiffness: 150, damping: 24, mass: 1 },
    springCount: { stiffness: 240, damping: 30, mass: 1 },
    /* Tight, near-critical response for live controls (sliders) where the
       readout must track the thumb rather than trail it. */
    springLive: { stiffness: 700, damping: 48, mass: 1 }
  };

  const EASE_OUT = "cubic-bezier(0.16, 1, 0.3, 1)";
  const EASE_IN = "cubic-bezier(0.4, 0, 1, 1)";

  const reduced = () => reduceMQ.matches;
  const clamp = (n, lo, hi) => (n < lo ? lo : n > hi ? hi : n);

  /* ------------------------------------------------------- frame watchdog
     Browsers suspend requestAnimationFrame for occluded or backgrounded
     windows. A spring driven by a paused rAF must never leave the UI showing
     a stale value, so the driver checks whether frames are actually arriving
     and falls back to applying targets directly. State stays truthful; only
     the easing is lost. The probe re-runs after any user input, so a window
     that comes back to the foreground animates again. */
  let framesLive = true;
  let probing = false;
  let probedAt = 0;

  function probeFrames() {
    if (probing) return;
    const now = typeof performance !== "undefined" ? performance.now() : Date.now();
    if (now - probedAt < 1000) return;
    probedAt = now;
    probing = true;
    let ticked = false;
    raf(() => {
      ticked = true;
      framesLive = true;
      probing = false;
    });
    setTimeout(() => {
      if (!ticked) {
        framesLive = false;
        probing = false;
        flushChannels();
      }
    }, 260);
  }

  /* A paused frame clock never runs the springs, so any driver still waiting
     for a frame is finished off at its target here — the marker, a counter or
     a slider readout must show the real value, animated or not. */
  function finishChannel(ch) {
    if (!ch || !ch.raf) return;
    caf(ch.raf);
    ch.raf = 0;
    ch.value = ch.target;
    ch.velocity = 0;
    if (ch.update) ch.update(ch.target);
    const done = ch.done;
    ch.done = null;
    if (done) done();
  }

  function flushChannels() {
    Object.keys(channels).forEach((key) => finishChannel(channels[key]));
  }

  /* Guard for a driver started before the frame clock was known to be dead:
     it settles at its target instead of waiting forever. */
  function armSettleGuard(ch) {
    setTimeout(() => {
      /* No frame has been delivered to this driver: the frame clock is
         suspended, so settle at the target instead of waiting forever. */
      if (!framesLive || !ch.ticks) finishChannel(ch);
    }, 450);
  }

  const reProbe = () => {
    probedAt = 0;
    probeFrames();
  };
  ["pointerdown", "keydown", "focus"].forEach((type) => {
    win.addEventListener(type, reProbe, { passive: true, capture: true });
  });
  doc.addEventListener("visibilitychange", reProbe);

  const animateNow = () => framesLive && !reduced() && typeof doc.documentElement.animate === "function";

  /* ------------------------------------------------------------- channels
     One driver per (element, property). Channels keep their current value
     and velocity, which is what makes an interrupted spring continue from
     where it was instead of restarting. */
  let uid = 0;
  const props = new WeakMap();
  const channels = Object.create(null);

  function keyFor(el, prop) {
    let bag = props.get(el);
    if (!bag) {
      bag = Object.create(null);
      props.set(el, bag);
    }
    if (!bag[prop]) {
      uid += 1;
      bag[prop] = "mo" + uid + ":" + prop;
    }
    return bag[prop];
  }

  function channel(key) {
    let ch = channels[key];
    if (!ch) {
      ch = channels[key] = { value: null, velocity: 0, target: 0, raf: 0 };
    }
    return ch;
  }

  function drive(key, cfg) {
    const ch = channel(key);
    if (ch.raf) {
      caf(ch.raf);
      ch.raf = 0;
    }

    probeFrames();

    const target = cfg.to;
    ch.target = target;
    ch.update = cfg.onUpdate || null;
    ch.done = cfg.onDone || null;
    ch.ticks = 0;

    if (cfg.snap) {
      ch.value = target;
      ch.velocity = 0;
    }
    if (ch.value === null) {
      ch.value = cfg.from === undefined ? target : cfg.from;
    }

    if (reduced() || !framesLive) {
      ch.value = target;
      ch.velocity = 0;
      if (ch.update) ch.update(target);
      const done = ch.done;
      ch.done = null;
      if (done) done();
      return;
    }

    if (cfg.mode === "tween") {
      const from = ch.value;
      const dur = Math.max(1, cfg.duration || TOKENS.content);
      const ease = cfg.ease || ((p) => 1 - Math.pow(1 - p, 3));
      let start = 0;
      const stepT = (t) => {
        ch.ticks += 1;
        if (!start) start = t;
        const p = clamp((t - start) / dur, 0, 1);
        ch.value = from + (target - from) * ease(p);
        if (ch.update) ch.update(ch.value);
        if (p < 1) {
          ch.raf = raf(stepT);
          return;
        }
        ch.value = target;
        ch.raf = 0;
        const done = ch.done;
        ch.done = null;
        if (done) done();
      };
      ch.raf = raf(stepT);
      armSettleGuard(ch);
      return;
    }

    const stiffness = cfg.stiffness || TOKENS.spring.stiffness;
    const damping = cfg.damping || TOKENS.spring.damping;
    const mass = cfg.mass || TOKENS.spring.mass;
    const rest = cfg.rest || 0.015;
    let last = 0;

    const stepS = (t) => {
      ch.ticks += 1;
      const dt = Math.min(64, t - (last || t)) / 1000;
      last = t;
      /* Fixed sub-steps keep the integrator stable across frame drops. */
      const n = Math.max(1, Math.ceil(dt / 0.008));
      const h = dt / n;
      for (let i = 0; i < n; i += 1) {
        const force = -stiffness * (ch.value - target) - damping * ch.velocity;
        ch.velocity += (force / mass) * h;
        ch.value += ch.velocity * h;
      }
      if (ch.update) ch.update(ch.value);

      if (Math.abs(target - ch.value) < rest && Math.abs(ch.velocity) < rest * 14) {
        ch.value = target;
        ch.velocity = 0;
        ch.raf = 0;
        if (ch.update) ch.update(target);
        const done = ch.done;
        ch.done = null;
        if (done) done();
        return;
      }
      ch.raf = raf(stepS);
    };
    ch.raf = raf(stepS);
    armSettleGuard(ch);
  }

  /* ------------------------------------------------------- scalar helpers */

  function spring(el, prop, to, opts) {
    const o = opts || {};
    drive(keyFor(el, prop), {
      to: to,
      from: o.from,
      snap: !!o.snap,
      stiffness: o.stiffness,
      damping: o.damping,
      mass: o.mass,
      rest: o.rest,
      onUpdate: o.onUpdate,
      onDone: o.onDone
    });
  }

  function tween(el, prop, to, opts) {
    const o = opts || {};
    drive(keyFor(el, prop), {
      mode: "tween",
      to: to,
      from: o.from,
      snap: !!o.snap,
      duration: o.duration,
      ease: o.ease,
      onUpdate: o.onUpdate,
      onDone: o.onDone
    });
  }

  /* Count a number up/down. Interruptible: the channel carries its value. */
  function count(el, to, format, opts) {
    const o = opts || {};
    const fmt = typeof format === "function" ? format : (v) => String(Math.round(v));
    if (!el || typeof to !== "number" || !isFinite(to)) return;
    drive(keyFor(el, "num"), {
      to: to,
      from: o.from,
      snap: o.snap,
      stiffness: o.stiffness || TOKENS.springCount.stiffness,
      damping: o.damping || TOKENS.springCount.damping,
      onUpdate: (v) => {
        el.textContent = fmt(v);
      },
      onDone: o.onDone
    });
  }

  /* --------------------------------------------------------- element swap */

  function stopElementAnimations(el) {
    if (!el.getAnimations) return null;
    const live = el.getAnimations();
    if (!live.length) return null;
    const cs = win.getComputedStyle(el);
    const frozen = {
      opacity: parseFloat(cs.opacity),
      transform: cs.transform === "none" ? "" : cs.transform
    };
    live.forEach((a) => a.cancel());
    if (isFinite(frozen.opacity)) el.style.opacity = String(frozen.opacity);
    el.style.transform = frozen.transform;
    el.style.height = "";
    el.style.overflow = "";
    return frozen;
  }

  function clearElementStyles(el) {
    if (!el.getAnimations) return;
    el.getAnimations().forEach((a) => a.cancel());
    el.style.opacity = "";
    el.style.transform = "";
    el.style.height = "";
    el.style.overflow = "";
    el.style.willChange = "";
  }

  const shift = (x, y) =>
    "translate3d(" + x.toFixed(2) + "px," + y.toFixed(2) + "px,0)";

  /* Direction-aware content replacement.
     dir > 0 : forward (new content enters from the right)
     dir < 0 : backward
     dir   0 : neutral crossfade (peer states, hover) */
  function swap(el, mutate, opts) {
    const o = opts || {};
    if (!el) return;
    const token = (el.__moSwap || 0) + 1;
    el.__moSwap = token;

    probeFrames();

    if (reduced() || !framesLive || typeof el.animate !== "function") {
      stopElementAnimations(el);
      mutate();
      clearElementStyles(el);
      return;
    }

    const dir = o.dir > 0 ? 1 : o.dir < 0 ? -1 : 0;
    const dist = o.distance === undefined ? 9 : o.distance;
    const exitDur = o.exit === undefined ? TOKENS.exit : o.exit;
    const enterDur = o.duration || TOKENS.content;

    const frozen = stopElementAnimations(el);
    const startOpacity = frozen && isFinite(frozen.opacity) ? frozen.opacity : 1;
    const startTransform = frozen && frozen.transform ? frozen.transform : "none";

    el.style.willChange = "opacity, transform";

    /* The content is committed by whichever arrives first — the exit
       animation finishing or a timer — so a throttled or paused frame clock
       can never leave the element showing the previous state. */
    let committed = false;
    const commit = () => {
      if (committed || el.__moSwap !== token) return;
      committed = true;

      const lockHeight = o.height !== false;
      const heightFrom = el.getBoundingClientRect().height;
      if (lockHeight) {
        el.style.overflow = "hidden";
        el.style.height = heightFrom + "px";
      }

      mutate();

      if (!framesLive) {
        clearElementStyles(el);
        if (o.onDone) o.onDone();
        return;
      }

      let natural = heightFrom;
      if (lockHeight) natural = Math.max(el.scrollHeight, heightFrom);

      const enter = el.animate(
        [
          { opacity: 0, transform: shift(dir * dist, 0) },
          { opacity: 1, transform: "none" }
        ],
        { duration: enterDur, easing: EASE_OUT, fill: "forwards" }
      );

      if (lockHeight && Math.abs(natural - heightFrom) > 1) {
        el.animate(
          [{ height: heightFrom + "px" }, { height: natural + "px" }],
          { duration: o.duration || TOKENS.macro, easing: EASE_OUT, fill: "forwards" }
        );
      }

      let done = false;
      const finish = () => {
        if (done || el.__moSwap !== token) return;
        done = true;
        clearElementStyles(el);
        if (o.onDone) o.onDone();
      };
      enter.onfinish = finish;
      setTimeout(finish, enterDur + 80);
    };

    const exit = el.animate(
      [
        { opacity: startOpacity, transform: startTransform },
        { opacity: 0, transform: shift(-dir * dist, dir === 0 ? -3 : 0) }
      ],
      { duration: exitDur, easing: EASE_IN, fill: "forwards" }
    );
    exit.onfinish = commit;
    setTimeout(commit, exitDur + 40);
  }

  /* Content has already changed; settle the new content in. Used for short
     badges where an exit phase would only add latency. */
  function appear(el, dir, opts) {
    const o = opts || {};
    if (!el) return;
    probeFrames();
    if (reduced() || !framesLive || typeof el.animate !== "function") {
      clearElementStyles(el);
      return;
    }
    const token = (el.__moAppear || 0) + 1;
    el.__moAppear = token;
    stopElementAnimations(el);
    const d = dir > 0 ? 1 : dir < 0 ? -1 : 0;
    const dist = o.distance === undefined ? 6 : o.distance;
    const dur = o.duration || TOKENS.content;
    const anim = el.animate(
      [
        { opacity: 0, transform: shift(d * dist, d === 0 ? -dist * 0.5 : 0) },
        { opacity: 1, transform: "none" }
      ],
      { duration: dur, easing: EASE_OUT, fill: "forwards" }
    );
    const finish = () => {
      if (el.__moAppear !== token) return;
      clearElementStyles(el);
    };
    anim.onfinish = finish;
    setTimeout(finish, dur + 80);
  }

  function text(el, value, opts) {
    if (!el) return;
    const next = String(value);
    if (el.textContent === next) return;
    swap(
      el,
      () => {
        el.textContent = next;
      },
      Object.assign({ height: false, distance: 6, duration: 220, exit: 90 }, opts)
    );
  }

  /* ----------------------------------------------------------- indicator */
  /* Moves one persistent marker under/onto the active item. */
  function pill(el, targetEl, opts) {
    const o = opts || {};
    if (!el || !targetEl) return;
    const x = targetEl.offsetLeft;
    const w = targetEl.offsetWidth;
    const springCfg = {
      snap: !!o.snap,
      stiffness: o.stiffness || TOKENS.spring.stiffness,
      damping: o.damping || TOKENS.spring.damping
    };
    spring(el, "x", x, {
      snap: springCfg.snap,
      stiffness: springCfg.stiffness,
      damping: springCfg.damping,
      onUpdate: (v) => {
        el.style.transform = shift(v, 0);
      }
    });
    spring(el, "w", w, {
      snap: springCfg.snap,
      stiffness: springCfg.stiffness - 20,
      damping: springCfg.damping,
      onUpdate: (v) => {
        el.style.width = Math.max(0, v).toFixed(2) + "px";
      }
    });
  }

  /* -------------------------------------------------------------- expand */
  /* Open/close a block that is toggled with `hidden`. */
  function expand(el, open, opts) {
    const o = opts || {};
    if (!el) return;
    /* Nothing to animate when the block is already in the requested state. */
    if (!!el.hidden === !open) return;
    probeFrames();
    if (reduced() || !framesLive || typeof el.animate !== "function") {
      stopElementAnimations(el);
      el.hidden = !open;
      clearElementStyles(el);
      return;
    }

    const token = (el.__moExpand || 0) + 1;
    el.__moExpand = token;
    const settle = () => {
      if (el.__moExpand !== token) return;
      clearElementStyles(el);
    };

    stopElementAnimations(el);

    if (open) {
      el.hidden = false;
      const h = el.getBoundingClientRect().height;
      const dur = o.duration || TOKENS.macro;
      el.style.overflow = "hidden";
      const anim = el.animate(
        [
          { height: "0px", opacity: 0, transform: shift(0, -4) },
          { height: h + "px", opacity: 1, transform: "none" }
        ],
        { duration: dur, easing: EASE_OUT, fill: "forwards" }
      );
      anim.onfinish = settle;
      setTimeout(settle, dur + 80);
      return;
    }

    const h = el.getBoundingClientRect().height;
    if (!h) {
      el.hidden = true;
      return;
    }
    const dur = o.duration || 190;
    el.style.overflow = "hidden";
    const anim = el.animate(
      [
        { height: h + "px", opacity: 1 },
        { height: "0px", opacity: 0 }
      ],
      { duration: dur, easing: EASE_IN, fill: "forwards" }
    );
    const close = () => {
      if (el.__moExpand !== token) return;
      el.hidden = true;
      clearElementStyles(el);
    };
    anim.onfinish = close;
    setTimeout(close, dur + 80);
  }

  /* ----------------------------------------------------------- accordion */
  /* <details> is measured and revealed; the summary click is intercepted by
     the caller so the element never snaps open. */
  function accordion(details, content, open, opts) {
    const o = opts || {};
    if (!details || !content) return;
    probeFrames();
    if (reduced() || !framesLive || typeof content.animate !== "function") {
      stopElementAnimations(content);
      details.open = !!open;
      clearElementStyles(content);
      return;
    }

    const token = (content.__moAcc || 0) + 1;
    content.__moAcc = token;
    const settle = () => {
      if (content.__moAcc !== token) return;
      clearElementStyles(content);
    };

    if (open) {
      stopElementAnimations(content);
      details.open = true;
      const h = content.getBoundingClientRect().height;
      const dur = o.duration || TOKENS.macro;
      content.style.overflow = "hidden";
      const anim = content.animate(
        [
          { height: "0px", opacity: 0 },
          { height: h + "px", opacity: 1 }
        ],
        { duration: dur, easing: EASE_OUT, fill: "forwards" }
      );
      anim.onfinish = settle;
      setTimeout(settle, dur + 80);
      return;
    }

    const h = content.getBoundingClientRect().height;
    if (!h) {
      details.open = false;
      return;
    }
    const dur = o.duration || 190;
    stopElementAnimations(content);
    content.style.overflow = "hidden";
    const anim = content.animate(
      [
        { height: h + "px", opacity: 1 },
        { height: "0px", opacity: 0 }
      ],
      { duration: dur, easing: EASE_IN, fill: "forwards" }
    );
    const close = () => {
      if (content.__moAcc !== token) return;
      details.open = false;
      clearElementStyles(content);
    };
    anim.onfinish = close;
    setTimeout(close, dur + 80);
  }

  /* -------------------------------------------------------------- enter */
  /* Short capped stagger for freshly rendered rows. */
  function enter(container, selector, opts) {
    const o = opts || {};
    if (!container || reduced()) return;
    probeFrames();
    /* With a paused frame clock a delayed `backwards` fill would leave the
       rows invisible: skip the entrance and show them plainly. */
    if (!framesLive) return;
    const items = container.querySelectorAll(selector);
    if (!items.length) return;
    const max = o.max === undefined ? 8 : o.max;
    const step = o.step === undefined ? 24 : o.step;
    const distance = o.distance === undefined ? 6 : o.distance;
    const duration = o.duration || TOKENS.content;
    for (let i = 0; i < items.length; i += 1) {
      const el = items[i];
      if (typeof el.animate !== "function") return;
      if (i >= max) break;
      const anim = el.animate(
        [
          { opacity: 0, transform: shift(0, distance) },
          { opacity: 1, transform: "none" }
        ],
        { duration: duration, delay: i * step, easing: EASE_OUT, fill: "backwards" }
      );
      /* A paused frame clock would freeze a delayed "backwards" fill at
         opacity 0; cancelling restores the element's own styles. */
      setTimeout(() => anim.cancel(), duration + i * step + 100);
    }
  }

  function onChange(fn) {
    if (reduceMQ.addEventListener) reduceMQ.addEventListener("change", fn);
    else if (reduceMQ.addListener) reduceMQ.addListener(fn);
  }

  win.Motion = {
    tokens: TOKENS,
    reduced: reduced,
    onChange: onChange,
    spring: spring,
    tween: tween,
    count: count,
    swap: swap,
    text: text,
    appear: appear,
    pill: pill,
    expand: expand,
    accordion: accordion,
    enter: enter
  };
})();
