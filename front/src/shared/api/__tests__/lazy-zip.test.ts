import { describe, expect, it } from "bun:test";

import { createJsZipLoader, loadJsZip } from "@/shared/api/lazy-zip";

type JsZipModule = Awaited<ReturnType<typeof loadJsZip>>;

function fakeModule(): JsZipModule {
    return { loadAsync: () => Promise.resolve(null) } as unknown as JsZipModule;
}

describe("createJsZipLoader", () => {
    it("loads one shared module instance even under concurrent callers", async () => {
        const module = fakeModule();
        let calls = 0;
        const loader = createJsZipLoader(async () => {
            calls += 1;
            return module;
        });

        const [first, second] = await Promise.all([loader(), loader()]);

        expect(first).toBe(module);
        expect(second).toBe(module);
        expect(await loader()).toBe(module);
        expect(calls).toBe(1);
    });

    it("propagates an import failure and keeps the same rejection", async () => {
        const reason = new Error("chunk unavailable");
        const loader = createJsZipLoader(() => Promise.reject(reason));

        await expect(loader()).rejects.toBe(reason);
        await expect(loader()).rejects.toBe(reason);
    });
});

describe("loadJsZip", () => {
    it("resolves the real JSZip constructor with its static loader", async () => {
        const JSZip = await loadJsZip();
        expect(typeof JSZip.loadAsync).toBe("function");
    });
});
