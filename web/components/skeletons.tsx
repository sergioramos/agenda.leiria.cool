import type { CSSProperties } from 'react';

// Loading skeleton built from the REAL card structure (same DOM as EventCard),
// so the placeholder matches the layout exactly and there's no shift on swap
// (CLS — web-performance skill). Inline widths are React style props applied via
// CSSOM, which CSP style-src 'self' does not block.
function Bar({ style }: { style: CSSProperties }) {
  return <span className="sk-bar" style={style} />;
}

function SkeletonCard() {
  return (
    <article className="card is-skeleton" aria-hidden="true">
      <div className="card-media">
        <div className="card-img sk-bar" />
      </div>
      <div className="body">
        <div className="card-toprow">
          <div className="card-text">
            <Bar style={{ width: '62%', height: 18 }} />
            <Bar style={{ width: '42%', height: 14 }} />
          </div>
          <Bar style={{ width: 118, height: 34, flex: 'none' }} />
        </div>
        <div className="badges">
          <Bar style={{ width: 96, height: 24 }} />
          <Bar style={{ width: 48, height: 24 }} />
        </div>
      </div>
      <span className="card-divider" aria-hidden="true" />
      <div className="when">
        <Bar style={{ width: 26, height: 12 }} />
        <Bar style={{ width: 30, height: 30 }} />
        <Bar style={{ width: 24, height: 12 }} />
      </div>
    </article>
  );
}

export function ResultsSkeleton({ n = 5 }: { n?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      <div className="sk-head">
        <Bar style={{ width: 84, height: 12 }} />
        <Bar style={{ width: 220, height: 24 }} />
      </div>
      <div className="cards">
        {Array.from({ length: n }, (_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    </div>
  );
}

// Skeleton pills for the topic-chip row while the week loads.
const CHIP_WIDTHS = [217, 175, 117, 209, 234, 200, 209, 267, 225, 192, 259, 209, 234, 250];
export function ChipSkeleton() {
  return (
    <>
      {CHIP_WIDTHS.map((w, i) => (
        <span key={i} className="sk-bar sk-chip" style={{ width: w }} aria-hidden="true" />
      ))}
    </>
  );
}
