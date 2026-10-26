// Inline 16px icons (stroke = currentColor), so the chrome reads like Figma's without an icon library.
const P = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.3,
  strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const Icon = {
  move: () => <svg {...P}><path d="M3 2l9 5-4 1.2L6.5 12z" fill="currentColor" stroke="none" /></svg>,
  hand: () => <svg {...P}><path d="M5 8V3.8a1 1 0 012 0V7m0-3.7a1 1 0 012 0V7m0-2.8a1 1 0 012 0V8m0-1.5a1 1 0 012 0V10a4 4 0 01-4 4H8.4a3 3 0 01-2.3-1.1L3.4 9.6a1 1 0 011.5-1.3L5 8.5" /></svg>,
  rect: () => <svg {...P}><rect x="2.5" y="3.5" width="11" height="9" rx="0.5" /></svg>,
  text: () => <svg {...P}><path d="M3 3.5h10M8 3.5V13M6 13h4" /></svg>,
  undo: () => <svg {...P}><path d="M5 4L2 7l3 3" /><path d="M2 7h7.5a4 4 0 010 8H7" /></svg>,
  redo: () => <svg {...P}><path d="M11 4l3 3-3 3" /><path d="M14 7H6.5a4 4 0 000 8H9" /></svg>,
  eye: () => <svg {...P}><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" /><circle cx="8" cy="8" r="2" /></svg>,
  eyeOff: () => <svg {...P}><path d="M2 2l12 12M6.6 4a6 6 0 011.4-.5C12 3.5 14.5 8 14.5 8a12 12 0 01-1.8 2.3M10.5 11.9A6 6 0 018 12.5C4 12.5 1.5 8 1.5 8a12 12 0 012.3-2.8" /></svg>,
  lock: () => <svg {...P}><rect x="3.5" y="7" width="9" height="6.5" rx="1" /><path d="M5.5 7V5a2.5 2.5 0 015 0v2" /></svg>,
  unlock: () => <svg {...P}><rect x="3.5" y="7" width="9" height="6.5" rx="1" /><path d="M5.5 7V5a2.5 2.5 0 014.8-1" /></svg>,
  image: () => <svg {...P}><rect x="2" y="3" width="12" height="10" rx="1" /><circle cx="6" cy="6.5" r="1.2" /><path d="M2.5 12l4-4 3 3 2-2 2.5 2.5" /></svg>,
  button: () => <svg {...P}><rect x="1.5" y="4.5" width="13" height="7" rx="3.5" /><path d="M5.5 8h5" /></svg>,
  alignLeft: () => <svg {...P}><path d="M2.5 2v12M5 5h8M5 11h5" /></svg>,
  alignHCenter: () => <svg {...P}><path d="M8 2v12M4 5h8M5.5 11h5" /></svg>,
  alignRight: () => <svg {...P}><path d="M13.5 2v12M3 5h8M6 11h5" /></svg>,
  alignTop: () => <svg {...P}><path d="M2 2.5h12M5 5v8M11 5v5" /></svg>,
  alignVCenter: () => <svg {...P}><path d="M2 8h12M5 4v8M11 5.5v5" /></svg>,
  alignBottom: () => <svg {...P}><path d="M2 13.5h12M5 3v8M11 6v5" /></svg>,
  textLeft: () => <svg {...P}><path d="M2.5 4h11M2.5 8h7M2.5 12h9" /></svg>,
  textCenter: () => <svg {...P}><path d="M2.5 4h11M4.5 8h7M3.5 12h9" /></svg>,
  textRight: () => <svg {...P}><path d="M2.5 4h11M6.5 8h7M4.5 12h9" /></svg>,
  upload: () => <svg {...P}><path d="M8 11V3M5 6l3-3 3 3M3 11v2h10v-2" /></svg>,
};
