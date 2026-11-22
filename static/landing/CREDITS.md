# EnergySense landing — sources, references and assumptions

## UI Forge design references

Patterns studied in the local UI Forge vault and adapted for this page. No vault source was
copied verbatim; the implementations below were re-authored as semantic HTML, CSS and vanilla JS
to match the Flask/vanilla stack and this project's design system.

- `reference/atelier-vale-original` — editorial shell, hero composition, glass navigation,
  once-only reveal choreography, mobile overlay menu structure.
- `art-direction/atelier-vale-material-language` — measured palette (ink / olive / clay),
  serif-display + grotesque pairing, 1200px shell, `cubic-bezier(.16,1,.3,1)` easing.
- `directional-light` — pointer-following, damped (spring-like) light model. Re-implemented as a
  single requestAnimationFrame loop writing CSS custom properties, applied to separate material
  layers with directional masks rather than a cursor radial gradient.
- `editorial-stagger` — shared timing and offset child entrances. Re-implemented with
  IntersectionObserver plus CSS custom-property delays.
- `technology/cinematic-scroll-playbook` — one owner per scroll-driven property, measured progress,
  teardown, and stable reduced-motion composition.
- `film-frame` — still-under-media layering principle, adapted to layered stills instead of video.

## Typography

- **Fraunces** (variable, roman + italic) — SIL Open Font License 1.1.
- **DM Sans** (variable) — SIL Open Font License 1.1.

Both are self-hosted in `fonts/` (fetched once from Google Fonts while building; no runtime
network request). Latin subsets only.

## Imagery

All imagery was generated for this project and then graded, matted and compressed locally.
No stock, no hotlinked assets, no third-party photography.

- `img/hero-wall.webp` — lime-plaster wall with raking light and olive-leaf shadows
- `img/hero-stone.webp` — translucent amber calcite, transparent matte
- `img/hero-pedestal.webp` — limestone plinth, transparent matte
- `img/hero-branch.webp` — olive branch, transparent matte
- `img/hero-drape.webp` — olive linen, transparent matte
- `img/arch-olive.webp` — plaster arch onto an olive grove
- `img/bronze-disc.webp` — patinated bronze disc in plaster
- `img/dusk-ridge.webp` — olive treeline at last light

## Quantitative content

The 24-hour load profile and the shift figures are **illustrative**, not measured household data.
The slider computes its outputs live from the stated assumptions:

- tariff: ₹8.40 / kWh peak, ₹4.60 / kWh off-peak
- modelled carbon: 0.79 / 0.47 kg CO₂e per kWh
- flexible load shifted: 0.2 - 1.4 kWh per day

The page's factual claims are limited to the system's design: a 24-hour forecast horizon, one
aggregate meter input, heuristic appliance estimation (not direct per-appliance measurement),
and local/offline execution.
