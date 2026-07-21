import type { ReactNode } from 'react';

// Logo + a right-hand slot (week picker on the public page, "Ver site" link on
// admin). `rv` opts the masthead into the page reveal wave.
export default function Masthead({ rv = false, children }: { rv?: boolean; children?: ReactNode }) {
  return (
    <div className={rv ? 'masthead rv' : 'masthead'}>
      <span className="brand-logo" />
      {children}
    </div>
  );
}
