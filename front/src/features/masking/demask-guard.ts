/**
 * The single condition under which demasking may start.
 *
 * Both the button state and the operation handler use it: a visually disabled
 * button is not a security or correctness boundary, because Enter/Space and a
 * direct handler call must be blocked by the same rule as a mouse click.
 */
export type DemaskGuardInput = {
    busy: boolean;
    hasText: boolean;
    hasFile: boolean;
    hasKeys: boolean;
    keysError: string | null;
};

export function canDemask(input: DemaskGuardInput): boolean {
    return (
        !input.busy && (input.hasText || input.hasFile) && input.hasKeys && input.keysError === null
    );
}
