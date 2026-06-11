/* Continuous announcement-bar marquee (Figma: Pregoeiro 19:2073 / 19:2604).
   Builds two identical halves and animates translateX(-50%) for a seamless,
   gapless loop. Segments are repeated until each half covers the viewport. */
'use strict';

function buildTicker(track, makeSegment, speedPxPerS = 70) {
  if (!track) return;
  track.innerHTML = '';
  const half = document.createElement('div');
  half.className = 'ticker-half';
  half.append(makeSegment());
  track.append(half);

  const segW = Math.max(half.getBoundingClientRect().width, 1);
  const copies = Math.max(2, Math.ceil(window.innerWidth / segW) + 1);
  for (let i = 1; i < copies; i++) half.append(makeSegment());

  const halfW = half.getBoundingClientRect().width;
  const dup = half.cloneNode(true);
  dup.setAttribute('aria-hidden', 'true');
  track.append(dup);
  track.style.setProperty('--ticker-duration', `${Math.max(Math.round(halfW / speedPxPerS), 10)}s`);
}
