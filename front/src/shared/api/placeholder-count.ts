import { loadJsZip } from "@/shared/api/lazy-zip";

/**
 * Count `[LABEL_N]` placeholders in a masked DOCX by inspecting its XML parts.
 *
 * The OPC transformer writes each placeholder into one text node, so a plain
 * scan of the part XML is enough — no DOM parsing or full document text is
 * needed. Shared parts are counted once each (a placeholder occurrence in a
 * header is just as real as one in the body).
 */
export async function countPlaceholdersInDocx(blob: Blob): Promise<Record<string, number>> {
    const JSZip = await loadJsZip();
    const archive = await JSZip.loadAsync(await blob.arrayBuffer());
    const counts: Record<string, number> = {};
    const pattern = /\[([A-Z]+)_\d+\]/g;
    for (const entry of Object.values(archive.files)) {
        if (entry.dir || !entry.name.endsWith(".xml")) continue;
        if (entry.name === "[Content_Types].xml") continue;
        const xml = await entry.async("string");
        for (const match of xml.matchAll(pattern)) {
            const label = match[1]!.toLowerCase();
            counts[label] = (counts[label] ?? 0) + 1;
        }
    }
    return counts;
}
