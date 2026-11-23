import type { Metadata } from "next";

import { Providers } from "./providers";

import "./globals.css";

export const metadata: Metadata = {
    description: "Маскирование чувствительной информации в тексте и DOCX-файлах",
    applicationName: "ДокМаскер",
    icons: {
        icon: "/ruschem_icon.png",
        apple: "/ruschem_icon.png",
    },
};

export default function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    return (
        <html lang="ru" className="h-full antialiased" suppressHydrationWarning>
            <head>
                {/* Plain <title> (no metadata.title): Next/React 19 re-applies a
                    metadata title after hydration and would overwrite the
                    client-set per-mode title. This static value is only the
                    pre-hydration label; MaskerPage sets the mode-specific one. */}
                <title>ДокМаскер / Маскировка</title>
            </head>
            <body className="bg-background text-foreground min-h-full font-sans">
                <Providers>{children}</Providers>
            </body>
        </html>
    );
}
