import type { Metadata } from "next";

import { Providers } from "./providers";

import "./globals.css";

export const metadata: Metadata = {
    title: "ДокМаскер",
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
            <body className="bg-background text-foreground min-h-full font-sans">
                <Providers>{children}</Providers>
            </body>
        </html>
    );
}
