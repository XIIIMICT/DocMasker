import { MaskerPage } from "./masker-page";

// SUPPORT_MAIL is a runtime server env var: the page must not be statically
// prerendered at build time (otherwise the button state freezes to the build
// environment and the mailbox value is baked into the prerendered HTML).
export const dynamic = "force-dynamic";

/**
 * «Связаться с разработчиком» mailbox (`SUPPORT_MAIL`), read on the server
 * and passed down as a prop — it is not a `NEXT_PUBLIC_` variable, so the
 * address never has to be baked into the client bundle. An empty value simply
 * hides the control in the header.
 */
export default function Home() {
    const supportMail = (process.env.SUPPORT_MAIL ?? "").trim();
    return <MaskerPage supportMail={supportMail} />;
}
