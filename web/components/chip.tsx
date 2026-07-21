import type { ReactNode } from 'react';

// Toggle chip: a real <button> carrying its state via aria-pressed
// (accessibility skill — semantic HTML first, ARIA state last).
export default function Chip({
  pressed,
  onClick,
  className = 'chip',
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button type="button" className={className} aria-pressed={pressed} onClick={onClick}>
      {children}
    </button>
  );
}
