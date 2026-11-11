type P = { size?: number };
const base = (size = 16) => ({
  width: size, height: size, viewBox: "0 0 16 16", fill: "none",
  stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
});

export const Play = ({ size }: P) => (
  <svg {...base(size)}><path d="M5 3.5v9l7-4.5z" fill="currentColor" stroke="none" /></svg>
);
export const Pause = ({ size }: P) => (
  <svg {...base(size)}><rect x="4" y="3.5" width="2.6" height="9" fill="currentColor" stroke="none" /><rect x="9.4" y="3.5" width="2.6" height="9" fill="currentColor" stroke="none" /></svg>
);
export const Restart = ({ size }: P) => (
  <svg {...base(size)}><path d="M3 8a5 5 0 1 0 1.6-3.7" /><path d="M3 2.5v3h3" /></svg>
);
export const Minimize = ({ size }: P) => (
  <svg {...base(size)}><path d="M4 8h8" /></svg>
);
export const Maximize = ({ size }: P) => (
  <svg {...base(size)}><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5L9 7M2.5 13.5L7 9" /></svg>
);
export const Close = ({ size }: P) => (
  <svg {...base(size)}><path d="M4 4l8 8M12 4l-8 8" /></svg>
);
export const Undo = ({ size }: P) => (
  <svg {...base(size)}><path d="M5.5 4L2.5 7l3 3" /><path d="M2.5 7h7a3.5 3.5 0 0 1 0 7H8" /></svg>
);
export const Compare = ({ size }: P) => (
  <svg {...base(size)}><path d="M3 13V6M8 13V3M13 13V9" /></svg>
);
export const Spark = ({ size }: P) => (
  <svg {...base(size)}><path d="M8 2v3M8 11v3M2 8h3M11 8h3M4 4l1.8 1.8M10.2 10.2L12 12M12 4l-1.8 1.8M5.8 10.2L4 12" /></svg>
);

/** Abstract falcon-wing mark (FalconAero). */
export const Mark = ({ size = 26 }: P) => (
  <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
    <path d="M3 21c7-1.5 12.5-5.6 16.5-12.5L29 6c-2.6 5.3-6.9 9.8-12.2 12.6L21 27l-5.3-6.9C11.6 21.6 7.3 22 3 21z"
      fill="var(--gold)" />
    <path d="M15.7 20.1L21 27" stroke="#0b0e12" strokeWidth="1.2" />
  </svg>
);
