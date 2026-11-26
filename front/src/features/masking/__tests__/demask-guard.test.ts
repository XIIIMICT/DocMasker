import { describe, expect, test } from "bun:test";

import { canDemask, type DemaskGuardInput } from "../demask-guard";

const valid: DemaskGuardInput = {
    busy: false,
    hasText: true,
    hasFile: false,
    hasKeys: true,
    keysError: null,
};

describe("canDemask", () => {
    test("allows a ready text session with valid keys", () => {
        expect(canDemask(valid)).toBe(true);
    });

    test("allows a file session without text", () => {
        expect(canDemask({ ...valid, hasText: false, hasFile: true })).toBe(true);
    });

    test("empty keys are not valid keys, even with no keysError", () => {
        expect(canDemask({ ...valid, hasKeys: false })).toBe(false);
        expect(canDemask({ ...valid, hasKeys: false, keysError: null })).toBe(false);
    });

    test("malformed keys, missing input and busy state all block demasking", () => {
        expect(canDemask({ ...valid, keysError: "Неверный формат ключей" })).toBe(false);
        expect(canDemask({ ...valid, hasText: false, hasFile: false })).toBe(false);
        expect(canDemask({ ...valid, busy: true })).toBe(false);
    });
});
