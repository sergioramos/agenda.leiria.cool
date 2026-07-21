// Continuous announcement-bar marquee, ported from docs/assets/ticker.js.
// Builds two identical halves and animates translateX(-50%) for a seamless,
// gapless loop (the CSS keyframes live in site.css). Segments repeat until each
// half covers the viewport. Rebuilds on font load, resize, and text change.
// The build is measurement-driven (duplicate until width is covered), which is
// awkward as JSX — an imperative useLayoutEffect is the honest port.
import { useLayoutEffect } from 'react';
import type { RefObject } from 'react';

const TICKER_BASE = 'Pregoeiro · O pregão semanal de Lisboa';

function makeSegment(text: string, accent: boolean): HTMLSpanElement {
  const seg = document.createElement('span');
  seg.className = 'ticker-item';
  seg.append(document.createTextNode(TICKER_BASE + (text ? ' · ' : '')));
  if (text) {
    const status = document.createElement('span');
    status.className = 'ticker-status' + (accent ? ' accent' : '');
    status.textContent = text;
    seg.append(status);
  }
  return seg;
}

function buildTicker(track: HTMLElement, text: string, accent: boolean, speedPxPerS = 70): void {
  const half = document.createElement('div');
  half.className = 'ticker-half';
  half.append(makeSegment(text, accent));
  track.replaceChildren(half);

  const segW = Math.max(half.getBoundingClientRect().width, 1);
  const copies = Math.max(2, Math.ceil(window.innerWidth / segW) + 1);
  for (let i = 1; i < copies; i++) half.append(makeSegment(text, accent));

  const halfW = half.getBoundingClientRect().width;
  const dup = half.cloneNode(true) as HTMLElement;
  dup.setAttribute('aria-hidden', 'true');
  track.append(dup);
  track.style.setProperty('--ticker-duration', `${Math.max(Math.round(halfW / speedPxPerS), 10)}s`);
}

export function useTicker(ref: RefObject<HTMLDivElement | null>, text: string, accent = false): void {
  useLayoutEffect(() => {
    const track = ref.current;
    if (!track) return;
    const rebuild = () => buildTicker(track, text, accent);
    rebuild();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(rebuild);
    let timer: ReturnType<typeof setTimeout>;
    const onResize = () => {
      clearTimeout(timer);
      timer = setTimeout(rebuild, 200);
    };
    window.addEventListener('resize', onResize);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('resize', onResize);
    };
  }, [ref, text, accent]);
}
