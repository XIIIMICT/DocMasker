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
        // `valid.keysError` is already null; the second call used to repeat
        // this exact expression. The hook test covers the operation boundary.
        expect(canDemask({ ...valid, hasKeys: false })).toBe(false);
    });

    test("malformed keys, missing input and busy state all block demasking", () => {
        expect(canDemask({ ...valid, keysError: "Неверный формат ключей" })).toBe(false);
        expect(canDemask({ ...valid, hasText: false, hasFile: false })).toBe(false);
        expect(canDemask({ ...valid, busy: true })).toBe(false);
    });
});
