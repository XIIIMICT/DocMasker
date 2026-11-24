"""
Detector registry: the mapping between the single masking engine and detection code.

A `Detector` binds a stable id and a label to one provider method of
`RuleBasedMasker`. Adding a label means writing one `_find_*` provider on the
masker and registering it here once — the single engine then runs it. The
registry stores method *names* (resolved with `getattr` at run time), so this
module stays import-light and avoids a circular import with `rule_based`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .rule_based import Entity, RuleBasedMasker


@dataclass(frozen=True, slots=True)
class Detector:
    """One detection provider: a stable id, the label it reports, its method."""

    detector_id: str
    label: str
    method: str

    def run(self, masker: RuleBasedMasker, text: str) -> Iterable[Entity]:
        """Invoke the provider on `text` (the masker supplies natasha state)."""
        provider: Any = getattr(masker, self.method)
        return provider(text)


#: Every detector the pipeline knows, keyed by stable id.
DETECTORS: dict[str, Detector] = {
    "email": Detector(detector_id="email", label="email", method="_find_emails"),
    "link": Detector(detector_id="link", label="link", method="_find_links"),
    "phone": Detector(detector_id="phone", label="phone", method="_find_phones"),
    "person": Detector(detector_id="person", label="person", method="_find_persons"),
    "address": Detector(detector_id="address", label="address", method="_find_addresses"),
    "organization": Detector(detector_id="organization", label="organization", method="_find_organizations"),
}
