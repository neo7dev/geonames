/** DialogContent props that keep the dialog open: no X, and Escape or a click outside does nothing. */
export const stayOpen = {
  showCloseButton: false,
  onInteractOutside: (e: Event) => e.preventDefault(),
  onEscapeKeyDown: (e: Event) => e.preventDefault(),
}
