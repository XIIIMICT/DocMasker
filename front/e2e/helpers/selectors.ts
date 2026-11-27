/**
 * The browser-side file inputs are visually hidden behind accessible buttons,
 * so `setInputFiles` must target them by their stable `accept` contract. Keep
 * the selector in one place: duplicating the raw CSS made drift easy and gave
 * the false impression that an inaccessible control was being tested.
 */
export const DATA_FILE_INPUT = 'input[type="file"][accept*=".docx"]';
