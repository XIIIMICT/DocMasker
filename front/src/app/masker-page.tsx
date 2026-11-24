"use client";

import { ACCEPTED_EXTENSIONS } from "@/features/masking/types";
import { useMaskerWorkflow } from "@/features/masking/use-masker-workflow";
import { Brand } from "@/shared/ui/brand";
import {
    DemaskInputPane,
    MaskInputPane,
    MaskResultPane,
    UnmaskResultPane,
} from "@/shared/ui/panes";
import { SupportButton } from "@/shared/ui/support-button";
import { ThemeSwitcher } from "@/shared/ui/theme-switcher";

export function MaskerPage({ supportMail }: { supportMail: string }) {
    const w = useMaskerWorkflow();

    return (
        <div className="app-shell">
            <header className="app-header">
                <Brand mode={w.mode} onModeChange={w.onModeChange} />
                <div className="app-header__end">
                    <SupportButton supportMail={supportMail} variant="tertiary" />
                    <ThemeSwitcher />
                </div>
            </header>

            <main className="masker-body">
                <div className="editor-grid">
                    {w.inMaskMode ? (
                        <MaskInputPane
                            input={w.input}
                            inputTextRef={w.inputTextRef}
                            sourceDocRef={w.sourceDocRef}
                            selectedFile={w.selectedFile}
                            inFileMode={w.inFileMode}
                            busy={w.busy}
                            maskEnabled={w.maskEnabled}
                            activeJob={w.activeJob !== null}
                            drag={w.drag}
                            onTextChange={w.onTextChange}
                            onFileActionPress={w.onMaskFileActionPress}
                            onMask={w.handleMask}
                        />
                    ) : (
                        <DemaskInputPane
                            input={w.input}
                            inputTextRef={w.inputTextRef}
                            sourceDocRef={w.sourceDocRef}
                            selectedFile={w.selectedFile}
                            inFileMode={w.inFileMode}
                            busy={w.busy}
                            unmaskEnabled={w.unmaskEnabled}
                            activeJob={w.activeJob !== null}
                            keysInvalid={w.keysInvalid}
                            demaskKeys={w.demaskKeys}
                            inputTab={w.inputTab}
                            demaskStats={w.demaskStats}
                            drag={w.drag}
                            onTextChange={w.onTextChange}
                            onDemaskKeysChange={(value) => w.update({ demaskKeys: value })}
                            onKeysDrop={w.onKeysDrop}
                            onInputTabChange={(key) => w.update({ inputTab: key })}
                            onDataFilePress={w.onDataFileActionPress}
                            onKeysFilePress={w.onKeysFileActionPress}
                            onUnmask={w.handleUnmask}
                        />
                    )}

                    <div className="editor-pane">
                        {w.inMaskMode ? (
                            <MaskResultPane
                                error={w.error}
                                resultTab={w.resultTab}
                                onResultTabChange={(key) => w.update({ resultTab: key })}
                                stats={w.stats}
                                keys={w.keys}
                                maskedNothing={w.maskedNothing}
                                showResultPreview={w.showResultPreview}
                                maskedFile={w.maskedFile}
                                inFileMode={w.inFileMode}
                                result={w.result}
                                resultTextRef={w.resultTextRef}
                                maskedDocRef={w.maskedDocRef}
                                canDownloadResult={w.canDownloadResult}
                                canDownloadKeys={w.canDownloadKeys}
                                onDownloadResult={w.downloadResult}
                                onDownloadKeys={w.downloadKeys}
                            />
                        ) : (
                            <UnmaskResultPane
                                error={w.error}
                                restored={w.restored}
                                restoredFile={w.restoredFile}
                                restoredDocRef={w.restoredDocRef}
                                demaskKeys={w.demaskKeys}
                                inFileMode={w.inFileMode}
                                canDownload={w.canDownloadRestored}
                                onDownload={w.downloadRestored}
                            />
                        )}
                    </div>
                </div>

                <input
                    ref={w.fileInputRef}
                    type="file"
                    accept={ACCEPTED_EXTENSIONS.join(",")}
                    hidden
                    onChange={w.onFileSelected}
                />
                <input
                    ref={w.keysInputRef}
                    type="file"
                    accept=".yaml,.yml"
                    hidden
                    onChange={w.onKeysSelected}
                />
            </main>
        </div>
    );
}
