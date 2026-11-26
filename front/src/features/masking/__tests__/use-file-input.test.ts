import { beforeEach, describe, expect, it, mock } from "bun:test";

import { act, renderHook, waitFor } from "@testing-library/react";
import type { ChangeEvent, DragEvent } from "react";

import type { JobAccepted } from "@/shared/api/jobs";

import { DOCX_MIME } from "../types";
import { useFileInput } from "../use-file-input";
import { useModeDrafts } from "../use-mode-drafts";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const JOB_ID = "12345678-1234-4234-8234-123456789abc";
const ACCEPTED: JobAccepted = {
    job_id: JOB_ID,
    status: "queued",
    status_url: `/api/jobs/${JOB_ID}`,
    result_url: `/api/jobs/${JOB_ID}/result`,
};

function changeEvent(files: File[]): ChangeEvent<HTMLInputElement> {
    return { target: { files } } as unknown as ChangeEvent<HTMLInputElement>;
}

function dragEvent(files: File[], types: string[] = ["Files"]) {
    const preventDefault = mock(() => {});
    const event = {
        preventDefault,
        dataTransfer: { types, files, dropEffect: "none" },
    } as unknown as DragEvent<HTMLDivElement>;
    return { event, preventDefault, transfer: event.dataTransfer };
}

function setup(options: { busy?: boolean; onCancelJob?: () => void } = {}) {
    let cancels = 0;
    const onCancelJob = options.onCancelJob ?? (() => (cancels += 1));
    const hook = renderHook(() => {
        const drafts = useModeDrafts();
        const file = useFileInput({
            busy: options.busy ?? false,
            dragging: drafts.state.dragging,
            inFileMode: drafts.state.selectedFile !== null,
            activeJob: drafts.state.activeJob,
            onCancelJob,
            update: drafts.update,
            resetResult: drafts.resetResult,
        });
        return { drafts, file };
    });
    return { hook, cancels: () => cancels };
}

beforeEach(() => {
    window.localStorage.clear();
});

describe("useFileInput — file selection", () => {
    it("reads a .txt client-side into the editable input", async () => {
        const { hook } = setup();
        const file = new File(["содержимое"], "doc.txt", { type: "text/plain" });

        await act(async () => {
            hook.result.current.file.onFileSelected(changeEvent([file]));
        });

        expect(hook.result.current.drafts.state.input).toBe("содержимое");
        expect(hook.result.current.drafts.state.textBaseName).toBe("doc");
        expect(hook.result.current.drafts.state.selectedFile).toBeNull();
    });

    it("reports a failed file read without leaking the underlying reason", async () => {
        const { hook } = setup();
        const file = new File(["x"], "doc.txt", { type: "text/plain" });
        Object.defineProperty(file, "text", {
            value: () => Promise.reject(new Error("DISK-MARKER")),
        });

        await act(async () => {
            hook.result.current.file.onFileSelected(changeEvent([file]));
        });
        await waitFor(() =>
            expect(hook.result.current.drafts.state.error).toBe(
                "Не удалось прочитать текстовый файл.",
            ),
        );
        expect(hook.result.current.drafts.state.error).not.toContain("DISK-MARKER");
    });

    it("selects a .docx for the backend and clears the text input", async () => {
        const { hook } = setup();
        act(() => hook.result.current.drafts.update({ input: "старый ввод" }));
        const file = new File(["x"], "doc.docx", { type: DOCX_MIME });

        await act(async () => {
            hook.result.current.file.onFileSelected(changeEvent([file]));
        });

        expect(hook.result.current.drafts.state.selectedFile).toBe(file);
        expect(hook.result.current.drafts.state.input).toBe("");
        expect(hook.result.current.drafts.state.textBaseName).toBeNull();
    });

    it("rejects an unsupported extension", async () => {
        const { hook } = setup();
        await act(async () => {
            hook.result.current.file.onFileSelected(
                changeEvent([new File(["x"], "doc.pdf", { type: "application/pdf" })]),
            );
        });
        expect(hook.result.current.drafts.state.error).toBe("Поддерживаются файлы .docx и .txt.");
        expect(hook.result.current.drafts.state.selectedFile).toBeNull();
    });

    it("loads a keys file and reports a failed keys read", async () => {
        const { hook } = setup();
        await act(async () => {
            hook.result.current.file.onKeysSelected(
                changeEvent([new File(["Физлица:\n  - VALUE-A"], "keys.yaml")]),
            );
        });
        expect(hook.result.current.drafts.state.demaskKeys).toContain("VALUE-A");
        expect(hook.result.current.drafts.state.inputTab).toBe("keys");

        const bad = new File(["x"], "keys.txt");
        await act(async () => {
            hook.result.current.file.onKeysSelected(changeEvent([bad]));
        });
        expect(hook.result.current.drafts.state.error).toBe(
            "Ключи демаскировки должны быть файлом .yaml.",
        );

        const unreadable = new File(["x"], "keys.yml");
        Object.defineProperty(unreadable, "text", {
            value: () => Promise.reject(new Error("DISK-MARKER")),
        });
        await act(async () => {
            hook.result.current.file.onKeysDrop(dragEvent([unreadable]).event);
        });
        await waitFor(() =>
            expect(hook.result.current.drafts.state.error).toBe(
                "Не удалось прочитать файл ключей.",
            ),
        );
        expect(hook.result.current.drafts.state.error).not.toContain("DISK-MARKER");
    });
});

describe("useFileInput — drag/drop", () => {
    it("tracks enter/leave depth and applies the dropped .docx", async () => {
        const { hook } = setup();
        const file = new File(["x"], "doc.docx", { type: DOCX_MIME });
        const enter = dragEvent([file]);
        const over = dragEvent([file]);
        const leave = dragEvent([file]);
        const drop = dragEvent([file]);

        act(() => hook.result.current.file.drag.onDragEnter(enter.event));
        expect(hook.result.current.drafts.state.dragging).toBe(true);
        expect(hook.result.current.file.drag.dragging).toBe(true);
        act(() => hook.result.current.file.drag.onDragOver(over.event));
        expect(over.transfer.dropEffect).toBe("copy");

        // A nested child fires another enter; one leave must not end the drag.
        const nested = dragEvent([file]);
        act(() => hook.result.current.file.drag.onDragEnter(nested.event));
        act(() => hook.result.current.file.drag.onDragLeave(leave.event));
        expect(hook.result.current.drafts.state.dragging).toBe(true);
        act(() => hook.result.current.file.drag.onDragLeave(leave.event));
        expect(hook.result.current.drafts.state.dragging).toBe(false);

        await act(async () => {
            hook.result.current.file.drag.onDrop(drop.event);
        });
        expect(drop.preventDefault).toHaveBeenCalled();
        expect(hook.result.current.drafts.state.selectedFile).toBe(file);
        expect(hook.result.current.drafts.state.dragging).toBe(false);
    });

    it("ignores dragged non-file data and drops while busy", async () => {
        const { hook } = setup({ busy: true });
        const file = new File(["x"], "doc.docx", { type: DOCX_MIME });
        const enter = dragEvent([file], ["text/plain"]);
        const drop = dragEvent([file]);

        act(() => hook.result.current.file.drag.onDragEnter(enter.event));
        expect(hook.result.current.drafts.state.dragging).toBe(false);
        await act(async () => {
            hook.result.current.file.drag.onDrop(drop.event);
        });
        expect(hook.result.current.drafts.state.selectedFile).toBeNull();
    });
});

describe("useFileInput — file action buttons", () => {
    it("shares one implementation between the masking and demasking data buttons", async () => {
        const { hook, cancels } = setup();
        expect(hook.result.current.file.onMaskFileActionPress).toBe(
            hook.result.current.file.onDataFileActionPress,
        );

        const click = mock(() => {});
        hook.result.current.file.fileInputRef.current = {
            value: "x",
            click,
        } as unknown as HTMLInputElement;
        act(() => hook.result.current.file.onMaskFileActionPress());
        expect(click).toHaveBeenCalledTimes(1);
        expect(cancels()).toBe(0);

        const file = new File(["x"], "doc.docx", { type: DOCX_MIME });
        await act(async () => {
            hook.result.current.file.onFileSelected(changeEvent([file]));
        });
        act(() => hook.result.current.file.onDataFileActionPress());
        expect(hook.result.current.drafts.state.selectedFile).toBeNull();
        expect(hook.result.current.file.fileInputRef.current?.value).toBe("");
    });

    it("cancels the active job instead of touching the file input", async () => {
        const { hook, cancels } = setup();
        act(() => hook.result.current.drafts.update({ activeJob: ACCEPTED }));
        const click = mock(() => {});
        hook.result.current.file.fileInputRef.current = {
            value: "",
            click,
        } as unknown as HTMLInputElement;

        act(() => hook.result.current.file.onMaskFileActionPress());
        expect(cancels()).toBe(1);
        expect(click).not.toHaveBeenCalled();
    });

    it("the keys button only opens the keys picker", () => {
        const { hook } = setup();
        const click = mock(() => {});
        hook.result.current.file.keysInputRef.current = {
            click,
        } as unknown as HTMLInputElement;
        act(() => hook.result.current.file.onKeysFileActionPress());
        expect(click).toHaveBeenCalledTimes(1);
    });
});
