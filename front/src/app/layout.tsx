import type { Metadata } from "next";

import { Providers } from "./providers";

import "./globals.css";

export const metadata: Metadata = {
    title: "DocMasker",
    description: "Маскирование чувствительной информации в тексте",
    applicationName: "DocMasker",
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
