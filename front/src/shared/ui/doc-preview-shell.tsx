"use client";

export function DocIcon() {
    return (
        <svg viewBox="0 0 24 24" width="44" height="44" fill="none" aria-hidden="true">
            <path
                d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7l-5-5z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinejoin="round"
            />
            <path d="M14 2v5h5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
            <path
                d="M9 13h6M9 17h6M9 9h1"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
            />
        </svg>
    );
}

export function DocPreviewShell({
    title,
    subtitle,
    children,
}: {
    title: string;
    subtitle?: string;
    children?: React.ReactNode;
}) {
    return (
        <div className="docx-preview">
            <div className="docx-preview__icon">
                <DocIcon />
            </div>
            <div className="docx-preview__meta">
                <div className="docx-preview__name">{title}</div>
                {subtitle ? <div className="docx-preview__subtitle">{subtitle}</div> : null}
            </div>
            {children}
        </div>
    );
}
