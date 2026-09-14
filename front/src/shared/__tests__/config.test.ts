import { afterEach, describe, expect, it } from "bun:test";

import { getBackendInternalUrl } from "@/shared/config";

const ORIGINAL = process.env.BACKEND_INTERNAL_URL;

afterEach(() => {
    if (ORIGINAL === undefined) {
        delete process.env.BACKEND_INTERNAL_URL;
    } else {
        process.env.BACKEND_INTERNAL_URL = ORIGINAL;
    }
});

describe("getBackendInternalUrl", () => {
    it("returns the configured backend url", () => {
        process.env.BACKEND_INTERNAL_URL = "http://back:8001";
        expect(getBackendInternalUrl()).toBe("http://back:8001");
    });

    it("throws a Russian error when unset", () => {
        delete process.env.BACKEND_INTERNAL_URL;
        expect(() => getBackendInternalUrl()).toThrow(/BACKEND_INTERNAL_URL/);
    });
});
