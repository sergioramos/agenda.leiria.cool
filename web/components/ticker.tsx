import { useRef } from 'react';
import { useTicker } from '../hooks/use-ticker';

// Top announcement bar. `text` is the live status (e.g. "Atualizado 5/7/2026"
// or "⚠ Dados de exemplo"); accent tints it vermilion.
export default function Ticker({ text, accent = false }: { text: string; accent?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useTicker(ref, text, accent);
  return (
    <>
      <div className="topbar rv">
        <div className="ticker-track" ref={ref} />
      </div>
      <div className="topbar-rule" aria-hidden="true" />
    </>
  );
}
