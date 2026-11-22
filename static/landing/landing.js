/* =====================================================================
   EnergySense — landing behaviour
   Vanilla JS, no dependencies. Progressive: every section reads without it.
   ===================================================================== */
(() => {
  "use strict";

  const doc = document;
  const reduceMQ = window.matchMedia("(prefers-reduced-motion: reduce)");
  const touchMQ = window.matchMedia("(hover: none), (pointer: coarse)");
  let reduce = reduceMQ.matches;

  /* -------------------------------------------------------------------
     Header: quiet compression once the page moves
     ------------------------------------------------------------------- */
  const head = doc.querySelector("[data-head]");
  if (head) {
    const setHead = () => head.classList.toggle("is-stuck", window.scrollY > 18);
    setHead();
    window.addEventListener("scroll", setHead, { passive: true });
  }

  /* -------------------------------------------------------------------
     Mobile menu
     ------------------------------------------------------------------- */
  const menu = doc.querySelector("[data-menu]");
  const menuToggle = doc.querySelector("[data-menu-toggle]");
  const menuClose = doc.querySelector("[data-menu-close]");

  if (menu && menuToggle) {
    let open = false;
    let lastFocus = null;
    const background = [doc.getElementById("main"), doc.querySelector(".foot"), head].filter(Boolean);

    if ("inert" in menu) menu.inert = true;

    const openMenu = () => {
      if (open) return;
      open = true;
      lastFocus = doc.activeElement;
      menu.hidden = false;
      menu.inert = false;
      doc.body.classList.add("menu-open");
      menuToggle.setAttribute("aria-expanded", "true");
      background.forEach((el) => { el.inert = true; });
      /* Force one style flush so the sheet is painted in its closed state;
         without it the open transition (and its link stagger) is skipped. */
      void menu.offsetWidth;
      if (!open) return;
      /* Applied synchronously: the open state must never depend on a frame
         callback, which a throttled window can withhold. */
      menu.classList.add("is-open");
      const first = menu.querySelector("a[href], button");
      if (first) first.focus({ preventScroll: true });
      else menu.focus({ preventScroll: true });
      /* Resilience net: if the sheet never became visible (a throttled
         window can withhold frames), stop animating and show it plainly. */
      window.setTimeout(() => {
        if (!open) return;
        const opacity = parseFloat(window.getComputedStyle(menu).opacity);
        if (!(opacity > 0.5)) menu.classList.add("is-static");
      }, 500);
    };

    const closeMenu = (restoreFocus = true) => {
      if (!open) return;
      open = false;
      menu.classList.remove("is-open");
      menu.classList.remove("is-static");
      menuToggle.setAttribute("aria-expanded", "false");
      doc.body.classList.remove("menu-open");
      background.forEach((el) => { el.inert = false; });
      window.setTimeout(() => {
        if (!open) {
          menu.hidden = true;
          menu.inert = true;
        }
      }, 560);
      if (restoreFocus && lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
    };

    menuToggle.addEventListener("click", openMenu);
    if (menuClose) menuClose.addEventListener("click", () => closeMenu());

    menu.querySelectorAll('a[href^="#"]').forEach((link) => {
      link.addEventListener("click", () => {
        closeMenu(false);
        window.setTimeout(() => {
          const target = doc.querySelector(link.getAttribute("href"));
          if (target) {
            target.setAttribute("tabindex", "-1");
            target.focus({ preventScroll: true });
          }
        }, 360);
      });
    });

    doc.addEventListener("keydown", (event) => {
      if (!open) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(menu.querySelectorAll("a[href], button:not([disabled])"))
        .filter((el) => el.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!event.shiftKey && doc.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && doc.activeElement === first) {
        event.preventDefault();
        last.focus();
      }
    });

    window.matchMedia("(min-width: 901px)").addEventListener("change", (event) => {
      if (event.matches) closeMenu(false);
    });
  }

  /* -------------------------------------------------------------------
     Editorial reveals
     ------------------------------------------------------------------- */
  const revealTargets = Array.from(doc.querySelectorAll(".reveal, .plate"));

  const showAll = () => revealTargets.forEach((el) => el.classList.add("is-in"));

  if ("IntersectionObserver" in window && !reduce) {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-in");
          io.unobserve(entry.target);
        });
      },
      { rootMargin: "0px 0px -6% 0px", threshold: 0.1 }
    );
    revealTargets.forEach((el) => io.observe(el));
  } else {
    showAll();
  }

  /* -------------------------------------------------------------------
     Observe / Anticipate / Adapt — chapter index follows the reading column
     ------------------------------------------------------------------- */
  const chapterSection = doc.querySelector("[data-chapters]");
  if (chapterSection) {
    const blocks = Array.from(chapterSection.querySelectorAll("[data-chapter-block]"));
    const items = Array.from(chapterSection.querySelectorAll(".chapters__rail li"));
    let active = -1;

    const setActive = (index) => {
      if (index === active || index < 0) return;
      active = index;
      items.forEach((item, i) => item.classList.toggle("is-active", i === index));
    };

    const syncFromScroll = () => {
      const marker = window.innerHeight * 0.56;
      let index = 0;
      blocks.forEach((block, i) => {
        if (block.getBoundingClientRect().top <= marker) index = i;
      });
      setActive(index);
    };

    setActive(0);
    syncFromScroll();
    window.addEventListener("scroll", syncFromScroll, { passive: true });
    window.addEventListener("resize", syncFromScroll);
  }

  /* -------------------------------------------------------------------
     Hero lighting
     Layered images + CSS masks. One damped rAF loop drives every variable;
     no radial "spotlight" follows the pointer. Light direction, material
     highlight, shadow bias and parallax all read from the same pointer.
     ------------------------------------------------------------------- */
  const hero = doc.querySelector("[data-scene]");

  if (hero) {
    const stone = hero.querySelector(".scene__stone");
    let tx = 0.12, ty = -0.08;
    let cx = 0.12, cy = -0.08;
    let tilt = 0.5, cli = 0.5;
    let tsy = 0, csy = 0;
    let raf = null, running = false, last = 0, attached = false;

    const writeVars = () => {
      hero.style.setProperty("--mx", cx.toFixed(4));
      hero.style.setProperty("--my", cy.toFixed(4));
      hero.style.setProperty("--li", cli.toFixed(3));
      hero.style.setProperty("--sy", csy.toFixed(4));
      hero.style.setProperty("--light-angle", (114 + cx * 30).toFixed(2) + "deg");
      hero.style.setProperty(
        "--light-pos",
        (50 + cx * 38).toFixed(2) + "% " + (46 + cy * 32).toFixed(2) + "%"
      );
    };

    const step = (now) => {
      const dt = Math.min(64, now - (last || now)) / 1000;
      last = now;
      const kPointer = 1 - Math.exp(-dt * 5.2);
      const kLight = 1 - Math.exp(-dt * 3.6);
      const kScroll = 1 - Math.exp(-dt * 9);
      cx += (tx - cx) * kPointer;
      cy += (ty - cy) * kPointer;
      cli += (tilt - cli) * kLight;
      csy += (tsy - csy) * kScroll;
      writeVars();

      const settled =
        Math.abs(tx - cx) < 0.0015 &&
        Math.abs(ty - cy) < 0.0015 &&
        Math.abs(tilt - cli) < 0.002 &&
        Math.abs(tsy - csy) < 0.0012;

      if (settled) {
        running = false;
        raf = null;
        last = 0;
        return;
      }
      raf = requestAnimationFrame(step);
    };

    const kick = () => {
      if (!attached) return;
      if (running) return;
      running = true;
      last = 0;
      raf = requestAnimationFrame(step);
    };

    const onMove = (event) => {
      if (event.pointerType === "touch") return;
      const rect = hero.getBoundingClientRect();
      if (!rect.height) return;
      tx = Math.max(-1, Math.min(1, ((event.clientX - rect.left) / rect.width) * 2 - 1));
      ty = Math.max(-1, Math.min(1, ((event.clientY - rect.top) / rect.height) * 2 - 1));

      if (stone) {
        const s = stone.getBoundingClientRect();
        const dx = (event.clientX - (s.left + s.width / 2)) / (rect.width * 0.8);
        const dy = (event.clientY - (s.top + s.height / 2)) / (rect.height * 0.8);
        const near = Math.min(1, Math.hypot(dx, dy));
        tilt = 0.18 + (1 - near) * 0.88;
      }
      kick();
    };

    const onLeave = () => {
      tx = 0.1;
      ty = -0.06;
      tilt = 0.42;
      kick();
    };

    const onScroll = () => {
      const rect = hero.getBoundingClientRect();
      const h = Math.max(1, rect.height);
      tsy = Math.max(0, Math.min(1, -rect.top / (h * 0.82)));
      kick();
    };

    const attach = () => {
      if (attached || reduce || touchMQ.matches) return;
      attached = true;
      hero.addEventListener("pointermove", onMove, { passive: true });
      hero.addEventListener("pointerleave", onLeave);
      window.addEventListener("scroll", onScroll, { passive: true });
      writeVars();
      onScroll();
    };

    const detach = () => {
      if (!attached) return;
      attached = false;
      hero.removeEventListener("pointermove", onMove);
      hero.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
      raf = null;
      running = false;
      last = 0;
      tsy = 0;
      csy = 0;
      cx = tx = 0.12;
      cy = ty = -0.08;
      cli = tilt = 0.5;
      writeVars();
    };

    writeVars();
    attach();

    const refreshMode = () => {
      reduce = reduceMQ.matches;
      if (reduce || touchMQ.matches) detach();
      else attach();
    };
    if (reduceMQ.addEventListener) reduceMQ.addEventListener("change", refreshMode);
    if (touchMQ.addEventListener) touchMQ.addEventListener("change", refreshMode);
    window.addEventListener("resize", () => { if (attached) onScroll(); });
  }

  /* -------------------------------------------------------------------
     Slow parallax in editorial plates (transform on the frame only)
     ------------------------------------------------------------------- */
  const parallaxImages = Array.from(doc.querySelectorAll("[data-parallax]"));
  if (parallaxImages.length && !reduce) {
    const frames = parallaxImages.map((img) => img.closest(".plate__frame") || img);
    let ticking = false;
    const update = () => {
      ticking = false;
      const vh = window.innerHeight || 1;
      frames.forEach((frame) => {
        const rect = frame.getBoundingClientRect();
        if (rect.bottom < -80 || rect.top > vh + 80) return;
        const p = Math.max(-1, Math.min(1, (rect.top + rect.height / 2 - vh / 2) / vh));
        frame.style.transform = "translate3d(0," + (p * -16).toFixed(2) + "px,0)";
      });
    };
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
  }

  /* -------------------------------------------------------------------
     Illustrative shift model — values are computed, assumptions are stated
     The figures are interpolated, not replaced: dragging the range moves the
     numbers with it, in both directions, and the track fills behind the thumb.
     ------------------------------------------------------------------- */
  const range = doc.getElementById("shift");
  if (range) {
    const out = doc.getElementById("shiftOut");
    const costFrom = doc.getElementById("costFrom");
    const costTo = doc.getElementById("costTo");
    const co2After = doc.getElementById("co2After");
    const motion = window.Motion;

    const TARIFF_PEAK = 8.4;
    const TARIFF_OFF = 4.6;
    const CO2_PEAK = 0.79;
    const CO2_OFF = 0.47;

    const fmtKwh = (v) => v.toFixed(1) + " kWh";
    const fmtMoney = (v) => "\u20b9" + v.toFixed(2);
    const fmtCo2 = (v) => v.toFixed(2);

    const min = Number(range.min) || 0;
    const max = Number(range.max) || 100;
    const span = max - min;

    const paintFill = () => {
      const value = Number(range.value) || 0;
      const pct = span > 0 ? ((value - min) / span) * 100 : 0;
      range.style.setProperty("--fill", Math.max(0, Math.min(100, pct)).toFixed(2) + "%");
    };

    const render = () => {
      const kwh = Number(range.value) || 0;
      const before = kwh * TARIFF_PEAK;
      const after = kwh * TARIFF_OFF;
      const co2 = kwh * CO2_OFF;

      paintFill();

      if (motion) {
        const live = motion.tokens.springLive;
        if (out) motion.count(out, kwh, fmtKwh, live);
        if (costFrom) motion.count(costFrom, before, fmtMoney, live);
        if (costTo) motion.count(costTo, after, fmtMoney, live);
        if (co2After) motion.count(co2After, co2, fmtCo2, live);
      } else {
        if (out) out.textContent = fmtKwh(kwh);
        if (costFrom) costFrom.textContent = fmtMoney(before);
        if (costTo) costTo.textContent = fmtMoney(after);
        if (co2After) co2After.textContent = fmtCo2(co2);
      }

      range.setAttribute(
        "aria-valuetext",
        kwh.toFixed(1) + " kilowatt-hours shifted. Cost of that load: rupees " +
        before.toFixed(2) + " peak, rupees " + after.toFixed(2) + " off-peak; " +
        co2.toFixed(2) + " kilograms CO2e."
      );
    };

    range.addEventListener("input", render);
    render();
  }

  /* -------------------------------------------------------------------
     Accessibility: keep reveals visible if motion preference changes
     ------------------------------------------------------------------- */
  reduceMQ.addEventListener("change", (event) => {
    if (event.matches) {
      reduce = true;
      showAll();
    }
  });
  window.__landingReady = true;
})();
