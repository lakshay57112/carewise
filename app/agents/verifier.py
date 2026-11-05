"""Verifier agent: a second pair of eyes on the extraction.

It never trusts the reader. Every medicine, dose and date has to be findable in the
original text, and anything unclear is handed back to the person to confirm.
"""
import re

from ..models import Discharge, Flag


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def run(data: Discharge, source: str) -> tuple[list[Flag], dict]:
    flags: list[Flag] = []
    checks = 0
    passed = 0
    src = _norm(source)

    def check(ok: bool, flag: Flag | None = None) -> None:
        nonlocal checks, passed
        checks += 1
        if ok:
            passed += 1
        elif flag:
            flags.append(flag)

    if not data.medicines:
        flags.append(Flag(id="meds-none", field="medicines", value="", issue="No medicines were found."))

    seen: set[str] = set()
    for i, med in enumerate(data.medicines):
        label = f"{med.name} {med.dose}".strip()
        check(_norm(med.name) in src, Flag(id=f"m{i}-name", field="medicine", value=label,
                                           issue="This name does not appear in the document."))
        check(bool(med.dose) and _norm(med.dose) in src,
              Flag(id=f"m{i}-dose", field="dose", value=label, issue="Could not match this dose in the document."))
        check(med.times_per_day > 0 or bool(med.timing),
              Flag(id=f"m{i}-freq", field="how often", value=label, issue="It is not clear how often to take this."))
        check(med.duration_days is not None,
              Flag(id=f"m{i}-days", field="duration", value=label, issue="No number of days was found."))
        key = _norm(med.name)
        check(key not in seen, Flag(id=f"m{i}-dup", field="medicine", value=label, issue="Listed more than once."))
        seen.add(key)

    if data.follow_up.date:
        check(_norm(data.follow_up.date) in src,
              Flag(id="fu-date", field="follow-up", value=data.follow_up.date,
                   issue="This date does not appear in the document."))
    else:
        check(False, Flag(id="fu-none", field="follow-up", value="", issue="No follow-up date was found."))

    check(bool(data.warning_signs),
          Flag(id="warn-none", field="warning signs", value="",
               issue="No 'return to hospital if' signs were found, so symptom check-ins can't compare against them."))

    confidence = round(passed / checks, 2) if checks else 0.0
    return flags, {"checks": checks, "passed": passed, "confidence": confidence}
