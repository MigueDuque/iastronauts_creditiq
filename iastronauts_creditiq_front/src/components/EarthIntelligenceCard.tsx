import { useEffect, useRef, type CSSProperties, type RefObject } from 'react';

/**
 * EarthIntelligenceCard — the "AI MARKET PULSE" hero card.
 * --------------------------------------------------------
 * Renders the market-pulse narrative (eyebrow, headline, subtitle, metric
 * pills) over a large Earth that floats gently inside the card rectangle.
 *
 * Animation refinements:
 *  - The card uses `overflow: hidden`, so the oversized Earth is clipped
 *    cleanly at the card edges and appears to float inside it.
 *  - The Earth is sized responsively with `clamp(min, Nvw, max)`: large on
 *    wide screens, shrinking on narrow ones, never exceeding its bounds.
 *  - Horizontal position is dynamic: computed from the measured card width as
 *    `containerWidth * horizontalFactor` and clamped to `maxHorizontalOffset`
 *    (default 300px). A `ResizeObserver` keeps it responsive.
 *  - Vertical motion is a smooth sinusoidal oscillation around a center that is
 *    shifted down by `verticalOffset`, reversing instantly at ±`amplitude`.
 *
 * All motion and sizing parameters (`verticalOffset`, `horizontalFactor`,
 * `maxHorizontalOffset`, `amplitude`, `speed`, `earthMinSize`, `earthMaxSize`,
 * `earthViewportWidth`) are props and easy to tune.
 */

interface MetricPill {
  icon: string;
  label: string;
  sub: string;
  /** Accent color for the pill's glow / icon backdrop. */
  accent: string;
}

interface EarthIntelligenceCardProps {
  /** Small uppercase label above the headline. */
  eyebrow?: string;
  /** Main headline (first, neutral-colored part). */
  title?: string;
  /** Highlighted continuation of the headline (accent gradient). */
  highlight?: string;
  /** Supporting paragraph under the headline. */
  subtitle?: string;
  /** Metric pills rendered at the bottom of the card. */
  metrics?: MetricPill[];

  /** Vertical offset (px) shifting the oscillation center downward. */
  verticalOffset?: number;
  /** Fraction of the card width used to compute the horizontal offset. */
  horizontalFactor?: number;
  /** Hard cap (px) for how far right the Earth can move. */
  maxHorizontalOffset?: number;
  /** Amplitude (px) of the vertical sinusoidal oscillation. */
  amplitude?: number;
  /** Speed of the oscillation (radians/second). */
  speed?: number;
  /** Smallest the Earth may shrink to on narrow screens (px). */
  earthMinSize?: number;
  /** Largest the Earth may grow to on wide screens (px). */
  earthMaxSize?: number;
  /** Preferred Earth size as a fraction of the viewport width (vw units). */
  earthViewportWidth?: number;
  /** Static rotation of the Earth image, in degrees. */
  rotation?: number;
  /** Earth image URL. */
  imageSrc?: string;

  /** Max darkness of the overlay at the bottom-left corner (0–1). */
  overlayOpacity?: number;
  /** Real Gaussian blur (px) applied to the Earth behind the overlay. 0 = off. */
  overlayBlur?: number;
  /** Gradient direction, e.g. 'to top right', '135deg'. */
  overlayDirection?: string;
}

export default function EarthIntelligenceCard({
  eyebrow = 'AI MARKET PULSE',
  title = '',
  highlight = '',
  subtitle = '',
  metrics = [],
  verticalOffset = 120,
  horizontalFactor = 0.5,
  maxHorizontalOffset = 350,
  amplitude = 5,
  speed = 1.1,
  earthMinSize = 300,
  earthMaxSize = 500,
  earthViewportWidth = 150,
  rotation = 45,
  imageSrc = '/earth_globe.png',
  overlayOpacity = 1.7,
  overlayBlur = 0,
  overlayDirection = 'to top right',
}: EarthIntelligenceCardProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const earthRef = useRef<HTMLImageElement>(null);
  const rafRef = useRef<number>(0);
  const startRef = useRef<number>(0);
  /** Latest clamped horizontal offset (px). Held in a ref so the rAF loop
   *  always reads the current value without restarting the animation. */
  const horizontalOffsetRef = useRef<number>(0);

  // Measure the card width → derive the clamped horizontal offset (responsive).
  useEffect(() => {
    const card = cardRef.current;
    if (!card) return;

    const recompute = () => {
      const containerWidth = card.clientWidth;
      // const maxOffset = maxHorizontalOffset;
      // const horizontalOffset = Math.min(containerWidth * horizontalFactor, maxOffset);
      horizontalOffsetRef.current = Math.min(
        containerWidth * horizontalFactor,
        maxHorizontalOffset,
      );
    };

    recompute();
    const observer = new ResizeObserver(recompute);
    observer.observe(card);
    return () => observer.disconnect();
  }, [horizontalFactor, maxHorizontalOffset]);

  // Drive the smooth sinusoidal vertical oscillation.
  useEffect(() => {
    const animate = (timestamp: number) => {
      if (!startRef.current) startRef.current = timestamp;
      const t = (timestamp - startRef.current) / 1000; // seconds

      const earth = earthRef.current;
      if (earth) {
        const x = horizontalOffsetRef.current;
        // translateY(verticalOffset + amplitude * sin(t)) — smooth oscillation
        // with an immediate reversal at the ±amplitude boundaries.
        const y = verticalOffset + amplitude * Math.sin(t * speed);
        // Base translate(-50%, -50%) re-centers the Earth on its anchor point;
        // rotate() applies the configurable static spin.
        earth.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px) rotate(${rotation}deg)`;
      }

      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafRef.current);
  }, [verticalOffset, amplitude, speed, rotation]);

  const cardStyle: CSSProperties = {
    position: 'relative',
    overflow: 'hidden', // clip the oversized Earth cleanly at the card edges
    borderRadius: 18,
    minHeight: 260,
    padding: '32px 36px',
    // Deep navy / near-black, fading diagonally: almost pure black in the
    // bottom-left → dark navy → slightly lighter dark-blue toward the top-right.
    background:
      'linear-gradient(to top right, rgba(0,0,0,0.9) 0%, rgba(10,20,40,0.8) 50%, rgba(15,25,50,0.6) 100%), #020617',
    border: '1px solid rgba(56, 189, 248, 0.18)',
    boxShadow: '0 22px 60px -24px rgba(8, 47, 110, 0.7)',
    isolation: 'isolate',
  };

  // Earth anchored at the card's horizontal center; the dynamic translateX then
  // pushes it toward the right, where it is partially clipped by the edge.
  // Using an <img> (replaced element) guarantees intrinsic dimensions are
  // respected — a background-image div with height:auto can collapse to 0.
  const earthSizeClamp = `clamp(${earthMinSize}px, ${earthViewportWidth}vw, ${earthMaxSize}px)`;
  const earthStyle: CSSProperties = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    width: earthSizeClamp,
    height: earthSizeClamp,
    borderRadius: '50%',
    objectFit: 'cover',
    transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
    willChange: 'transform',
    filter: [
      'saturate(1.35)',
      'contrast(1.08)',
      'brightness(1.06)',
      'drop-shadow(0 0 18px rgba(13, 40, 84, 0.5))',
      'drop-shadow(0 0 40px rgba(56, 132, 226, 0.18))',
    ].join(' '),
    zIndex: 0,
    pointerEvents: 'none',
    display: 'block',
  };

  // Diagonal gradient: near-black in the bottom-left (where the text sits),
  // fading smoothly to transparent toward the top-right so the Earth stays
  // visible. Sits above the Earth, below the content — improving legibility
  // without blocking the image.
  const overlayStyle: CSSProperties = {
    position: 'absolute',
    inset: 0,
    // Diagonal readability gradient:
    //   bottom-left → near-black  | mid → translucent dark-blue | top-right → transparent
    // The transparent top-right reveals the vivid Earth colors; the dark corner
    // keeps the text legible. overlayOpacity is clamped to a valid 0–1 alpha.
    background: `linear-gradient(${overlayDirection}, rgba(0,0,0,${Math.min(1, overlayOpacity)}) 0%, rgba(10,20,40,${Math.min(
      1,
      overlayOpacity,
    ) * 0.6}) 45%, rgba(0,0,0,0) 100%)`,
    // Real frosted-glass blur of the Earth behind. Set overlayBlur > 0 to use.
    backdropFilter: overlayBlur ? `blur(${overlayBlur}px)` : undefined,
    WebkitBackdropFilter: overlayBlur ? `blur(${overlayBlur}px)` : undefined,
    pointerEvents: 'none',
    zIndex: 1,
  } as CSSProperties;

  const contentStyle: CSSProperties = {
    position: 'relative',
    zIndex: 2,
    maxWidth: '62%',
  };

  return (
    <div ref={cardRef} style={cardStyle}>
      {/* Floating, clipped Earth */}
      <img
        ref={earthRef as RefObject<HTMLImageElement>}
        src={imageSrc}
        alt=""
        aria-hidden="true"
        style={earthStyle}
      />

      {/* Gradient blur/darkening overlay for depth + text readability */}
      <div style={overlayStyle} aria-hidden="true" />

      {/* Narrative content */}
      <div style={contentStyle}>
        {eyebrow && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: '#56CCF2',
            }}
          >
            {eyebrow}
          </span>
        )}

        {(title || highlight) && (
          <h2
            style={{
              margin: '12px 0 0',
              fontSize: 26,
              fontWeight: 700,
              lineHeight: 1.25,
              letterSpacing: '-0.02em',
              color: '#f1f5f9',
            }}
          >
            {title}{' '}
            {highlight && (
              <span
                style={{
                  background: 'linear-gradient(135deg, #2F80FF 0%, #56CCF2 55%, #56F2C1 100%)',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                  backgroundClip: 'text',
                }}
              >
                {highlight}
              </span>
            )}
          </h2>
        )}

        {subtitle && (
          <p
            style={{
              margin: '14px 0 0',
              fontSize: 13.5,
              lineHeight: 1.6,
              color: '#94a3b8',
              maxWidth: 520,
            }}
          >
            {subtitle}
          </p>
        )}

        {metrics.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 22 }}>
            {metrics.map((m) => (
              <div
                key={m.label}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '10px 14px',
                  borderRadius: 12,
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(59,130,246,0.14)',
                  boxShadow: `0 0 24px -12px ${m.accent}`,
                }}
              >
                <span
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: 9,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 15,
                    background: m.accent,
                  }}
                >
                  {m.icon}
                </span>
                <div style={{ lineHeight: 1.3 }}>
                  <p style={{ margin: 0, fontSize: 12.5, fontWeight: 600, color: '#e2e8f0' }}>
                    {m.label}
                  </p>
                  <p style={{ margin: 0, fontSize: 11, color: '#64748b' }}>{m.sub}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
