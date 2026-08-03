import type { ReactNode } from "react";

type PageIntroProps = {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
};

export default function PageIntro({
  title,
  actions,
}: PageIntroProps) {
  return (
    <header className="page-intro">
      <div className="page-intro-copy">
        <h2>{title}</h2>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}
