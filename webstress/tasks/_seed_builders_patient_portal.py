"""Composable seed builder framework for the Patient Portal environment.

Provides :class:`PatientPortalSeedContext` and a registry of builder
functions that generate deterministic healthcare test data.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from webstress.backend.models.patient_portal import (
    Appointment,
    ClinicalMessage,
    EmergencyContact,
    Immunization,
    InsuranceClaim,
    InsurancePlan,
    LabResult,
    Pharmacy,
    Prescription,
    Provider,
    Referral,
    ScreeningRecommendation,
    SlotInfo,
)


# ---------------------------------------------------------------------------
# ResolvedActor (shared shape with Gmail / Robinhood)
# ---------------------------------------------------------------------------

@dataclass
class ResolvedActor:
    """A named person with a deterministically-generated email address."""

    name: str
    email: str
    first_name: str


# ---------------------------------------------------------------------------
# Hardcoded provider name templates by specialty
# ---------------------------------------------------------------------------

_PROVIDER_NAMES: dict[str, list[str]] = {
    "pcp": [
        "Dr. Sarah Mitchell", "Dr. David Chen", "Dr. Lisa Patel",
        "Dr. James Rivera", "Dr. Emily Brooks",
    ],
    "cardiology": [
        "Dr. Robert Kim", "Dr. Ana Rodriguez", "Dr. Michael Torres",
        "Dr. Patricia Nguyen", "Dr. Steven Wright",
    ],
    "endocrinology": [
        "Dr. Karen Singh", "Dr. Thomas Garcia", "Dr. Maria Lopez",
        "Dr. Brian Morris", "Dr. Jennifer Adams",
    ],
    "dermatology": [
        "Dr. Sandra Lee", "Dr. Andrew Park", "Dr. Rachel Green",
        "Dr. Kevin Pham", "Dr. Laura Martinez",
    ],
    "orthopedics": [
        "Dr. William Clark", "Dr. Diana Flores", "Dr. Mark Sullivan",
        "Dr. Christine Yang", "Dr. Peter Walsh",
    ],
    "neurology": [
        "Dr. Helen Cho", "Dr. Daniel Murphy", "Dr. Samantha Price",
        "Dr. Richard Tanaka", "Dr. Olivia Bennett",
    ],
    "radiology": [
        "Dr. Paul Hoffman", "Dr. Natalie Russo", "Dr. Gregory Lin",
        "Dr. Catherine Stone", "Dr. Derek Foster",
    ],
    "phlebotomy": [
        "Outpatient Lab Services", "Clinical Laboratory",
        "Diagnostic Lab Center", "Pre-Op Lab Suite",
    ],
    "billing": [
        "Billing Department", "Claims Office", "Patient Accounts",
    ],
    "admin": [
        "Front Desk", "Patient Services", "Medical Records",
    ],
}

_SPECIALTY_DEPARTMENTS: dict[str, str] = {
    "pcp": "Primary Care",
    "cardiology": "Cardiology",
    "endocrinology": "Endocrinology",
    "dermatology": "Dermatology",
    "orthopedics": "Orthopedics",
    "neurology": "Neurology",
    "radiology": "Radiology",
    "phlebotomy": "Laboratory",
    "billing": "Billing",
    "admin": "Administration",
}

# ---------------------------------------------------------------------------
# Screening pools
# ---------------------------------------------------------------------------

_SCREENING_ALL: list[dict[str, Any]] = [
    {"name": "Colonoscopy", "min_age": 45, "frequency": "every 10 years"},
    {"name": "Lipid Panel", "min_age": 20, "frequency": "every 5 years"},
    {"name": "Blood Pressure Screening", "min_age": 18, "frequency": "annually"},
    {"name": "Diabetes Screening", "min_age": 35, "frequency": "every 3 years"},
    {"name": "Lung Cancer Screening", "min_age": 50, "frequency": "annually"},
]

_SCREENING_FEMALE: list[dict[str, Any]] = [
    {"name": "Mammogram", "min_age": 40, "frequency": "every 2 years"},
    {"name": "Cervical Cancer Screening", "min_age": 21, "frequency": "every 3 years"},
    {"name": "Bone Density Scan", "min_age": 65, "frequency": "every 2 years"},
]

# ---------------------------------------------------------------------------
# Medication pool
# ---------------------------------------------------------------------------

_MEDICATIONS: list[dict[str, Any]] = [
    {"name": "Lisinopril 10mg", "dosage": "10mg", "frequency": "once daily"},
    {"name": "Metformin 500mg", "dosage": "500mg", "frequency": "twice daily"},
    {"name": "Atorvastatin 20mg", "dosage": "20mg", "frequency": "once daily at bedtime"},
    {"name": "Amlodipine 5mg", "dosage": "5mg", "frequency": "once daily"},
    {"name": "Losartan 50mg", "dosage": "50mg", "frequency": "once daily"},
    {"name": "Warfarin 5mg", "dosage": "5mg", "frequency": "once daily"},
    {"name": "Omeprazole 20mg", "dosage": "20mg", "frequency": "once daily before breakfast"},
    {"name": "Levothyroxine 75mcg", "dosage": "75mcg", "frequency": "once daily on empty stomach"},
    {"name": "Gabapentin 300mg", "dosage": "300mg", "frequency": "three times daily"},
    {"name": "Sertraline 50mg", "dosage": "50mg", "frequency": "once daily"},
]

# Known drug interaction pairs
_INTERACTION_PAIRS: list[tuple[str, str]] = [
    ("Warfarin 5mg", "Atorvastatin 20mg"),
    ("Lisinopril 10mg", "Losartan 50mg"),
    ("Metformin 500mg", "Gabapentin 300mg"),
]

# ---------------------------------------------------------------------------
# Lab test pool
# ---------------------------------------------------------------------------

_LAB_TESTS: list[dict[str, Any]] = [
    {"name": "HbA1c", "code": "4548-4", "unit": "%", "ref": "4.0-5.6", "normal": "5.2", "abnormal": "7.1", "critical": "10.5"},
    {"name": "LDL Cholesterol", "code": "2089-1", "unit": "mg/dL", "ref": "0-130", "normal": "110", "abnormal": "155", "critical": "220"},
    {"name": "HDL Cholesterol", "code": "2085-9", "unit": "mg/dL", "ref": "40-60", "normal": "52", "abnormal": "32", "critical": "22"},
    {"name": "Triglycerides", "code": "2571-8", "unit": "mg/dL", "ref": "0-150", "normal": "120", "abnormal": "210", "critical": "550"},
    {"name": "Total Cholesterol", "code": "2093-3", "unit": "mg/dL", "ref": "0-200", "normal": "180", "abnormal": "245", "critical": "320"},
    {"name": "TSH", "code": "3016-3", "unit": "mIU/L", "ref": "0.4-4.0", "normal": "2.1", "abnormal": "6.8", "critical": "15.0"},
    {"name": "Creatinine", "code": "2160-0", "unit": "mg/dL", "ref": "0.6-1.2", "normal": "0.9", "abnormal": "1.8", "critical": "4.5"},
    {"name": "Glucose Fasting", "code": "1558-6", "unit": "mg/dL", "ref": "70-100", "normal": "88", "abnormal": "135", "critical": "350"},
    {"name": "INR", "code": "6301-6", "unit": "", "ref": "0.8-1.2", "normal": "1.0", "abnormal": "2.8", "critical": "5.0"},
    {"name": "CBC WBC", "code": "6690-2", "unit": "10^3/uL", "ref": "4.5-11.0", "normal": "7.2", "abnormal": "14.5", "critical": "25.0"},
]

_LIPID_PANEL_COMPONENTS = ["LDL Cholesterol", "HDL Cholesterol", "Triglycerides", "Total Cholesterol"]

# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------

_CLINICAL_SUBJECTS: list[str] = [
    "Follow-up on recent lab results",
    "Medication adjustment recommendation",
    "Appointment reminder",
    "Test results available",
    "Care plan update",
]

_BILLING_SUBJECTS: list[str] = [
    "Statement for recent visit",
    "Insurance claim update",
    "Outstanding balance notification",
]

_RX_RENEWAL_SUBJECTS: list[str] = [
    "Prescription renewal request",
    "Refill authorization needed",
    "Medication renewal due",
]

_BODY_CONTEXT_SUBJECTS: dict[str, str] = {
    "discharge_summary": "Discharge Summary",
    "formulary_info": "Formulary Coverage Update",
    "generic_alternative": "Generic Alternative Recommendation",
    "bp_medication_adjustment": "Blood Pressure Medication Adjustment",
    "referral_details": "Specialist Referral Information",
}

# Denial reason pool for EOB claims
_EOB_DENIAL_REASONS: list[str] = [
    "Service not medically necessary",
    "Out-of-network provider",
    "Missing prior authorization",
    "Duplicate claim submission",
    "Procedure not covered under plan",
]

# ---------------------------------------------------------------------------
# Vaccine pool
# ---------------------------------------------------------------------------

_VACCINES: list[dict[str, Any]] = [
    # `short_name` is used by canonical_diff predicates that try to match the
    # vaccine name in an appointment reason. The full `name` includes a
    # parenthetical (e.g. "Tdap (Tetanus)") which agents rarely repeat
    # verbatim — the short form is the bare form an agent is likely to type
    # ("Tdap", "Flu"), and is what task evaluators should match against.
    {"name": "Influenza (Flu)", "short_name": "Influenza", "series": False, "annual": True},
    {"name": "COVID-19 Booster", "short_name": "COVID-19", "series": False, "annual": True},
    {"name": "Tdap (Tetanus)", "short_name": "Tdap", "series": False, "annual": False, "interval_years": 10},
    {"name": "Shingles (Shingrix)", "short_name": "Shingles", "series": True, "doses": 2, "interval_months": 2},
    {"name": "Hepatitis B", "short_name": "Hepatitis B", "series": True, "doses": 3, "interval_months": 1},
    {"name": "Pneumococcal (PCV20)", "short_name": "Pneumococcal", "series": False, "annual": False, "min_age": 65},
    {"name": "HPV", "short_name": "HPV", "series": True, "doses": 3, "interval_months": 2, "max_age": 45},
]

# ---------------------------------------------------------------------------
# Pharmacy pool
# ---------------------------------------------------------------------------

_PHARMACY_TEMPLATES: list[dict[str, str]] = [
    {"name": "CVS Pharmacy #4821", "address": "1200 Market St, Springfield, IL 62701", "phone": "(555) 234-5678"},
    {"name": "Walgreens #09832", "address": "450 Oak Ave, Springfield, IL 62702", "phone": "(555) 345-6789"},
    {"name": "CVS Pharmacy #4833", "address": "890 Pine Blvd, Springfield, IL 62703", "phone": "(555) 456-7890"},
    {"name": "Rite Aid #1155", "address": "320 Elm St, Springfield, IL 62704", "phone": "(555) 567-8901"},
]

_MAIL_ORDER_PHARMACY: dict[str, str] = {
    "name": "Express Scripts Mail Order",
    "address": "PO Box 21100, Tempe, AZ 85285",
    "phone": "(800) 555-1234",
}


# ---------------------------------------------------------------------------
# PatientPortalSeedContext
# ---------------------------------------------------------------------------

class PatientPortalSeedContext:
    """Mutable accumulator threaded through every Patient Portal builder step."""

    def __init__(
        self,
        seed: int,
        rng: random.Random,
        fake: Any,
        now: datetime,
        base: dict[str, Any],
    ) -> None:
        self.seed = seed
        self.rng = rng
        self.fake = fake
        self.now = now
        self.base = base
        self.actors: dict[str, ResolvedActor] = {}
        self.outputs: dict[str, Any] = {}
        self.counters: dict[str, int] = {}

    def next_id(self, prefix: str) -> str:
        """Return a monotonically increasing id like ``prov_1``."""
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}_{self.counters[prefix]}"

    def get_provider_by_specialty(self, specialty: str) -> dict | None:
        """Return the first provider dict matching *specialty*, or None."""
        for prov in self.base.get("providers", []):
            if prov.get("specialty") == specialty:
                return prov
        return None

    def get_pcp(self) -> dict:
        """Return the PCP provider dict.  Raises if none found."""
        pcp_id = self.base.get("patient", {}).get("pcp_id")
        if pcp_id:
            for prov in self.base.get("providers", []):
                if prov.get("id") == pcp_id:
                    return prov
        raise ValueError("No PCP found in base state")

    def email_for_name(self, name: str, domain: str = "thornton.com") -> str:
        local = "".join(
            ch.lower() for ch in name if ch.isalnum() or ch == " "
        ).replace(" ", ".")
        local = ".".join(part for part in local.split(".") if part) or "contact"
        return f"{local}@{domain}"

    def resolve_actor(
        self,
        key: str,
        domain: str = "thornton.com",
        name: str | None = None,
        is_vip: bool = False,
    ) -> ResolvedActor:
        """Generate a deterministic actor and cache it under *key*."""
        if key in self.actors:
            return self.actors[key]
        if name is None:
            name = self.fake.name()
        first_name = name.split()[0]
        email = self.email_for_name(name, domain)
        actor = ResolvedActor(name=name, email=email, first_name=first_name)
        self.actors[key] = actor
        return actor


# ---------------------------------------------------------------------------
# Builder registry
# ---------------------------------------------------------------------------

BuilderFn = Callable[["PatientPortalSeedContext", dict[str, Any]], dict[str, Any]]

PATIENT_PORTAL_BUILDER_REGISTRY: dict[str, BuilderFn] = {}


def _register(name: str) -> Callable[[BuilderFn], BuilderFn]:
    def decorator(fn: BuilderFn) -> BuilderFn:
        PATIENT_PORTAL_BUILDER_REGISTRY[name] = fn
        return fn
    return decorator


# ---------------------------------------------------------------------------
# 1. patient_profile
# ---------------------------------------------------------------------------

_INSURANCE_TIERS: dict[str, dict[str, Any]] = {
    "basic": {"copay": Decimal("50"), "deductible": Decimal("5000"), "plan_prefix": "Bronze"},
    "standard": {"copay": Decimal("30"), "deductible": Decimal("2000"), "plan_prefix": "Silver"},
    "premium": {"copay": Decimal("15"), "deductible": Decimal("500"), "plan_prefix": "Gold"},
}


@_register("patient_profile")
def build_patient_profile(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create the patient singleton, insurance, emergency contact, and PCP assignment.

    Params: allergies (list[str]), conditions (list[str]), insurance_tier (str),
            overdue_screening_count (int) — force this many screenings to be overdue
    Outputs: patient_name, pcp_id, pcp_name, insurance_plan_name, member_id,
             group_number, conditions_list, allergies_list, applicable_screening_names,
             overdue_screening_names (the subset whose next_due has already passed)
    """
    allergies = params.get("allergies", [])
    conditions = params.get("conditions", [])
    tier_key = params.get("insurance_tier", "standard")
    tier = _INSURANCE_TIERS.get(tier_key, _INSURANCE_TIERS["standard"])
    overdue_screening_count: int = params.get("overdue_screening_count", 0)

    # Generate patient demographics
    patient_name = ctx.fake.name()
    sex = ctx.rng.choice(["male", "female"])
    # Age between 25 and 75
    age = ctx.rng.randint(25, 75)
    dob = date(ctx.now.year - age, ctx.rng.randint(1, 12), ctx.rng.randint(1, 28))
    phone = f"(555) {ctx.rng.randint(100, 999)}-{ctx.rng.randint(1000, 9999)}"
    email = ctx.email_for_name(patient_name)

    # Emergency contact
    ec_name = ctx.fake.name()
    ec_phone = f"(555) {ctx.rng.randint(100, 999)}-{ctx.rng.randint(1000, 9999)}"
    ec_rel = ctx.rng.choice(["Spouse", "Parent", "Sibling", "Child", "Friend"])

    # Insurance plan
    plan_name = f"{tier['plan_prefix']} {ctx.rng.choice(['PPO', 'HMO', 'EPO'])} Plan"
    member_id = f"MBR-{ctx.rng.randint(1000000, 9999999)}"
    group_number = f"GRP-{ctx.rng.randint(10000, 99999)}"
    deductible_met = Decimal(str(ctx.rng.randint(0, int(tier['deductible']))))

    # PCP assignment -- the PCP provider will be created by provider_directory,
    # but we reserve the ID here for cross-reference.
    pcp_id = "prov_1"

    # Build applicable screenings based on age and sex
    eligible: list[dict[str, Any]] = []
    for s in _SCREENING_ALL:
        if age >= s["min_age"]:
            eligible.append(s)
    if sex == "female":
        for s in _SCREENING_FEMALE:
            if age >= s["min_age"]:
                eligible.append(s)

    # Pick 3-5 from eligible
    num_screenings = min(len(eligible), ctx.rng.randint(3, 5))
    ctx.rng.shuffle(eligible)
    selected_screenings = eligible[:num_screenings]

    screening_models: list[dict[str, Any]] = []
    overdue_forced = 0
    for idx, s in enumerate(selected_screenings):
        # Force overdue for the first `overdue_screening_count` screenings
        force_overdue = overdue_forced < overdue_screening_count
        if force_overdue:
            # Make last_completed far enough in the past that next_due has already passed
            freq_years = _parse_frequency_years(s["frequency"])
            years_ago = freq_years + ctx.rng.randint(1, 2)
            last_completed = date(ctx.now.year - years_ago, ctx.rng.randint(1, 12), ctx.rng.randint(1, 28))
            next_due = date(last_completed.year + freq_years, last_completed.month, last_completed.day)
            overdue_forced += 1
        elif ctx.rng.random() > 0.3:
            # Random last_completed in the past 0-5 years (some may be None)
            years_ago = ctx.rng.randint(0, 5)
            last_completed = date(ctx.now.year - years_ago, ctx.rng.randint(1, 12), ctx.rng.randint(1, 28))
            # Compute next_due based on frequency
            freq_years = _parse_frequency_years(s["frequency"])
            next_due = date(last_completed.year + freq_years, last_completed.month, last_completed.day)
        else:
            last_completed = None
            next_due = date(ctx.now.year, ctx.rng.randint(1, 12), ctx.rng.randint(1, 28))

        screening_models.append({
            "screening_name": s["name"],
            "recommended_age_start": s["min_age"],
            "frequency": s["frequency"],
            "last_completed": last_completed.isoformat() if last_completed else None,
            "next_due": next_due.isoformat() if next_due else None,
        })

    patient_dict = {
        "id": "patient_1",
        "name": patient_name,
        "sex": sex,
        "dob": dob.isoformat(),
        "phone": phone,
        "email": email,
        "insurance_plan": {
            "plan_name": plan_name,
            "member_id": member_id,
            "group_number": group_number,
            "copay": str(tier["copay"]),
            "deductible": str(tier["deductible"]),
            "deductible_met": str(deductible_met),
        },
        "pcp_id": pcp_id,
        "allergies": allergies,
        "conditions": conditions,
        "pharmacy_ids": [],
        "emergency_contact": {
            "name": ec_name,
            "phone": ec_phone,
            "relationship": ec_rel,
        },
        "applicable_screenings": screening_models,
    }

    ctx.base["patient"] = patient_dict
    # B-1: opt-in confirmation workflow. Specialties listed here trigger the
    # create_appointment route to land new appointments in
    # confirmation_state="pending" so the agent must complete a two-step
    # schedule+confirm workflow. Empty list = legacy behavior.
    if "auto_confirm_specialties" in params:
        ctx.base["auto_confirm_specialties"] = list(params["auto_confirm_specialties"] or [])

    # Compute the overdue subset — every screening whose next_due has already
    # passed relative to the seeded clock. Consumers (e.g. the
    # pp_preventive_screening_review canonical_diff) need this as a target
    # so a bijection can create one appointment per overdue screening
    # without reconstructing the filter inside a predicate (Class 8
    # comprehension-scope hazard).
    _today = ctx.now.date()
    overdue_screening_names = [
        s["screening_name"]
        for s in screening_models
        if s["next_due"] is not None and date.fromisoformat(s["next_due"]) < _today
    ]

    return {
        "patient_name": patient_name,
        "pcp_id": pcp_id,
        "pcp_name": "",  # Will be filled by provider_directory
        "insurance_plan_name": plan_name,
        "member_id": member_id,
        "group_number": group_number,
        "conditions_list": conditions,
        "allergies_list": allergies,
        "applicable_screening_names": [s["screening_name"] for s in screening_models],
        "overdue_screening_names": overdue_screening_names,
    }


def _parse_frequency_years(freq: str) -> int:
    """Parse a screening frequency string into years."""
    if "10 years" in freq:
        return 10
    if "5 years" in freq:
        return 5
    if "3 years" in freq:
        return 3
    if "2 years" in freq:
        return 2
    return 1  # annually


# ---------------------------------------------------------------------------
# 2. provider_directory
# ---------------------------------------------------------------------------

@_register("provider_directory")
def build_provider_directory(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create N providers across requested specialties with realistic available slots.

    Params:
      specialties (list[str]): one provider of each listed specialty by default.
      count_per_specialty (dict[str, int]): override the default count-of-1
        for specific specialties. E.g. {"pcp": 2} creates two distinct PCPs.
        Required when tasks need bijection identity tests across providers of
        the same specialty (e.g. immunizations administered by different PCPs).
      must_include (list[str]): specialties that must be present; merged with
        `specialties` with duplicates collapsed.
      force_in_person_specialties (list[str]): for each listed specialty, every
        provider of that specialty is guaranteed at least one in-person slot.
        If a provider's randomly-generated slots are all telehealth, the
        earliest slot's type is flipped to "in-person". This is applied AFTER
        all RNG slot draws (so it does not perturb the deterministic seed
        stream of other tasks) and only activates when the param is supplied —
        existing tasks are unaffected. Required by tasks whose canonical
        answer is "the earliest in-person slot of provider X", to keep that
        answer guaranteed to exist across every seed.
    Outputs: provider_ids, providers_by_specialty
    """
    specialties = params.get("specialties", ["pcp"])
    count_per_specialty = params.get("count_per_specialty", {}) or {}
    must_include = set(params.get("must_include", []))
    force_in_person_specialties = set(params.get("force_in_person_specialties", []) or [])
    # Merge specialties + must_include, deduped. count_per_specialty controls
    # how many providers of each specialty are created (default 1).
    all_specialties = list(dict.fromkeys(specialties + list(must_include)))

    if "providers" not in ctx.base:
        ctx.base["providers"] = []

    provider_ids: list[str] = []
    providers_by_specialty: dict[str, list[str]] = {}

    # Track which names have already been used per specialty so multiple
    # providers of the same specialty don't collide on name.
    used_names_per_spec: dict[str, set[str]] = {}

    for base_spec in all_specialties:
        n_of_this = max(1, int(count_per_specialty.get(base_spec, 1)))
        for _copy_idx in range(n_of_this):
            spec = base_spec
            names_pool = list(_PROVIDER_NAMES.get(spec, [f"Dr. {ctx.fake.name()}"]))
            used = used_names_per_spec.setdefault(spec, set())
            available_names = [n for n in names_pool if n not in used]
            if not available_names:
                # Exhausted pool — generate a unique synthetic name.
                available_names = [f"Dr. {ctx.fake.name()}"]
            dept = _SPECIALTY_DEPARTMENTS.get(spec, spec.title())

            # For PCP, always use prov_1 to match patient.pcp_id (first PCP only)
            if spec == "pcp" and not any(p.get("id") == "prov_1" for p in ctx.base["providers"]):
                prov_id = "prov_1"
                ctx.counters["prov"] = max(ctx.counters.get("prov", 0), 1)
            else:
                prov_id = ctx.next_id("prov")

            prov_name = ctx.rng.choice(available_names)
            used.add(prov_name)
            accepting = spec not in ("billing", "admin")
            npi = f"{ctx.rng.randint(1000000000, 9999999999)}"

            # Generate 3-6 available slots over the next 2 weeks
            num_slots = ctx.rng.randint(3, 6)
            slots: list[dict[str, Any]] = []
            for _ in range(num_slots):
                days_ahead = ctx.rng.randint(1, 14)
                hour = ctx.rng.randint(9, 16)
                slot_dt = ctx.now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
                slot_type = ctx.rng.choice(["in-person", "telehealth"])
                slots.append({
                    "datetime": slot_dt.isoformat(),
                    "type": slot_type,
                    "duration_minutes": 30,
                })
            # Sort slots by datetime
            slots.sort(key=lambda s: s["datetime"])

            # Guarantee at least one in-person slot for opted-in specialties.
            # Applied after all RNG draws so the deterministic seed stream is
            # unchanged for tasks that do not request this. If no slot is
            # in-person, flip the earliest slot to in-person (a stable, fully
            # deterministic choice). No-op when an in-person slot already
            # exists or the specialty is not opted in.
            if spec in force_in_person_specialties and slots and not any(
                s["type"] == "in-person" for s in slots
            ):
                slots[0]["type"] = "in-person"

            prov_dict = {
                "id": prov_id,
                "name": prov_name,
                "specialty": spec,
                "department": dept,
                "npi": npi,
                "accepting_new": accepting,
                "available_slots": slots,
            }
            ctx.base["providers"].append(prov_dict)
            provider_ids.append(prov_id)
            providers_by_specialty.setdefault(spec, []).append(prov_id)

    # Update PCP name in outputs if we created a PCP
    if "pcp" in providers_by_specialty:
        pcp_prov = next(
            (p for p in ctx.base["providers"] if p["id"] == "prov_1"), None
        )
        if pcp_prov:
            ctx.outputs["pcp_name"] = pcp_prov["name"]

    # Derived: for each specialty, the globally-earliest available slot across
    # ALL providers of that specialty. Tasks that ask the agent to book "the
    # next available <specialty> slot" when there are MULTIPLE providers of
    # that specialty need a scalar answer that disambiguates BOTH the datetime
    # AND the owning provider — comparing only `x in providers_by_specialty[s]`
    # plus a min-over-slots expr leaves a false-positive hole (an agent could
    # name the earliest datetime but the wrong sibling provider). Exposing the
    # owning provider id as a scalar lets the canonical_diff pin it exactly.
    # Slot datetimes are unique within a specialty (random day/hour over 14
    # days), so the (datetime, provider_id) pair is deterministic; ties break
    # on provider_id ascending for total determinism.
    min_slot_by_specialty: dict[str, dict[str, str]] = {}
    earliest_slot_provider_by_specialty: dict[str, str] = {}
    earliest_slot_datetime_by_specialty: dict[str, str] = {}
    for spec, ids in providers_by_specialty.items():
        candidates: list[tuple[str, str]] = []
        for pid in ids:
            prov = next((p for p in ctx.base["providers"] if p["id"] == pid), None)
            if prov is None:
                continue
            for slot in prov.get("available_slots", []):
                candidates.append((slot["datetime"], pid))
        if not candidates:
            continue
        candidates.sort(key=lambda c: (c[0], c[1]))
        best_dt, best_pid = candidates[0]
        min_slot_by_specialty[spec] = {"datetime": best_dt, "provider_id": best_pid}
        earliest_slot_provider_by_specialty[spec] = best_pid
        earliest_slot_datetime_by_specialty[spec] = best_dt

    return {
        "provider_ids": provider_ids,
        "providers_by_specialty": providers_by_specialty,
        "min_slot_by_specialty": min_slot_by_specialty,
        "earliest_slot_provider_by_specialty": earliest_slot_provider_by_specialty,
        "earliest_slot_datetime_by_specialty": earliest_slot_datetime_by_specialty,
    }


# ---------------------------------------------------------------------------
# 3. pharmacy_list
# ---------------------------------------------------------------------------

@_register("pharmacy_list")
def build_pharmacy_list(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create pharmacies with one default. Optional mail-order.

    Params:
        count (2-3)
        include_mail_order (bool)
        must_include_name (str | list[str]): case-insensitive substring(s) of
            pharmacy template names that MUST be present in the selected
            pharmacies. Each matched template is pinned before other
            templates are appended up to `count`.
        target_pharmacy_name (str): case-insensitive substring of a pharmacy
            template name whose id should be exposed as `target_pharmacy_id`
            in the outputs. The name must also match one of `must_include_name`
            (or the caller must guarantee it ends up in the selection) —
            otherwise `target_pharmacy_id` may be None.
    Outputs: pharmacy_ids, default_pharmacy_id, mail_order_pharmacy_id,
             target_pharmacy_id, new_default_pharmacy_id,
             lowest_fee_retail_pharmacy_id, lowest_fee_retail_pharmacy_name,
             retail_fee_by_id

    ``new_default_pharmacy_id`` is the first non-default, non-mail-order
    retail pharmacy in ``pharmacy_ids`` (i.e. ``selected[1]`` when count >= 2).
    Tasks like ``pp_coordinate_rx_transfer`` that tell the agent to transfer
    prescriptions to "another retail pharmacy in your pharmacy list" use this
    as the canonical new-default answer.

    ``distinct_retail_fees`` (list[int]): when supplied, the retail pharmacies
    are assigned these dispensing fees in selection order (recycled if the
    list is shorter than the count). This makes the "lowest dispensing fee"
    discriminator deterministic and unambiguous across seeds. When omitted,
    each retail pharmacy gets a random fee from ``[5, 8, 10, 12]`` (legacy
    behaviour).

    ``lowest_fee_retail_pharmacy_id`` is the id of the non-default, non-mail
    -order retail pharmacy with the strictly lowest ``dispensing_fee``,
    breaking ties by the lower numeric id suffix. Tasks that ask the agent to
    re-derive the cheapest retail pharmacy use this as the canonical answer.
    """
    count = params.get("count", 2)
    include_mail_order = params.get("include_mail_order", False)
    distinct_retail_fees_raw = params.get("distinct_retail_fees")
    must_include_raw = params.get("must_include_name") or []
    if isinstance(must_include_raw, str):
        must_include_names = [must_include_raw]
    else:
        must_include_names = list(must_include_raw)
    target_pharmacy_name: str | None = params.get("target_pharmacy_name")

    if "pharmacies" not in ctx.base:
        ctx.base["pharmacies"] = []

    templates = list(_PHARMACY_TEMPLATES)
    ctx.rng.shuffle(templates)

    # Pin must_include templates to the front (preserving shuffle for the rest).
    pinned: list[dict[str, str]] = []
    for needle in must_include_names:
        match = next(
            (t for t in templates if needle.lower() in t["name"].lower()),
            None,
        )
        if match is not None:
            templates.remove(match)
            pinned.append(match)
    templates = pinned + templates
    selected = templates[:min(count, len(templates))]

    # If target_pharmacy_name resolves to selected[0] (the default), swap it
    # with selected[1] so the target is a non-default pharmacy the agent can
    # switch TO. Tasks like pp_update_default_pharmacy need target != default.
    if (
        target_pharmacy_name
        and len(selected) >= 2
        and target_pharmacy_name.lower() in selected[0]["name"].lower()
    ):
        selected[0], selected[1] = selected[1], selected[0]

    pharmacy_ids: list[str] = []
    default_pharmacy_id: str = ""
    mail_order_pharmacy_id: str | None = None
    target_pharmacy_id: str | None = None
    new_default_pharmacy_id: str | None = None

    # Deterministic distinct dispensing fees in selection order, when the
    # caller wants an unambiguous "lowest fee" discriminator.
    distinct_retail_fees: list[int] | None = None
    if distinct_retail_fees_raw:
        distinct_retail_fees = [int(f) for f in distinct_retail_fees_raw]

    for i, tmpl in enumerate(selected):
        pharm_id = ctx.next_id("pharm")
        is_default = i == 0
        if distinct_retail_fees:
            dispensing_fee = Decimal(str(distinct_retail_fees[i % len(distinct_retail_fees)]))
        else:
            dispensing_fee = Decimal(str(ctx.rng.choice([5, 8, 10, 12])))

        pharm_dict = {
            "id": pharm_id,
            "name": tmpl["name"],
            "address": tmpl["address"],
            "phone": tmpl["phone"],
            "is_default": is_default,
            "is_mail_order": False,
            "dispensing_fee": str(dispensing_fee),
        }
        ctx.base["pharmacies"].append(pharm_dict)
        pharmacy_ids.append(pharm_id)
        if is_default:
            default_pharmacy_id = pharm_id
        elif new_default_pharmacy_id is None:
            # First non-default retail pharmacy — canonical "switch-to" answer
            # for tasks that transfer prescriptions away from the closing default.
            new_default_pharmacy_id = pharm_id
        if (
            target_pharmacy_name
            and target_pharmacy_id is None
            and target_pharmacy_name.lower() in tmpl["name"].lower()
        ):
            target_pharmacy_id = pharm_id

    if include_mail_order:
        pharm_id = ctx.next_id("pharm")
        pharm_dict = {
            "id": pharm_id,
            "name": _MAIL_ORDER_PHARMACY["name"],
            "address": _MAIL_ORDER_PHARMACY["address"],
            "phone": _MAIL_ORDER_PHARMACY["phone"],
            "is_default": False,
            "is_mail_order": True,
            "dispensing_fee": "0",
            "cost_per_90day_supply": str(Decimal(str(ctx.rng.randint(15, 45)))),
        }
        ctx.base["pharmacies"].append(pharm_dict)
        pharmacy_ids.append(pharm_id)
        mail_order_pharmacy_id = pharm_id

    # Update patient's pharmacy_ids
    if "patient" in ctx.base:
        ctx.base["patient"]["pharmacy_ids"] = pharmacy_ids

    # Compute the cheapest NON-DEFAULT, NON-MAIL-ORDER retail pharmacy. This is
    # the canonical answer for tasks that ask the agent to re-derive "lowest
    # dispensing fee retail pharmacy" rather than naming the store. Tie-break by
    # the lower numeric id suffix so the answer is deterministic when two
    # retail pharmacies share a fee.
    #
    # pp_coordinate_rx_transfer also reads this as the canonical destination
    # for moving prescriptions to "the retail pharmacy with the lowest
    # dispensing fee" (excluding the closing default and any mail-order
    # pharmacy). It is exposed under both the descriptive name and the legacy
    # ``cheapest_retail_pharmacy_id`` alias; both refer to the same pharmacy.
    def _id_suffix(pid: str) -> int:
        try:
            return int(pid.rsplit("_", 1)[-1])
        except (ValueError, IndexError):
            return 0

    retail_candidates = [
        p for p in ctx.base.get("pharmacies", [])
        if not p.get("is_mail_order") and not p.get("is_default")
    ]
    lowest_fee_retail_pharmacy_id: str | None = None
    lowest_fee_retail_pharmacy_name: str | None = None
    cheapest_retail_pharmacy_id: str | None = None
    retail_fee_by_id: dict[str, str] = {
        p["id"]: str(p["dispensing_fee"]) for p in retail_candidates
    }
    if retail_candidates:
        cheapest = min(
            retail_candidates,
            key=lambda p: (Decimal(str(p["dispensing_fee"])), _id_suffix(p["id"])),
        )
        lowest_fee_retail_pharmacy_id = cheapest["id"]
        lowest_fee_retail_pharmacy_name = cheapest["name"]
        cheapest_retail_pharmacy_id = cheapest["id"]

    return {
        "pharmacy_ids": pharmacy_ids,
        "default_pharmacy_id": default_pharmacy_id,
        "mail_order_pharmacy_id": mail_order_pharmacy_id,
        "target_pharmacy_id": target_pharmacy_id,
        "new_default_pharmacy_id": new_default_pharmacy_id,
        "lowest_fee_retail_pharmacy_id": lowest_fee_retail_pharmacy_id,
        "lowest_fee_retail_pharmacy_name": lowest_fee_retail_pharmacy_name,
        "retail_fee_by_id": retail_fee_by_id,
        "cheapest_retail_pharmacy_id": cheapest_retail_pharmacy_id,
    }


# ---------------------------------------------------------------------------
# 4. appointment_history
# ---------------------------------------------------------------------------

@_register("appointment_history")
def build_appointment_history(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create a mix of upcoming, completed, and cancelled appointments.

    Params: upcoming_count, completed_count, cancelled_count,
            include_specialist (bool), conflict_pair (bool),
            conflict_same_provider (bool) — when conflict_pair is set, force
            both conflicting appointments onto the PCP so booked_at is the
            only discriminator,
            conflict_count (int, default 2) — number of appointments sharing
            the same datetime when conflict_pair is set; >2 produces a
            triple-or-more overlap whose keep/cancel partition is exposed via
            conflict_keep_apt_id / conflict_cancel_apt_ids,
            target_specialty (str | None) — when set, the upcoming
            appointment for that specialty is exposed as `target_apt_id`
            (PP-5). When unset, `target_apt_id` falls back to
            `specialist_apt_id` (the first non-PCP upcoming).
    Outputs: upcoming_ids, completed_ids, cancelled_ids, next_appointment_id,
             conflict_apt_ids, conflict_apt_date, conflict_provider_name,
             conflict_keep_apt_id, conflict_cancel_apt_ids,
             conflict_last_booked_apt_id,
             pcp_apt_id, specialist_apt_id, telehealth_apt_id, target_apt_id
    """
    upcoming_count = params.get("upcoming_count", 2)
    completed_count = params.get("completed_count", 2)
    cancelled_count = params.get("cancelled_count", 1)
    include_specialist = params.get("include_specialist", True)
    conflict_pair = params.get("conflict_pair", False)
    target_specialty: str | None = params.get("target_specialty")
    # When True, both appointments in the conflict pair are booked with the
    # *same* provider (the PCP). The two appointments then share provider AND
    # datetime, so the ONLY field that distinguishes them is ``booked_at`` —
    # forcing a consumer task to re-derive the earlier/later booking rather
    # than ground on the provider. Defaults False to preserve the existing
    # two-different-providers behaviour for tasks that rely on it.
    conflict_same_provider: bool = bool(params.get("conflict_same_provider", False))
    # B-1: per-specialty list of specialties whose upcoming appointments
    # should be created with `requires_confirmation=True`. Default empty
    # list preserves backward compatibility — existing tasks remain
    # confirmation-free unless they opt in.
    confirmation_specialties: list[str] = list(
        params.get("requires_confirmation_specialties", []) or []
    )

    if "appointments" not in ctx.base:
        ctx.base["appointments"] = []

    providers = ctx.base.get("providers", [])
    if not providers:
        raise ValueError("provider_directory must run before appointment_history")

    pcp_provider = next((p for p in providers if p["specialty"] == "pcp"), providers[0])
    specialist_providers = [p for p in providers if p["specialty"] not in ("pcp", "billing", "admin")]
    completed_provider_pool = [p for p in providers if p["specialty"] not in ("billing", "admin")] or providers

    upcoming_ids: list[str] = []
    pcp_apt_date: str = ""
    completed_ids: list[str] = []
    cancelled_ids: list[str] = []
    conflict_apt_ids: list[str] = []
    conflict_apt_date: str = ""
    conflict_provider_name: str = ""
    pcp_apt_id: str | None = None
    specialist_apt_id: str | None = None
    telehealth_apt_id: str | None = None
    next_appointment_id: str | None = None
    # PP-5: when target_specialty is provided, this is the apt_id of the
    # upcoming appointment whose provider has that specialty. Falls back
    # to specialist_apt_id (first non-PCP) when unset.
    target_apt_id: str | None = None
    # Pre-pick a target-specialty provider (if any). Falls back to None
    # when no provider matches; in that case target_apt_id stays None and
    # the YAML's eval falls back to specialist_apt_id.
    target_specialty_provider: dict[str, Any] | None = None
    if target_specialty:
        target_specialty_provider = next(
            (p for p in providers if p.get("specialty") == target_specialty),
            None,
        )

    def _link_matching_referral(apt_dict: dict[str, Any], prov: dict[str, Any]) -> None:
        for ref in ctx.base.get("referrals", []):
            if ref.get("linked_appointment_id"):
                continue
            provider_match = ref.get("to_provider_id") == prov["id"]
            specialty_match = ref.get("to_specialty") == prov.get("specialty")
            if provider_match or specialty_match:
                apt_dict["linked_referral_id"] = ref["id"]
                ref["linked_appointment_id"] = apt_dict["id"]
                break

    # --- Upcoming appointments ---
    # PP-5: if target_specialty is set, reserve slot i==1 for that
    # specialty so target_apt_id is bound to a provider of that specialty
    # rather than "first non-PCP".
    target_slot_index = 1 if (target_specialty_provider is not None and upcoming_count >= 2) else None
    for i in range(upcoming_count):
        apt_id = ctx.next_id("apt")
        days_ahead = ctx.rng.randint(1, 21)
        hour = ctx.rng.randint(9, 16)
        apt_dt = ctx.now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)

        # First upcoming is PCP, optional reserved target-specialty slot,
        # rest alternate.
        if i == 0:
            prov = pcp_provider
        elif i == target_slot_index and target_specialty_provider is not None:
            prov = target_specialty_provider
        elif include_specialist and specialist_providers:
            prov = ctx.rng.choice(specialist_providers)
        else:
            prov = pcp_provider

        apt_type = ctx.rng.choice(["in-person", "telehealth"])
        booked_at = ctx.now - timedelta(days=ctx.rng.randint(1, 14))

        needs_confirm = prov.get("specialty") in confirmation_specialties
        apt_dict = {
            "id": apt_id,
            "provider_id": prov["id"],
            "datetime": apt_dt.isoformat(),
            "type": apt_type,
            "status": "scheduled",
            "reason": ctx.rng.choice(["Follow-up", "Routine checkup", "Medication review", "Annual physical"]),
            "notes": "",
            "linked_referral_id": None,
            "booked_at": booked_at.isoformat(),
            "location": "Main Campus" if apt_type == "in-person" else "Telehealth",
            "requires_confirmation": needs_confirm,
            "confirmation_state": "pending" if needs_confirm else "not_required",
        }
        _link_matching_referral(apt_dict, prov)
        ctx.base["appointments"].append(apt_dict)
        upcoming_ids.append(apt_id)

        if i == 0:
            pcp_apt_id = apt_id
            # Human-readable date for instruction templating (e.g.
            # "March 15 2026 at 10:00"). The raw ISO string is too
            # noisy for a task prompt.
            pcp_apt_date = apt_dt.strftime("%B %-d %Y at %H:%M")
        if i == 0 or (next_appointment_id is None):
            next_appointment_id = apt_id
        if include_specialist and specialist_providers and prov != pcp_provider and specialist_apt_id is None:
            specialist_apt_id = apt_id
        if apt_type == "telehealth" and telehealth_apt_id is None:
            telehealth_apt_id = apt_id
        # PP-5: target_apt_id is the appt for the requested specialty.
        if (
            target_specialty
            and target_apt_id is None
            and prov.get("specialty") == target_specialty
        ):
            target_apt_id = apt_id

    # --- Completed appointments ---
    for _ in range(completed_count):
        apt_id = ctx.next_id("apt")
        days_ago = ctx.rng.randint(7, 90)
        hour = ctx.rng.randint(9, 16)
        apt_dt = ctx.now.replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
        prov = ctx.rng.choice(completed_provider_pool)
        booked_at = apt_dt - timedelta(days=ctx.rng.randint(7, 30))

        apt_dict = {
            "id": apt_id,
            "provider_id": prov["id"],
            "datetime": apt_dt.isoformat(),
            "type": ctx.rng.choice(["in-person", "telehealth"]),
            "status": "completed",
            "reason": ctx.rng.choice(["Follow-up", "Lab review", "Consultation"]),
            "notes": "Patient doing well. Continue current treatment plan.",
            "linked_referral_id": None,
            "booked_at": booked_at.isoformat(),
            "location": "Main Campus",
        }
        _link_matching_referral(apt_dict, prov)
        ctx.base["appointments"].append(apt_dict)
        completed_ids.append(apt_id)

    # --- Cancelled appointments ---
    for _ in range(cancelled_count):
        apt_id = ctx.next_id("apt")
        days_ago = ctx.rng.randint(1, 30)
        hour = ctx.rng.randint(9, 16)
        apt_dt = ctx.now.replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
        prov = ctx.rng.choice(providers)
        booked_at = apt_dt - timedelta(days=ctx.rng.randint(7, 21))

        apt_dict = {
            "id": apt_id,
            "provider_id": prov["id"],
            "datetime": apt_dt.isoformat(),
            "type": "in-person",
            "status": "cancelled",
            "reason": "Patient requested cancellation",
            "notes": "",
            "linked_referral_id": None,
            "booked_at": booked_at.isoformat(),
            "location": "Main Campus",
        }
        ctx.base["appointments"].append(apt_dict)
        cancelled_ids.append(apt_id)

    # --- Conflict cluster: N overlapping scheduled appointments ---
    # `conflict_count` controls how many appointments share the exact same
    # datetime. Defaults to 2 so legacy callers (conflict_pair: true) keep the
    # ORIGINAL two-appointment behaviour BYTE-FOR-BYTE — same RNG draw order
    # and same "first-created is earlier-booked, second is one day later"
    # ordering — so sibling tasks that read `conflict_apt_ids.1` as the
    # later-booked appointment continue to resolve correctly.
    #
    # For conflict_count > 2 (a triple-or-more overlap) the booked_at ordering
    # is DELIBERATELY decoupled from the creation/id order: distinct booked_at
    # offsets are shuffled so the agent cannot infer "keep the earliest" from
    # id suffix alone — it must read booked_at on each cluster member. The
    # keep/cancel partition is exposed via conflict_keep_apt_id /
    # conflict_cancel_apt_ids.
    conflict_count = int(params.get("conflict_count", 2))
    conflict_keep_apt_id: str | None = None
    conflict_cancel_apt_ids: list[str] = []
    conflict_last_booked_apt_id: str | None = None
    if conflict_pair and len(providers) >= 2 and conflict_count >= 2:
        conflict_dt = ctx.now.replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=ctx.rng.randint(3, 10))
        conflict_apt_date = conflict_dt.strftime("%B %-d %Y at %H:%M")
        if conflict_count == 2:
            # LEGACY path (unchanged RNG draws / ordering). First appointment
            # booked earlier, second booked 1 day later so booked_at values are
            # always distinct and ordering is deterministic.
            first_booked_at = ctx.now - timedelta(days=ctx.rng.randint(2, 7))
            second_booked_at = first_booked_at + timedelta(days=1)
            conflict_booked_ats = [first_booked_at, second_booked_at]
        else:
            # Distinct booked_at offsets (whole days apart), shuffled so the
            # earliest-booked member is not predictable from id order.
            base_booked_at = ctx.now - timedelta(days=ctx.rng.randint(conflict_count + 1, 12))
            booked_offsets = list(range(conflict_count))
            ctx.rng.shuffle(booked_offsets)
            conflict_booked_ats = [
                base_booked_at + timedelta(days=off) for off in booked_offsets
            ]
        cluster: list[dict[str, Any]] = []
        for j in range(conflict_count):
            apt_id = ctx.next_id("apt")
            # When conflict_same_provider is set, both appointments use the
            # PCP so booked_at is the only discriminator; otherwise keep the
            # legacy two-different-providers behaviour.
            prov = pcp_provider if conflict_same_provider else providers[j % len(providers)]
            booked_at = conflict_booked_ats[j]

            apt_dict = {
                "id": apt_id,
                "provider_id": prov["id"],
                "datetime": conflict_dt.isoformat(),
                "type": "in-person",
                "status": "scheduled",
                "reason": "Follow-up",
                "notes": "",
                "linked_referral_id": None,
                "booked_at": booked_at.isoformat(),
                "location": "Main Campus",
            }
            _link_matching_referral(apt_dict, prov)
            ctx.base["appointments"].append(apt_dict)
            conflict_apt_ids.append(apt_id)
            upcoming_ids.append(apt_id)
            cluster.append(apt_dict)

        conflict_provider_name = (
            pcp_provider.get("name", "")
            if conflict_same_provider
            else providers[0].get("name", "")
        )
        # Earliest-booked appointment is the one to KEEP; every other member
        # of the cluster must be cancelled. Tie-break on id for total order
        # (booked_at values are distinct here, so the tie-break never fires —
        # it mirrors the canonical_diff predicate for safety).
        ordered = sorted(cluster, key=lambda a: (a["booked_at"], a["id"]))
        conflict_keep_apt_id = ordered[0]["id"]
        conflict_cancel_apt_ids = [a["id"] for a in ordered[1:]]
        # Last-booked (max by booked_at, id) member of the cluster — preserved
        # as a back-compat output so the original `later_booked_apt_id` target
        # still resolves. It is always one of the appointments to cancel.
        conflict_last_booked_apt_id = ordered[-1]["id"]

    # PP-5: when target_specialty is unset (or no matching provider was
    # found), fall back to specialist_apt_id so consumers can read a
    # uniform `target_apt_id` field regardless of the seed shape.
    if target_apt_id is None:
        target_apt_id = specialist_apt_id

    return {
        "upcoming_ids": upcoming_ids,
        "completed_ids": completed_ids,
        "cancelled_ids": cancelled_ids,
        "next_appointment_id": next_appointment_id,
        "conflict_apt_ids": conflict_apt_ids,
        "conflict_apt_date": conflict_apt_date,
        "conflict_provider_name": conflict_provider_name,
        "conflict_keep_apt_id": conflict_keep_apt_id,
        "conflict_cancel_apt_ids": conflict_cancel_apt_ids,
        "conflict_last_booked_apt_id": conflict_last_booked_apt_id,
        "pcp_apt_id": pcp_apt_id,
        "pcp_apt_date": pcp_apt_date,
        "specialist_apt_id": specialist_apt_id,
        "telehealth_apt_id": telehealth_apt_id,
        "target_apt_id": target_apt_id,
    }


# ---------------------------------------------------------------------------
# 5. prescription_cabinet
# ---------------------------------------------------------------------------

@_register("prescription_cabinet")
def build_prescription_cabinet(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate prescriptions with varying refill states.

    Params: active_count (int), expired_count (int), zero_refill_count (int),
            expiring_soon_count (int), expiring_zero_refill_count (int),
            interaction_pair (bool), source_pharmacy_role (str),
            source_active_count (int), source_exclude_pharmacy_name (str)
    Outputs: active_rx_ids, zero_refill_rx_id, expiring_rx_ids,
             expiring_zero_refill_rx_ids, interacting_rx_ids,
             interacting_medications, zero_refill_rx_ids,
             zero_refill_medications, rxes_at_source_pharmacy,
             non_source_active_rx_ids, source_pharmacy_id

    Note on ``expiring_zero_refill_count``: forces the first N entries of
    the ``expiring_rx_ids`` subset to have ``refills_remaining == 0``. This
    lets tasks deterministically pin the "expiring AND zero-refill" target
    intersection. Remaining expiring rxes get ``randint(1, 2)`` so the
    distinction is meaningful.

    Note on ``source_pharmacy_role`` / ``source_active_count``: pins the first
    ``source_active_count`` ACTIVE prescriptions onto the pharmacy identified
    by ``source_pharmacy_role`` (``"mail_order"`` or ``"default"``) and exposes
    that exact id set as ``rxes_at_source_pharmacy``. The remaining active
    prescriptions are pinned off the source (and, if
    ``source_exclude_pharmacy_name`` is given, off the transfer destination
    too) and exposed as ``non_source_active_rx_ids`` so a transfer bijection
    can saturate over a deterministic subset while the rest stay decoys.
    """
    active_count = params.get("active_count", 3)
    expired_count = params.get("expired_count", 0)
    zero_refill_count = params.get("zero_refill_count", 0)
    expiring_soon_count = params.get("expiring_soon_count", 0)
    expiring_zero_refill_count = params.get("expiring_zero_refill_count", 0)
    interaction_pair = params.get("interaction_pair", False)
    # When True, after the genuine active↔active interaction pair is wired up,
    # attach a DECOY interaction entry between an expired prescription and one
    # active prescription that is NOT a member of the genuine pair. This means
    # the cabinet contains two prescriptions whose ``interactions`` list is
    # non-empty but only ONE pair is an active↔active conflict; an agent that
    # naively scans for ``interactions != []`` (rather than confirming both
    # members are ``status == "active"``) will surface the wrong pair. Requires
    # ``interaction_pair`` and at least one expired prescription to take effect.
    expired_interaction_decoy = bool(params.get("expired_interaction_decoy", False))
    target_medication_name: str | None = params.get("target_medication_name")
    target_exclude_mail_order = bool(params.get("target_exclude_mail_order", False))
    target_exclude_pharmacy_name: str | None = params.get("target_exclude_pharmacy_name")
    # When True, every active prescription starts on the default pharmacy.
    # Used by tasks like pp_coordinate_rx_transfer where the scenario is
    # "your default pharmacy is closing — transfer all your active rxes to
    # another retail pharmacy". We need each rx to actually move (not be
    # already at the destination) so the canonical_diff update[0] bijection
    # saturates.
    active_at_default_only = bool(params.get("active_at_default_only", False))
    # PP: place exactly the first N active prescriptions on the default
    # pharmacy (the "transfer set") and the remaining active prescriptions on
    # `active_trap_pharmacy_id` (a non-default retail pharmacy that should NOT
    # be touched). Lets a task expose `active_at_default_rx_ids` so a transfer
    # bijection saturates over exactly the rxes at the closing/old default
    # while sibling rxes at another pharmacy act as a frozen distractor set.
    active_at_default_count = int(params.get("active_at_default_count", 0) or 0)
    active_trap_pharmacy_id: str | None = params.get("active_trap_pharmacy_id")
    # Force the FIRST `source_active_count` active prescriptions onto a
    # single "source" pharmacy identified by role. Used by tasks like
    # pp_transfer_prescription where the scenario is "the mail-order pharmacy
    # is discontinuing — transfer exactly the prescriptions filled there to a
    # specific retail location". The remaining active prescriptions are
    # pinned to a pharmacy that is NEITHER the source NOR (optionally) the
    # destination, so they are decoys the agent must NOT move. Exposes the
    # exact subset as `rxes_at_source_pharmacy` so a bijection can saturate
    # over a deterministic set without reconstructing the filter inside a
    # predicate (Class 6 set-in-filter hazard). `source_pharmacy_role` is one
    # of {"mail_order", "default"}; `source_exclude_pharmacy_name` keeps the
    # NON-source active rxes off the transfer destination so they stay decoys.
    source_pharmacy_role: str | None = params.get("source_pharmacy_role")
    source_active_count = int(params.get("source_active_count", 0) or 0)
    source_exclude_pharmacy_name: str | None = params.get("source_exclude_pharmacy_name")

    if "prescriptions" not in ctx.base:
        ctx.base["prescriptions"] = []

    providers = ctx.base.get("providers", [])
    pharmacies = ctx.base.get("pharmacies", [])
    pcp_id = ctx.base.get("patient", {}).get("pcp_id", "prov_1")
    default_pharm_id = next((p["id"] for p in pharmacies if p.get("is_default")), "pharm_1") if pharmacies else "pharm_1"

    # Resolve the "source" pharmacy id (the one the source active rxes are
    # pinned to) from its role. Falls back to None when no matching pharmacy
    # exists; in that case the source-pinning logic is skipped and
    # `rxes_at_source_pharmacy` stays empty.
    source_pharmacy_id: str | None = None
    if source_pharmacy_role == "mail_order":
        source_pharmacy_id = next(
            (p["id"] for p in pharmacies if p.get("is_mail_order")), None
        )
    elif source_pharmacy_role == "default":
        source_pharmacy_id = default_pharm_id if pharmacies else None

    # Shuffle the medication pool, then pin target_medication_name first if specified
    med_pool = list(_MEDICATIONS)
    ctx.rng.shuffle(med_pool)
    if target_medication_name:
        pinned = next(
            (m for m in med_pool if target_medication_name.lower() in m["name"].lower()),
            None,
        )
        if pinned:
            med_pool.remove(pinned)
            med_pool.insert(0, pinned)
    med_idx = 0

    active_rx_ids: list[str] = []
    zero_refill_rx_id: str | None = None
    zero_refill_medication: str = ""
    # Full list of the dedicated zero-refill active prescriptions created by the
    # ``zero_refill_count`` loop (NOT the expiring subset). Tasks that must
    # renew *every* out-of-refills prescription (e.g. pp_request_renewal, which
    # asks the agent to renew each medication that has 0 refills remaining)
    # need the complete set to drive a bijection without recomputing the
    # refills==0 filter inside a predicate (Class 6 set-precompute hazard).
    zero_refill_rx_ids: list[str] = []
    zero_refill_medications: list[str] = []
    target_rx_id: str | None = None
    expiring_rx_ids: list[str] = []
    expiring_zero_refill_rx_ids: list[str] = []
    interacting_rx_ids: list[str] = []
    interacting_medications: list[str] = []
    # Subset of active rxes deterministically pinned to the source pharmacy
    # (e.g. the discontinuing mail-order pharmacy). This is the canonical
    # transfer set for pp_transfer_prescription.
    rxes_at_source_pharmacy: list[str] = []

    def _make_rx(
        med: dict,
        status: str,
        refills: int,
        expires_days: int,
        *,
        force_retail_pharmacy: bool = False,
        exclude_pharmacy_name: str | None = None,
        force_default_pharmacy: bool = False,
        pin_pharmacy_id: str | None = None,
        force_pharmacy_id: str | None = None,
        exclude_pharmacy_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        nonlocal med_idx
        rx_id = ctx.next_id("rx")
        provider_id = ctx.rng.choice([p["id"] for p in providers]) if providers else pcp_id
        available_pharmacies = list(pharmacies)
        if force_default_pharmacy and pharmacies:
            default_match = [p for p in pharmacies if p.get("is_default")]
            if default_match:
                available_pharmacies = default_match
        if force_retail_pharmacy and pharmacies:
            retail_pharmacies = [p for p in pharmacies if not p.get("is_mail_order")]
            if retail_pharmacies:
                available_pharmacies = retail_pharmacies
        if exclude_pharmacy_name:
            filtered = [
                p for p in available_pharmacies
                if exclude_pharmacy_name.lower() not in p.get("name", "").lower()
            ]
            if filtered:
                available_pharmacies = filtered
        if exclude_pharmacy_ids:
            filtered = [
                p for p in available_pharmacies
                if p.get("id") not in exclude_pharmacy_ids
            ]
            if filtered:
                available_pharmacies = filtered
        # An explicit pharmacy id pin overrides every heuristic above so the
        # rx lands deterministically on the requested pharmacy. ``pin_pharmacy_id``
        # (used to build a controlled "transfer set" at the default and a frozen
        # distractor set at another retail pharmacy) and ``force_pharmacy_id``
        # (used by the source-pharmacy transfer fixture) are driven by distinct,
        # mutually-exclusive callers; either one wins over the random choice.
        if pin_pharmacy_id and any(p["id"] == pin_pharmacy_id for p in pharmacies):
            pharm_id = pin_pharmacy_id
        elif force_pharmacy_id and any(p["id"] == force_pharmacy_id for p in pharmacies):
            pharm_id = force_pharmacy_id
        else:
            pharm_id = ctx.rng.choice([p["id"] for p in available_pharmacies]) if available_pharmacies else default_pharm_id
        last_filled = ctx.now - timedelta(days=ctx.rng.randint(7, 60))
        expires_at = ctx.now + timedelta(days=expires_days)

        return {
            "id": rx_id,
            "medication": med["name"],
            "dosage": med["dosage"],
            "frequency": med["frequency"],
            "provider_id": provider_id,
            "pharmacy_id": pharm_id,
            "refills_remaining": refills,
            "last_filled": last_filled.isoformat(),
            "expires_at": expires_at.isoformat(),
            "status": status,
            "interactions": [],
        }

    # Active prescriptions (normal refills)
    active_at_default_rx_ids: list[str] = []
    for active_idx in range(active_count):
        if med_idx >= len(med_pool):
            break
        med = med_pool[med_idx]
        med_idx += 1
        is_target_med = bool(
            target_medication_name
            and target_medication_name.lower() in med["name"].lower()
        )
        force_retail_pharmacy = bool(target_exclude_mail_order and is_target_med)
        exclude_pharmacy_for_rx = (
            target_exclude_pharmacy_name if is_target_med else None
        )
        # When active_at_default_count is set: pin the first N active rxes to
        # the default pharmacy (the transfer set) and the rest to the trap
        # pharmacy (a frozen distractor set). This takes precedence over the
        # random/force_default placement so the split is deterministic.
        pin_pharmacy_for_rx: str | None = None
        if active_at_default_count > 0:
            if active_idx < active_at_default_count:
                pin_pharmacy_for_rx = default_pharm_id
            elif active_trap_pharmacy_id:
                pin_pharmacy_for_rx = active_trap_pharmacy_id
        # Source-pharmacy pinning: the first `source_active_count` active rxes
        # land on the source pharmacy (the transfer set); the rest are pinned
        # off the source AND (optionally) off the destination so they are
        # decoys the agent must not move.
        force_pharmacy_for_rx: str | None = None
        exclude_pharmacy_ids_for_rx: tuple[str, ...] = ()
        is_source_rx = False
        if source_pharmacy_id is not None and source_active_count > 0:
            if active_idx < source_active_count:
                force_pharmacy_for_rx = source_pharmacy_id
                is_source_rx = True
            else:
                # Non-source actives must NOT live on the source pharmacy
                # (otherwise the "transfer everything at the mail-order
                # pharmacy" instruction would be ambiguous), and they stay off
                # the transfer destination so they remain legitimate decoys.
                exclude_pharmacy_ids_for_rx = (source_pharmacy_id,)
                if source_exclude_pharmacy_name and exclude_pharmacy_for_rx is None:
                    exclude_pharmacy_for_rx = source_exclude_pharmacy_name
        rx = _make_rx(
            med,
            "active",
            ctx.rng.randint(2, 6),
            ctx.rng.randint(90, 365),
            force_retail_pharmacy=force_retail_pharmacy,
            exclude_pharmacy_name=exclude_pharmacy_for_rx,
            force_default_pharmacy=active_at_default_only,
            pin_pharmacy_id=pin_pharmacy_for_rx,
            force_pharmacy_id=force_pharmacy_for_rx,
            exclude_pharmacy_ids=exclude_pharmacy_ids_for_rx,
        )
        ctx.base["prescriptions"].append(rx)
        active_rx_ids.append(rx["id"])
        if (
            active_at_default_count > 0
            and active_idx < active_at_default_count
            and rx["pharmacy_id"] == default_pharm_id
        ):
            active_at_default_rx_ids.append(rx["id"])
        if is_source_rx:
            rxes_at_source_pharmacy.append(rx["id"])
        # Track target rx if this medication matches the pinned target
        if target_medication_name and target_rx_id is None:
            if target_medication_name.lower() in med["name"].lower():
                target_rx_id = rx["id"]

    # Zero-refill prescriptions
    for _ in range(zero_refill_count):
        if med_idx >= len(med_pool):
            break
        med = med_pool[med_idx]
        med_idx += 1
        rx = _make_rx(med, "active", 0, ctx.rng.randint(30, 180))
        ctx.base["prescriptions"].append(rx)
        active_rx_ids.append(rx["id"])
        zero_refill_rx_ids.append(rx["id"])
        zero_refill_medications.append(med["name"])
        if zero_refill_rx_id is None:
            zero_refill_rx_id = rx["id"]
            zero_refill_medication = med["name"]

    # Expiring-soon prescriptions. The first ``expiring_zero_refill_count``
    # entries are forced to refills=0 so the "expiring AND zero-refill"
    # intersection is deterministic across seeds; remaining ones get 1-2
    # refills so they explicitly should NOT be renewed.
    n_expiring_zero = min(expiring_zero_refill_count, expiring_soon_count)
    for idx in range(expiring_soon_count):
        if med_idx >= len(med_pool):
            break
        med = med_pool[med_idx]
        med_idx += 1
        if idx < n_expiring_zero:
            refills = 0
        elif expiring_zero_refill_count > 0:
            # Deterministic non-zero refill count for the "has refills" subset.
            refills = ctx.rng.randint(1, 2)
        else:
            # Legacy behaviour for tasks that didn't opt in to the split.
            refills = ctx.rng.randint(0, 2)
        rx = _make_rx(med, "active", refills, ctx.rng.randint(5, 25))
        ctx.base["prescriptions"].append(rx)
        active_rx_ids.append(rx["id"])
        expiring_rx_ids.append(rx["id"])
        if refills == 0:
            expiring_zero_refill_rx_ids.append(rx["id"])

    # Expired prescriptions
    expired_rx_ids: list[str] = []
    for _ in range(expired_count):
        if med_idx >= len(med_pool):
            break
        med = med_pool[med_idx]
        med_idx += 1
        rx = _make_rx(med, "expired", 0, -ctx.rng.randint(1, 90))
        ctx.base["prescriptions"].append(rx)
        expired_rx_ids.append(rx["id"])

    # Interaction pair -- two active meds with mutual conflict entries
    if interaction_pair and len(_INTERACTION_PAIRS) > 0:
        pair = ctx.rng.choice(_INTERACTION_PAIRS)
        pair_meds = [
            next((m for m in _MEDICATIONS if m["name"] == pair[0]), None),
            next((m for m in _MEDICATIONS if m["name"] == pair[1]), None),
        ]
        if pair_meds[0] and pair_meds[1]:
            rx_ids_pair: list[str] = []
            for k, pm in enumerate(pair_meds):
                # Reuse an existing prescription for this medication ONLY when it
                # is ACTIVE — the interaction pair must be an active↔active
                # conflict. If the only existing match is expired (or none
                # exists), mint a fresh ACTIVE prescription so the genuine pair
                # is always actionable regardless of seed.
                existing = next(
                    (
                        r for r in ctx.base["prescriptions"]
                        if r["medication"] == pm["name"] and r.get("status") == "active"
                    ),
                    None,
                )
                if existing:
                    rx_ids_pair.append(existing["id"])
                else:
                    rx = _make_rx(pm, "active", ctx.rng.randint(1, 4), ctx.rng.randint(60, 200))
                    ctx.base["prescriptions"].append(rx)
                    active_rx_ids.append(rx["id"])
                    rx_ids_pair.append(rx["id"])

            # Set interactions on both
            for rx_dict in ctx.base["prescriptions"]:
                if rx_dict["id"] == rx_ids_pair[0]:
                    rx_dict["interactions"] = [pair[1]]
                elif rx_dict["id"] == rx_ids_pair[1]:
                    rx_dict["interactions"] = [pair[0]]

            interacting_rx_ids = rx_ids_pair
            interacting_medications = list(pair)

    # Decoy interaction trap: wire a cross-interaction between an expired
    # prescription and one ACTIVE prescription that is NOT part of the genuine
    # active↔active pair. The decoy is intentionally invalid (one member is
    # expired), so the only conflict that warrants action is the genuine pair.
    decoy_interaction_rx_ids: list[str] = []
    if expired_interaction_decoy and expired_rx_ids:
        expired_id = expired_rx_ids[0]
        expired_rx = next(
            (r for r in ctx.base["prescriptions"] if r["id"] == expired_id), None
        )
        # Choose an active rx outside the genuine pair as the decoy's active side.
        decoy_active = next(
            (
                r for r in ctx.base["prescriptions"]
                if r.get("status") == "active"
                and r["id"] not in interacting_rx_ids
            ),
            None,
        )
        if expired_rx is not None and decoy_active is not None:
            expired_rx["interactions"] = [decoy_active["medication"]]
            decoy_active["interactions"] = [expired_rx["medication"]]
            decoy_interaction_rx_ids = [expired_id, decoy_active["id"]]

    # Provider ids that wrote the genuine active↔active interaction pair. Tasks
    # that route the agent to a prescriber (rather than the PCP) can pin this;
    # exposed unconditionally so it is available without re-scanning rx records.
    interacting_prescriber_ids: list[str] = []
    for rid in interacting_rx_ids:
        rx_obj = next((r for r in ctx.base["prescriptions"] if r["id"] == rid), None)
        if rx_obj is not None:
            interacting_prescriber_ids.append(rx_obj["provider_id"])

    # Subset of expiring rxes that still have ≥1 refill remaining — this is
    # the "request refill" target for tasks that distinguish refill-vs-renewal
    # based on whether the expiring rx has refills left.
    expiring_with_refills_rx_ids = [
        rid for rid in expiring_rx_ids
        if rid not in expiring_zero_refill_rx_ids
    ]

    # Active rxes NOT pinned to the source pharmacy. These share the "active"
    # category with the transfer set but must remain on their current
    # pharmacy — they are the decoys a filtered invariant freezes.
    non_source_active_rx_ids = [
        rid for rid in active_rx_ids if rid not in rxes_at_source_pharmacy
    ]

    return {
        "active_rx_ids": active_rx_ids,
        "active_at_default_rx_ids": active_at_default_rx_ids,
        "zero_refill_rx_id": zero_refill_rx_id,
        "zero_refill_medication": zero_refill_medication,
        "zero_refill_rx_ids": zero_refill_rx_ids,
        "zero_refill_medications": zero_refill_medications,
        "target_rx_id": target_rx_id,
        "expiring_rx_ids": expiring_rx_ids,
        "expiring_zero_refill_rx_ids": expiring_zero_refill_rx_ids,
        "expiring_with_refills_rx_ids": expiring_with_refills_rx_ids,
        "interacting_rx_ids": interacting_rx_ids,
        "interacting_medications": interacting_medications,
        "interacting_prescriber_ids": interacting_prescriber_ids,
        "decoy_interaction_rx_ids": decoy_interaction_rx_ids,
        # Source-pharmacy transfer fixture outputs.
        "rxes_at_source_pharmacy": rxes_at_source_pharmacy,
        "non_source_active_rx_ids": non_source_active_rx_ids,
        "source_pharmacy_id": source_pharmacy_id,
    }


# ---------------------------------------------------------------------------
# 6. lab_results_panel
# ---------------------------------------------------------------------------

@_register("lab_results_panel")
def build_lab_results_panel(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate lab results across dates and statuses.

    Params: resulted_count (int), pending_count (int), abnormal_count (int),
            critical_count (int), trend_test (str), trend_values (list[str])
    Outputs: resulted_lab_ids, pending_lab_ids, abnormal_lab_ids, critical_lab_id,
             trend_lab_ids, trend_test_name
    """
    resulted_count = params.get("resulted_count", 3)
    pending_count = params.get("pending_count", 1)
    abnormal_count = params.get("abnormal_count", 1)
    critical_count = params.get("critical_count", 0)
    trend_test = params.get("trend_test", None)
    trend_values = params.get("trend_values", None)

    if "lab_results" not in ctx.base:
        ctx.base["lab_results"] = []

    providers = ctx.base.get("providers", [])
    pcp_id = ctx.base.get("patient", {}).get("pcp_id", "prov_1")
    appointments = ctx.base.get("appointments", [])
    completed_apts = [a for a in appointments if a.get("status") == "completed"]
    linked_apt_cursor = 0

    # Available ordering providers (non-billing, non-admin)
    ordering_providers = [p["id"] for p in providers if p.get("specialty") not in ("billing", "admin")]
    if not ordering_providers:
        ordering_providers = [pcp_id]

    # When a trend_test is specified, exclude it from the random-pick pool so
    # a random normal/abnormal lab doesn't collide with the explicit trend
    # sequence and silently invert the most-recent reading (e.g. HbA1c's
    # normal=5.2 landing within the 6-month window after a 6.5→7.8 trend).
    lab_pool = [t for t in _LAB_TESTS if t["name"] != trend_test] if trend_test else list(_LAB_TESTS)
    ctx.rng.shuffle(lab_pool)
    lab_idx = 0

    resulted_lab_ids: list[str] = []
    pending_lab_ids: list[str] = []
    abnormal_lab_ids: list[str] = []
    critical_lab_id: str | None = None
    trend_lab_ids: list[str] = []
    trend_test_name: str | None = None

    def _pick_lab() -> dict[str, Any]:
        nonlocal lab_idx
        lab = lab_pool[lab_idx % len(lab_pool)]
        lab_idx += 1
        return lab

    def _make_lab(test: dict, flag: str, status: str, days_ago: int, value_override: str | None = None) -> dict[str, Any]:
        nonlocal linked_apt_cursor
        lab_id = ctx.next_id("lab")
        collected_at = ctx.now - timedelta(days=days_ago)
        value = value_override or test[flag] if flag in test else test["normal"]
        linked_apt = None
        if completed_apts:
            linked_apt = completed_apts[linked_apt_cursor % len(completed_apts)]
            linked_apt_cursor += 1
        ordered_by = linked_apt["provider_id"] if linked_apt else ctx.rng.choice(ordering_providers)
        return {
            "id": lab_id,
            "test_name": test["name"],
            "test_code": test["code"],
            "ordered_by": ordered_by,
            "collected_at": collected_at.isoformat(),
            "value": value,
            "unit": test["unit"],
            "reference_range": test["ref"],
            "flag": flag,
            "status": status,
            "linked_appointment_id": linked_apt["id"] if linked_apt else None,
        }

    # Normal resulted labs
    normal_count = max(0, resulted_count - abnormal_count - critical_count)
    for _ in range(normal_count):
        test = _pick_lab()
        # Check if this is a Lipid Panel component -- if the test name matches a
        # lipid component, it's already individual.  We generate panel tests
        # only when explicitly requested via trend_test.
        lab = _make_lab(test, "normal", "resulted", ctx.rng.randint(1, 60))
        ctx.base["lab_results"].append(lab)
        resulted_lab_ids.append(lab["id"])

    # Abnormal labs
    for _ in range(abnormal_count):
        test = _pick_lab()
        lab = _make_lab(test, "abnormal", "resulted", ctx.rng.randint(1, 30))
        ctx.base["lab_results"].append(lab)
        resulted_lab_ids.append(lab["id"])
        abnormal_lab_ids.append(lab["id"])

    # Critical labs
    for _ in range(critical_count):
        test = _pick_lab()
        lab = _make_lab(test, "critical", "resulted", ctx.rng.randint(0, 3))
        ctx.base["lab_results"].append(lab)
        resulted_lab_ids.append(lab["id"])
        abnormal_lab_ids.append(lab["id"])
        if critical_lab_id is None:
            critical_lab_id = lab["id"]

    # Pending labs
    for _ in range(pending_count):
        test = _pick_lab()
        lab_id = ctx.next_id("lab")
        collected_at = ctx.now - timedelta(days=ctx.rng.randint(0, 2))
        linked_apt = None
        if completed_apts:
            linked_apt = completed_apts[linked_apt_cursor % len(completed_apts)]
            linked_apt_cursor += 1
        ordered_by = linked_apt["provider_id"] if linked_apt else ctx.rng.choice(ordering_providers)
        lab = {
            "id": lab_id,
            "test_name": test["name"],
            "test_code": test["code"],
            "ordered_by": ordered_by,
            "collected_at": collected_at.isoformat(),
            "value": "",
            "unit": test["unit"],
            "reference_range": test["ref"],
            "flag": "normal",
            "status": "pending",
            "linked_appointment_id": linked_apt["id"] if linked_apt else None,
        }
        ctx.base["lab_results"].append(lab)
        pending_lab_ids.append(lab_id)

    # Trend test -- create a time series of the same test
    if trend_test and trend_values:
        trend_test_info = next((t for t in _LAB_TESTS if t["name"] == trend_test), None)
        if trend_test_info:
            trend_test_name = trend_test
            for i, val in enumerate(trend_values):
                days_ago = (len(trend_values) - i) * 90  # quarterly spacing
                flag = "normal"
                try:
                    # Determine flag from reference range
                    ref_parts = trend_test_info["ref"].split("-")
                    if len(ref_parts) == 2:
                        low, high = float(ref_parts[0]), float(ref_parts[1])
                        v = float(val)
                        if v > high * 1.5 or v < low * 0.5:
                            flag = "critical"
                        elif v > high or v < low:
                            flag = "abnormal"
                except (ValueError, IndexError):
                    pass

                lab = _make_lab(trend_test_info, flag, "resulted", days_ago, value_override=val)
                ctx.base["lab_results"].append(lab)
                trend_lab_ids.append(lab["id"])
                # If this is a critical trend value and no separate critical lab was created,
                # use it as the primary critical_lab_id
                if flag == "critical" and critical_lab_id is None:
                    critical_lab_id = lab["id"]

    # Derived: the test_name / test_code of every out-of-range RESULTED lab
    # (flag in {"abnormal", "critical"}), ordered by collected_at descending
    # then lab id, deduplicated while preserving that order. Tasks that ask
    # the agent to RE-DERIVE which resulted labs are out of range (e.g.
    # pp_cross_reference_labs_meds) need a scalar list target so a
    # `substring_all` / `set_eq` predicate can verify the agent named the
    # exact abnormal panel — without pushing reference-range parsing into a
    # `filter:`/`expr` scope (which only sees a+target+initial+state).
    _all_labs_by_id = {lab["id"]: lab for lab in ctx.base["lab_results"]}
    _abnormal_sorted = sorted(
        abnormal_lab_ids,
        key=lambda lid: (_all_labs_by_id[lid]["collected_at"], lid),
        reverse=True,
    )
    abnormal_lab_test_names: list[str] = []
    abnormal_lab_test_codes: list[str] = []
    # Per-lab "<test_name> <value> <unit>" label for every out-of-range
    # RESULTED lab, in the same (collected_at desc, id) order. Tasks that want
    # the agent to RE-DERIVE and quote each abnormal reading's actual value —
    # not merely list the test names — pin a `substring_all`/expr predicate
    # against this scalar list so a generic "review your labs" reason cannot
    # satisfy the gate. The unit is appended only when non-empty (e.g. INR has
    # no unit) so the label is an exact substring an agent can reproduce.
    abnormal_lab_value_labels: list[str] = []
    # test_name of the single most-severe (critical) out-of-range RESULTED lab.
    # Empty string when no critical lab exists. Lets a task escalate the
    # most-tempting "abnormal vs critical" distinction into an exact predicate
    # without pushing flag parsing into a filter scope.
    critical_lab_test_name: str = ""
    for lid in _abnormal_sorted:
        lab = _all_labs_by_id.get(lid)
        if lab is None:
            continue
        if lab["test_name"] not in abnormal_lab_test_names:
            abnormal_lab_test_names.append(lab["test_name"])
        if lab["test_code"] not in abnormal_lab_test_codes:
            abnormal_lab_test_codes.append(lab["test_code"])
        unit = str(lab.get("unit") or "").strip()
        value_label = (
            f"{lab['test_name']} {lab['value']} {unit}".strip()
            if unit
            else f"{lab['test_name']} {lab['value']}".strip()
        )
        if value_label not in abnormal_lab_value_labels:
            abnormal_lab_value_labels.append(value_label)
        if not critical_lab_test_name and lab.get("flag") == "critical":
            critical_lab_test_name = lab["test_name"]

    return {
        "resulted_lab_ids": resulted_lab_ids,
        "pending_lab_ids": pending_lab_ids,
        "abnormal_lab_ids": abnormal_lab_ids,
        "abnormal_lab_test_names": abnormal_lab_test_names,
        "abnormal_lab_test_codes": abnormal_lab_test_codes,
        "abnormal_lab_value_labels": abnormal_lab_value_labels,
        "critical_lab_test_name": critical_lab_test_name,
        "critical_lab_id": critical_lab_id,
        "trend_lab_ids": trend_lab_ids,
        "trend_test_name": trend_test_name,
    }


# ---------------------------------------------------------------------------
# Helper: resolve contextual provider
# ---------------------------------------------------------------------------

def _resolve_context_provider(
    ctx: PatientPortalSeedContext,
    body_context: dict[str, Any],
    clinical_providers: list[dict[str, Any]],
    pcp_id: str,
) -> dict[str, Any] | None:
    providers = ctx.base.get("providers", [])
    providers_by_id = {p["id"]: p for p in providers}

    explicit_provider_id = body_context.get("provider_id")
    if explicit_provider_id:
        return providers_by_id.get(str(explicit_provider_id))

    provider_selector = body_context.get("provider_selector")
    if provider_selector == "pcp":
        return providers_by_id.get(pcp_id)
    if provider_selector == "most_recent_completed":
        completed_apts = [a for a in ctx.base.get("appointments", []) if a.get("status") == "completed"]
        if completed_apts:
            most_recent = max(completed_apts, key=lambda a: a["datetime"])
            return providers_by_id.get(most_recent["provider_id"])

    specialty = body_context.get("provider_specialty")
    if specialty:
        return next((p for p in providers if p.get("specialty") == specialty), None)

    if clinical_providers:
        return clinical_providers[0]
    return providers_by_id.get(pcp_id) or (providers[0] if providers else None)


# ---------------------------------------------------------------------------
# Helper: generate contextual message body
# ---------------------------------------------------------------------------

def _generate_contextual_body(ctx: PatientPortalSeedContext, body_context: dict[str, Any]) -> str:
    """Generate a realistic message body based on *body_context* type.

    Reads from ctx.base["prescriptions"] and ctx.base["providers"] so it must
    be called after those builders have run.
    """
    btype = body_context.get("type", "")
    prescriptions = [rx for rx in ctx.base.get("prescriptions", []) if rx.get("status") == "active"]
    providers = ctx.base.get("providers", [])
    referrals = ctx.base.get("referrals", [])

    if btype == "discharge_summary":
        # List active meds, change one dosage, add one new med, omit one existing med
        if not prescriptions:
            return (
                "Discharge Summary - Medication List:\n"
                "No active medications found in your record.\n"
                "Please contact your care team if you believe this is in error."
            )
        # Work with up to 4 meds for readability
        meds = prescriptions[:4]
        lines = ["Discharge Summary - Medication List:"]
        changed_one = False
        omit_idx = len(meds) - 1  # omit the last active med from the discharge list
        line_number = 1
        for i, rx in enumerate(meds):
            if i == omit_idx:
                continue  # this one is "removed" — not listed on discharge summary
            med_name = rx["medication"]
            freq = rx.get("frequency", "daily")
            if not changed_one and i == 0:
                # Change dosage on first med
                original_dosage = rx.get("dosage", "")
                # Produce a plausibly changed dosage (double or halve)
                try:
                    dose_num = "".join(c for c in original_dosage if c.isdigit())
                    dose_unit = "".join(c for c in original_dosage if not c.isdigit())
                    new_num = int(dose_num) * 2 if int(dose_num) < 100 else int(dose_num) // 2
                    new_dosage = f"{new_num}{dose_unit}"
                except (ValueError, TypeError):
                    new_dosage = original_dosage
                lines.append(
                    f"{line_number}. {med_name.split()[0]} {new_dosage} {freq}"
                    f" (was {original_dosage} - dosage adjusted)"
                )
                changed_one = True
            else:
                lines.append(f"{line_number}. {med_name} {freq} (unchanged)")
            line_number += 1
        # Add one new med not in the current active list
        new_med_name = body_context.get("new_medication_name", "Metformin 500mg")
        new_med_frequency = body_context.get("new_medication_frequency", "twice daily")
        new_med = f"{new_med_name} {new_med_frequency} (NEW - started during hospitalization)"
        lines.append(f"{line_number}. {new_med}")
        # Note the omitted med
        omitted_name = meds[omit_idx]["medication"]
        lines.append(f"Note: {omitted_name} was discontinued during hospitalization.")
        if body_context.get("include_referral_mention"):
            # An explicit `referral_specialty` override wins — downstream callers
            # (e.g. `build_message_threads`) pre-compute a deterministic specialty
            # and set it here so the seed's exposed
            # `context_specialist_provider_ids` target exactly matches the
            # specialty named in the rendered body.
            override_specialty = body_context.get("referral_specialty")
            referral_mentions = [
                ref.get("to_specialty", "specialist").title()
                for ref in referrals
                if ref.get("status") in ("approved", "requested")
            ]
            if override_specialty:
                lines.append(
                    f"Follow-up referral recommended: {str(override_specialty).title()} consultation."
                )
            elif referral_mentions:
                lines.append(
                    "Follow-up referrals noted on discharge: "
                    + ", ".join(sorted(set(referral_mentions[:2])))
                    + ". Please coordinate with your PCP."
                )
            else:
                specialist = next(
                    (p for p in providers if p.get("specialty") not in ("pcp", "billing", "admin")),
                    None,
                )
                if specialist is not None:
                    lines.append(
                        f"Follow-up referral recommended: {specialist.get('specialty', 'specialist').title()} consultation."
                    )
        return "\n".join(lines)

    elif btype in ("formulary_info", "generic_alternative"):
        if not prescriptions:
            return (
                "Formulary Update: Please contact your insurance provider to verify "
                "coverage for your current medications."
            )
        requested_med_name = body_context.get("medication_name")
        new_med_name = body_context.get("new_medication_name")
        include_all_active = bool(body_context.get("include_all_active"))
        alternatives = body_context.get("alternatives", {})
        coverage_map = body_context.get("coverage_status_by_medication", {})
        default_coverage_status = str(body_context.get("coverage_status", "not covered"))
        preferred_pharmacy = body_context.get("preferred_pharmacy")

        def _default_alternative_name(name: str) -> str:
            generic_base = name.split()[0].lower()
            return f"{generic_base.capitalize()} (preferred generic)"

        def _coverage_line(med_name: str, coverage_status: str, alternative: str) -> str:
            coverage_lower = coverage_status.lower()
            if coverage_lower in ("covered", "preferred", "preferred brand", "preferred generic"):
                return f"{med_name}: covered as {coverage_status}."
            return f"{med_name}: {coverage_status}; preferred alternative is {alternative}."

        if include_all_active:
            lines = ["Formulary Review for New Plan:"]
            for rx in prescriptions:
                med_name = rx["medication"]
                alternative = alternatives.get(med_name, _default_alternative_name(med_name))
                coverage_status = str(coverage_map.get(med_name, default_coverage_status))
                lines.append(_coverage_line(med_name, coverage_status, alternative))
            if preferred_pharmacy:
                lines.append(f"Preferred pharmacy for this plan: {preferred_pharmacy}.")
            lines.append("Please let us know which medications need prior authorization.")
            return "\n".join(lines)

        med_name = new_med_name or requested_med_name or prescriptions[0]["medication"]
        alternative = body_context.get("alternative_name", _default_alternative_name(med_name))
        coverage_status = str(coverage_map.get(med_name, default_coverage_status))
        if btype == "formulary_info":
            coverage_lower = coverage_status.lower()
            if coverage_lower in ("covered", "preferred", "preferred brand", "preferred generic"):
                return (
                    f"Formulary Update: The recommended medication {med_name} is covered under your "
                    f"insurance plan as {coverage_status}. You may proceed if you would like to start it."
                )
            return (
                f"Formulary Update: The recommended medication {med_name} is {coverage_status} under "
                f"your insurance plan. The preferred covered alternative is {alternative}. "
                "Please message me if you would like me to prescribe the preferred option instead."
            )
        return (
            f"Cost Optimization Recommendation: The medication option {med_name} has a lower-cost "
            f"alternative available: {alternative}. Switching could reduce your monthly out-of-pocket "
            "cost. Please contact your provider if you would like to authorize the switch."
        )

    elif btype == "bp_medication_adjustment":
        # Find a BP-related med (Lisinopril, Losartan, Amlodipine, etc.) or use first active
        bp_keywords = ("lisinopril", "losartan", "amlodipine", "metoprolol", "atenolol", "valsartan")
        current_medication_name = body_context.get("current_medication_name")
        bp_rx = next(
            (
                rx for rx in prescriptions
                if current_medication_name and current_medication_name.lower() in rx["medication"].lower()
            ),
            None,
        ) or next(
            (rx for rx in prescriptions if any(k in rx["medication"].lower() for k in bp_keywords)),
            prescriptions[0] if prescriptions else None,
        )
        if bp_rx is None:
            return (
                "Based on your recent labs, I'd like to adjust your blood pressure medication. "
                "Please monitor your BP daily and report any dizziness."
            )
        med_name = bp_rx["medication"]
        current_dosage = bp_rx.get("dosage", "current dose")
        new_medication_name = body_context.get("new_medication_name")
        alternative_name = body_context.get("alternative_name")
        coverage_status = str(body_context.get("coverage_status", "covered"))
        if new_medication_name:
            coverage_line = (
                f"Formulary note: {new_medication_name} is covered on your current plan."
                if coverage_status.lower() in ("covered", "preferred", "preferred generic")
                else f"Formulary note: {new_medication_name} is {coverage_status}; preferred covered alternative is {alternative_name}."
            )
            return (
                f"I recommend changing your blood pressure medication from {med_name} to "
                f"{new_medication_name}. Please stop the old dose once you start the new medication "
                f"and monitor your blood pressure daily for the next 2 weeks. {coverage_line}"
            )
        # Produce a new higher dosage
        try:
            dose_num = "".join(c for c in current_dosage if c.isdigit())
            dose_unit = "".join(c for c in current_dosage if not c.isdigit())
            new_num = int(dose_num) * 2 if int(dose_num) < 100 else int(dose_num) + 25
            new_dosage = f"{new_num}{dose_unit}"
        except (ValueError, TypeError):
            new_dosage = "increased dose"
        med_base = med_name.split()[0]
        return (
            f"Based on your recent labs, I'd like to adjust your blood pressure medication. "
            f"Please increase your {med_base} from {current_dosage} to {new_dosage} starting "
            f"next week. Monitor your BP daily and report any dizziness or lightheadedness."
        )

    elif btype == "referral_details":
        # Find a non-PCP, non-billing, non-admin specialist provider
        specialist = next(
            (p for p in providers if p.get("specialty") not in ("pcp", "billing", "admin")),
            None,
        )
        if specialist is None:
            return (
                "Referral Information: A specialist referral has been submitted for you. "
                "Please check the referrals section of your portal for details and contact "
                "your care team with any questions."
            )
        name = specialist.get("name", "Specialist")
        specialty = specialist.get("specialty", "specialist").title()
        return (
            f"Referral Details: I have submitted a referral for you to see {name} "
            f"in our {specialty} department. The referral has been sent to your insurance "
            f"for prior authorization. You should receive approval within 3-5 business days. "
            f"Once approved, please call the {specialty} office to schedule your appointment."
        )

    # Fallback — should not normally be reached
    return ctx.fake.paragraph(nb_sentences=ctx.rng.randint(2, 4))


# ---------------------------------------------------------------------------
# 7. message_threads
# ---------------------------------------------------------------------------

@_register("message_threads")
def build_message_threads(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create message threads with realistic clinical conversations.

    Params: thread_count (int), unread_count (int), categories (list[str]),
            include_billing (bool), include_rx_renewal (bool),
            body_context (dict) — optional; injects specific content into the
            first provider message of the first clinical thread.
            unread_by_category (dict[str, int]) — optional; for each
            ``category -> N`` entry, create N *dedicated* threads (in addition
            to ``thread_count``) whose final message is an UNREAD provider
            message in that exact category. This guarantees a deterministic,
            category-labelled spread of unread messages so a task can target a
            computed subset (e.g. "mark only the unread clinical/scheduling
            messages, leave billing unread") rather than the all-or-nothing
            ``mark-all-read`` shortcut. The category-split ids are exposed via
            ``unread_msg_ids_by_category`` plus convenience scalar lists.
    Outputs: thread_ids, unread_msg_ids, billing_thread_id, rx_renewal_thread_id,
             all_msg_ids, unread_msg_ids_by_category
    """
    thread_count = params.get("thread_count", 3)
    unread_count = params.get("unread_count", 2)
    categories = params.get("categories", ["clinical"])
    include_billing = params.get("include_billing", False)
    include_rx_renewal = params.get("include_rx_renewal", False)
    # Dedicated per-category unread threads. Maps category -> count. Each entry
    # yields exactly `count` threads whose final provider message is unread and
    # carries that category. Purely additive — tasks that omit it are
    # unaffected.
    unread_by_category: dict[str, int] = dict(params.get("unread_by_category", {}) or {})
    body_context: dict[str, Any] | None = params.get("body_context")
    body_contexts: list[dict[str, Any]] = [dict(item) for item in params.get("body_contexts", [])]
    if body_context:
        body_contexts.insert(0, dict(body_context))

    if "messages" not in ctx.base:
        ctx.base["messages"] = []

    providers = ctx.base.get("providers", [])
    clinical_providers = [p for p in providers if p.get("specialty") not in ("billing", "admin")]
    billing_providers = [p for p in providers if p.get("specialty") == "billing"]
    pcp_id = ctx.base.get("patient", {}).get("pcp_id", "prov_1")

    thread_ids: list[str] = []
    unread_msg_ids: list[str] = []
    all_msg_ids: list[str] = []
    # Map of category -> list of UNREAD message ids in that category. Populated
    # both by the main loop (when its randomly-assigned unread message lands in
    # a category) and by the dedicated `unread_by_category` threads below.
    unread_msg_ids_by_category: dict[str, list[str]] = {}
    # Map of body_context type → the id of the first provider message in the
    # thread seeded for that context. Downstream tasks (e.g.
    # pp_respond_to_provider) use this to identify the specific incoming
    # message the agent must read.
    context_msg_ids: dict[str, str] = {}
    # Per-context-type: the specialty string named inside the rendered body
    # (currently populated for `discharge_summary` contexts that include a
    # referral mention) and the list of provider ids matching that specialty
    # in the seeded directory. Enables downstream tasks (e.g.
    # pp_post_hospitalization) to look up the single legitimate set of
    # "specialist follow-up" providers without re-parsing the body text.
    context_specialties: dict[str, str] = {}
    context_specialist_provider_ids: dict[str, list[str]] = {}
    # Per-context-type: for `discharge_summary` contexts, the rx id the body
    # text flags as "discontinued during hospitalization". Mirrors
    # `_generate_contextual_body`'s logic — the omitted rx is the last entry
    # of the first 4 active prescriptions, so downstream tasks (e.g.
    # pp_medication_reconciliation) can identify the exact rx the agent must
    # route through the renewal flow without re-parsing the body string.
    context_discontinued_rx_ids: dict[str, list[str]] = {}
    billing_thread_id: str | None = None
    rx_renewal_thread_id: str | None = None
    unread_assigned = 0

    for t in range(thread_count):
        thread_id = ctx.next_id("thread")
        thread_ids.append(thread_id)
        thread_context = body_contexts.pop(0) if body_contexts else None

        # Decide category for this thread
        if thread_context:
            cat = str(thread_context.get("category", "clinical"))
        elif include_billing and billing_thread_id is None and t == thread_count - 2:
            cat = "billing"
        elif include_rx_renewal and rx_renewal_thread_id is None and t == thread_count - 1:
            cat = "rx_renewal"
        elif categories:
            cat = ctx.rng.choice(categories)
        else:
            cat = "clinical"

        # Pick provider for the thread
        if thread_context:
            resolved_provider = _resolve_context_provider(ctx, thread_context, clinical_providers, pcp_id)
            prov_id = resolved_provider["id"] if resolved_provider else pcp_id
        elif cat == "billing" and billing_providers:
            prov_id = billing_providers[0]["id"]
        elif clinical_providers:
            prov_id = ctx.rng.choice(clinical_providers)["id"]
        else:
            prov_id = pcp_id

        # Pick subject — override with body_context type subject for the first clinical thread
        if cat == "billing":
            subject = ctx.rng.choice(_BILLING_SUBJECTS)
        elif cat == "rx_renewal":
            subject = ctx.rng.choice(_RX_RENEWAL_SUBJECTS)
        elif thread_context:
            subject = thread_context.get(
                "subject",
                _BODY_CONTEXT_SUBJECTS.get(thread_context.get("type", ""), ctx.rng.choice(_CLINICAL_SUBJECTS)),
            )
        else:
            subject = ctx.rng.choice(_CLINICAL_SUBJECTS)

        # For discharge_summary contexts with include_referral_mention, pick a
        # deterministic specialist specialty up-front so (a) the rendered body
        # mentions an exact specialty string and (b) the seeder can export the
        # matching specialist provider ids as a target for downstream tasks.
        if (
            thread_context
            and str(thread_context.get("type", "")) == "discharge_summary"
            and thread_context.get("include_referral_mention")
            and "referral_specialty" not in thread_context
        ):
            ctx_type = str(thread_context.get("type", ""))
            existing_referral_specs = [
                ref.get("to_specialty")
                for ref in ctx.base.get("referrals", [])
                if ref.get("status") in ("approved", "requested") and ref.get("to_specialty")
            ]
            chosen_specialty: str | None = None
            if existing_referral_specs:
                chosen_specialty = str(existing_referral_specs[0])
            else:
                # Reproduce the fallback used by `_generate_contextual_body`:
                # first non-pcp/billing/admin provider.
                fallback_specialist = next(
                    (
                        p for p in providers
                        if p.get("specialty") not in ("pcp", "billing", "admin")
                    ),
                    None,
                )
                if fallback_specialist is not None:
                    chosen_specialty = fallback_specialist.get("specialty")
            if chosen_specialty:
                thread_context["referral_specialty"] = chosen_specialty
                context_specialties[ctx_type] = chosen_specialty
                context_specialist_provider_ids[ctx_type] = [
                    p["id"] for p in providers
                    if p.get("specialty") == chosen_specialty
                ]

        # For discharge_summary contexts, pre-compute which active rx the body
        # will flag as "discontinued during hospitalization". The body
        # generator operates on the first 4 active prescriptions and omits the
        # last one (`meds[omit_idx]`); mirror that logic exactly so the target
        # matches the text the agent reads.
        if (
            thread_context
            and str(thread_context.get("type", "")) == "discharge_summary"
        ):
            ctx_type = str(thread_context.get("type", ""))
            active_rxes = [
                rx for rx in ctx.base.get("prescriptions", [])
                if rx.get("status") == "active"
            ]
            if active_rxes:
                meds_subset = active_rxes[:4]
                discontinued_rx = meds_subset[-1]
                context_discontinued_rx_ids.setdefault(ctx_type, []).append(
                    discontinued_rx["id"]
                )

        # Create 2-4 messages per thread (alternating provider/patient)
        msgs_in_thread = ctx.rng.randint(2, 4)
        for m in range(msgs_in_thread):
            msg_id = ctx.next_id("msg")
            from_type = "provider" if m % 2 == 0 else "patient"
            timestamp = ctx.now - timedelta(
                days=ctx.rng.randint(0, 14),
                hours=ctx.rng.randint(0, 23),
            )

            # Last message in unread threads should be unread (from provider)
            is_last = m == msgs_in_thread - 1
            is_read = True
            if is_last and from_type == "provider" and unread_assigned < unread_count:
                is_read = False
                unread_assigned += 1
            # Force-unread the first provider message of a contextual thread
            # (this is the seed carrier for the task's clinical content — if
            # the task asks the agent to read it, it must actually be unread).
            if thread_context and from_type == "provider" and m == 0 and is_read:
                is_read = False
                unread_assigned += 1

            # Inject contextual body for contextual threads; keep subsequent messages relevant
            if thread_context and from_type == "provider" and m == 0:
                body = _generate_contextual_body(ctx, thread_context)
            elif thread_context and from_type == "patient":
                body = "Thank you, I've reviewed this and will follow up as needed."
            elif thread_context and from_type == "provider" and m > 0:
                body = "Please let me know if you have any questions about the information above or your current medications."
            else:
                body = ctx.fake.paragraph(nb_sentences=ctx.rng.randint(2, 4))

            msg_dict = {
                "id": msg_id,
                "from_type": from_type,
                "provider_id": prov_id,
                "subject": subject,
                "body": body,
                "thread_id": thread_id,
                "timestamp": timestamp.isoformat(),
                "is_read": is_read,
                "category": cat,
            }
            ctx.base["messages"].append(msg_dict)
            all_msg_ids.append(msg_id)
            if not is_read:
                unread_msg_ids.append(msg_id)
                unread_msg_ids_by_category.setdefault(cat, []).append(msg_id)
            # Record context-keyed id for the first provider message of
            # contextual threads (e.g. bp_medication_adjustment → msg_X).
            if thread_context and from_type == "provider" and m == 0:
                ctx_type = str(thread_context.get("type", ""))
                if ctx_type and ctx_type not in context_msg_ids:
                    context_msg_ids[ctx_type] = msg_id

        if cat == "billing":
            billing_thread_id = thread_id
        elif cat == "rx_renewal":
            rx_renewal_thread_id = thread_id

    # --- Dedicated per-category unread threads -------------------------------
    # For every `category -> N` entry in `unread_by_category`, build N threads
    # whose final message is an UNREAD provider message in that exact category.
    # Each thread is a deterministic 3-message conversation
    # (provider -> patient -> provider) so the last message is always from the
    # provider and is the one left unread. This guarantees a known,
    # category-labelled spread of unread messages for tasks that target a
    # computed subset of the inbox rather than every unread message.
    _SUBJECTS_BY_CATEGORY: dict[str, list[str]] = {
        "billing": _BILLING_SUBJECTS,
        "rx_renewal": _RX_RENEWAL_SUBJECTS,
        "clinical": _CLINICAL_SUBJECTS,
    }
    for ded_cat in sorted(unread_by_category.keys()):
        ded_count = int(unread_by_category[ded_cat])
        for _ded_i in range(ded_count):
            thread_id = ctx.next_id("thread")
            thread_ids.append(thread_id)
            if ded_cat == "billing" and billing_providers:
                ded_prov_id = billing_providers[0]["id"]
            elif clinical_providers:
                ded_prov_id = ctx.rng.choice(clinical_providers)["id"]
            else:
                ded_prov_id = pcp_id
            ded_subjects = _SUBJECTS_BY_CATEGORY.get(ded_cat, _CLINICAL_SUBJECTS)
            ded_subject = ctx.rng.choice(ded_subjects)
            for ded_m in range(3):
                msg_id = ctx.next_id("msg")
                from_type = "provider" if ded_m % 2 == 0 else "patient"
                timestamp = ctx.now - timedelta(
                    days=ctx.rng.randint(0, 14),
                    hours=ctx.rng.randint(0, 23),
                )
                is_last = ded_m == 2
                is_read = not is_last  # only the final provider message is unread
                if from_type == "provider":
                    body = ctx.fake.paragraph(nb_sentences=ctx.rng.randint(2, 4))
                else:
                    body = "Thank you, I've reviewed this and will follow up as needed."
                msg_dict = {
                    "id": msg_id,
                    "from_type": from_type,
                    "provider_id": ded_prov_id,
                    "subject": ded_subject,
                    "body": body,
                    "thread_id": thread_id,
                    "timestamp": timestamp.isoformat(),
                    "is_read": is_read,
                    "category": ded_cat,
                }
                ctx.base["messages"].append(msg_dict)
                all_msg_ids.append(msg_id)
                if not is_read:
                    unread_msg_ids.append(msg_id)
                    unread_msg_ids_by_category.setdefault(ded_cat, []).append(msg_id)
            if ded_cat == "billing" and billing_thread_id is None:
                billing_thread_id = thread_id
            elif ded_cat == "rx_renewal" and rx_renewal_thread_id is None:
                rx_renewal_thread_id = thread_id

    return {
        "thread_ids": thread_ids,
        "unread_msg_ids": unread_msg_ids,
        "billing_thread_id": billing_thread_id,
        "rx_renewal_thread_id": rx_renewal_thread_id,
        "all_msg_ids": all_msg_ids,
        # Per-category list of UNREAD message ids (e.g.
        # {"clinical": ["msg_3"], "billing": ["msg_9"]}). Lets a task target a
        # computed subset of the inbox without re-deriving categories inside a
        # predicate.
        "unread_msg_ids_by_category": unread_msg_ids_by_category,
        # Per-body-context-type id of the first provider message for that
        # context (e.g. {"bp_medication_adjustment": "msg_1"}). Empty when
        # no `body_context`/`body_contexts` was supplied.
        "context_msg_ids": context_msg_ids,
        # Per-body-context-type specialty named in the rendered body (currently
        # populated for `discharge_summary` contexts with a referral mention).
        # Empty when no such context exists.
        "context_specialties": context_specialties,
        # Per-body-context-type list of provider ids whose specialty matches
        # the specialty named in the rendered body. Empty when no such
        # context exists.
        "context_specialist_provider_ids": context_specialist_provider_ids,
        # Per-body-context-type list of prescription ids the body flags as
        # "discontinued during hospitalization" (populated for
        # `discharge_summary` contexts).
        "context_discontinued_rx_ids": context_discontinued_rx_ids,
    }


# ---------------------------------------------------------------------------
# 7b. insurance_card_message
# ---------------------------------------------------------------------------

# Deterministic carrier/plan pools for the new-insurance scenario. These are
# distinct from the tier-derived plan names in `patient_profile` so the
# authoritative new plan is unambiguously different from the patient's
# existing (seeded) plan.
_INSURANCE_CARRIERS: list[dict[str, str]] = [
    {"carrier": "Aetna", "tier": "PPO Silver", "prefix": "AET"},
    {"carrier": "Cigna", "tier": "HMO Gold", "prefix": "CIG"},
    {"carrier": "United Healthcare", "tier": "PPO Platinum", "prefix": "UHC"},
    {"carrier": "Humana", "tier": "EPO Bronze", "prefix": "HUM"},
    {"carrier": "Kaiser Permanente", "tier": "HMO Plus", "prefix": "KP"},
]


@_register("insurance_card_message")
def build_insurance_card_message(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Seed two billing messages that carry insurance-card details.

    The *current* (authoritative) message is the MOST RECENT billing message in
    the inbox and contains the new plan name, member id, group number, and an
    updated contact phone/email. An *older* (stale, superseded) message carries
    DIFFERENT outdated values — a grounding decoy. Neither value set appears in
    the task instruction, so the agent must read the inbox, disambiguate the
    most-recent billing message from the stale one, and extract the exact
    strings to apply.

    Must run AFTER ``provider_directory`` (needs a billing/PCP provider) and
    is independent of ``message_threads`` (it appends its own messages).

    Params:
        stale_offset_days (int): how many days BEFORE the authoritative message
            the stale decoy is timestamped (default 21).
        current_offset_days (int): how many days BEFORE ``ctx.now`` the
            authoritative message is timestamped (default 1).
    Outputs: new_plan_name, new_member_id, new_group_number, new_phone,
             new_email, stale_plan_name, stale_member_id, stale_group_number,
             current_card_msg_id, stale_card_msg_id, billing_provider_id
    """
    stale_offset_days = int(params.get("stale_offset_days", 21))
    current_offset_days = int(params.get("current_offset_days", 1))

    providers = ctx.base.get("providers", [])
    billing_prov = next(
        (p for p in providers if p.get("specialty") == "billing"), None
    )
    pcp_id = ctx.base.get("patient", {}).get("pcp_id", "prov_1")
    billing_provider_id = billing_prov["id"] if billing_prov else pcp_id

    if "messages" not in ctx.base:
        ctx.base["messages"] = []

    # Pick two DISTINCT carriers deterministically: index 0 = authoritative,
    # index 1 = stale decoy.
    pool = list(_INSURANCE_CARRIERS)
    ctx.rng.shuffle(pool)
    current = pool[0]
    stale = pool[1]

    def _plan(c: dict[str, str]) -> str:
        return f"{c['carrier']} {c['tier']}"

    def _member(c: dict[str, str]) -> str:
        return f"{c['prefix']}-{ctx.rng.randint(1000000, 9999999)}"

    def _group(c: dict[str, str]) -> str:
        return f"GRP-{ctx.rng.randint(10000, 99999)}"

    new_plan_name = _plan(current)
    new_member_id = _member(current)
    new_group_number = _group(current)
    new_phone = f"(555) {ctx.rng.randint(200, 999)}-{ctx.rng.randint(1000, 9999)}"
    # Deterministic new contact email derived from the patient name + new
    # carrier domain so it is clearly distinct from the patient's seeded email.
    domain = "".join(ch for ch in current["carrier"].lower() if ch.isalpha()) + "mail.com"
    patient_name = ctx.base.get("patient", {}).get("name", "member")
    new_email = ctx.email_for_name(patient_name, domain)

    stale_plan_name = _plan(stale)
    stale_member_id = _member(stale)
    stale_group_number = _group(stale)
    stale_phone = f"(555) {ctx.rng.randint(200, 999)}-{ctx.rng.randint(1000, 9999)}"

    current_ts = ctx.now - timedelta(days=current_offset_days)
    stale_ts = ctx.now - timedelta(days=stale_offset_days)

    stale_msg_id = ctx.next_id("msg")
    stale_thread_id = ctx.next_id("thread")
    stale_body = (
        "Insurance Card Update (SUPERSEDED):\n"
        f"Plan name: {stale_plan_name}\n"
        f"Member ID: {stale_member_id}\n"
        f"Group number: {stale_group_number}\n"
        f"Benefits hotline: {stale_phone}\n"
        "NOTE: This card was issued in a prior enrollment period and has been "
        "replaced. Please disregard if you have received a newer card update."
    )
    ctx.base["messages"].append({
        "id": stale_msg_id,
        "from_type": "provider",
        "provider_id": billing_provider_id,
        "subject": "Insurance Card Update",
        "body": stale_body,
        "thread_id": stale_thread_id,
        "timestamp": stale_ts.isoformat(),
        "is_read": True,
        "category": "billing",
    })

    current_msg_id = ctx.next_id("msg")
    current_thread_id = ctx.next_id("thread")
    current_body = (
        "Your New Insurance Card — Effective Immediately:\n"
        f"Plan name: {new_plan_name}\n"
        f"Member ID: {new_member_id}\n"
        f"Group number: {new_group_number}\n"
        "Please also update your contact details on file to the ones we have "
        "for your new plan:\n"
        f"Contact phone: {new_phone}\n"
        f"Contact email: {new_email}\n"
        "Update your profile so claims route correctly under the new plan."
    )
    ctx.base["messages"].append({
        "id": current_msg_id,
        "from_type": "provider",
        "provider_id": billing_provider_id,
        "subject": "New Insurance Card on File",
        "body": current_body,
        "thread_id": current_thread_id,
        "timestamp": current_ts.isoformat(),
        "is_read": False,
        "category": "billing",
    })

    return {
        "new_plan_name": new_plan_name,
        "new_member_id": new_member_id,
        "new_group_number": new_group_number,
        "new_phone": new_phone,
        "new_email": new_email,
        "stale_plan_name": stale_plan_name,
        "stale_member_id": stale_member_id,
        "stale_group_number": stale_group_number,
        "current_card_msg_id": current_msg_id,
        "stale_card_msg_id": stale_msg_id,
        "billing_provider_id": billing_provider_id,
    }


# ---------------------------------------------------------------------------
# 8. referral_chain
# ---------------------------------------------------------------------------

@_register("referral_chain")
def build_referral_chain(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create referrals in various states.

    Params: approved_count (int), pending_count (int), denied_count (int),
            with_prior_auth (bool), expiring_soon (bool),
            must_have_specialties (list[str]) — approved referrals guaranteed for these specialties,
            extra_specialty_referrals (list[dict]) — additional decoy referrals appended
              AFTER the guaranteed/approved batch. Each dict accepts keys
              ``specialty`` (str), ``status`` (str: approved|requested|denied),
              ``prior_auth`` (bool, default False), ``prior_auth_status`` (str,
              optional override e.g. "pending"|"approved"|"denied"), and
              ``pin_to_specialty_provider`` (bool, default True — force
              ``to_provider_id`` to a provider that actually matches the
              referral specialty rather than borrowing a candidate appointment's
              provider). These are intentionally placed after the eligible
              approved referral so the create-appointment gate (which picks the
              FIRST approved referral for a specialty) still resolves to the
              eligible one, while the directory shows several same-specialty
              referrals the agent must disambiguate.
            pin_must_have_providers (bool, default False) — when True, every
              ``must_have_specialties`` approved referral pins its
              ``to_provider_id`` to a provider whose specialty matches, instead
              of inheriting an unrelated candidate appointment's provider.
    Outputs: approved_ref_ids, pending_ref_ids, denied_ref_ids,
             prior_auth_ref_id, expiring_ref_id,
             eligible_approved_ref_ids (approved referrals that clear the
               scheduling gate: not prior_auth_required OR prior_auth_status ==
               "approved"),
             eligible_ref_id_by_specialty (specialty → first eligible approved
               referral id),
             eligible_provider_id_by_specialty (specialty → that referral's
               to_provider_id),
             ineligible_approved_ref_ids (approved referrals blocked by an
               unapproved prior-auth — decoys the agent must NOT link).
    """
    approved_count = params.get("approved_count", 1)
    pending_count = params.get("pending_count", 1)
    denied_count = params.get("denied_count", 0)
    with_prior_auth = params.get("with_prior_auth", False)
    expiring_soon = params.get("expiring_soon", False)
    must_have_specialties: list[str] = list(params.get("must_have_specialties", []))
    extra_specialty_referrals: list[dict[str, Any]] = [
        dict(item) for item in (params.get("extra_specialty_referrals") or [])
    ]
    pin_must_have_providers = bool(params.get("pin_must_have_providers", False))

    if "referrals" not in ctx.base:
        ctx.base["referrals"] = []

    providers = ctx.base.get("providers", [])
    providers_by_id = {p["id"]: p for p in providers}
    appointments = ctx.base.get("appointments", [])
    pcp_id = ctx.base.get("patient", {}).get("pcp_id", "prov_1")
    specialist_specs = [p for p in providers if p.get("specialty") not in ("pcp", "billing", "admin")]

    # Specialties available for referrals
    available_specialties = list({p["specialty"] for p in specialist_specs}) or ["cardiology", "dermatology"]

    approved_ref_ids: list[str] = []
    pending_ref_ids: list[str] = []
    denied_ref_ids: list[str] = []
    prior_auth_ref_id: str | None = None
    expiring_ref_id: str | None = None

    def _make_ref(status: str, expires_days: int, prior_auth: bool = False,
                  specialty: str | None = None,
                  pin_to_specialty_provider: bool = False,
                  prior_auth_status_override: str | None = None) -> dict[str, Any]:
        ref_id = ctx.next_id("ref")
        candidate_appointments = [
            apt for apt in appointments
            if apt.get("status") in ("scheduled", "completed")
            and not apt.get("linked_referral_id")
            and providers_by_id.get(apt.get("provider_id"), {}).get("specialty") not in ("pcp", "billing", "admin")
        ]
        preferred_appointment = candidate_appointments[0] if candidate_appointments else None
        if specialty is None and preferred_appointment is not None:
            specialty = providers_by_id[preferred_appointment["provider_id"]]["specialty"]
        if specialty is None:
            specialty = ctx.rng.choice(available_specialties)
        if pin_to_specialty_provider:
            # Force the referral to point at a provider that actually matches
            # the referral specialty (deterministic: first matching provider),
            # not an unrelated candidate appointment's provider. Required when
            # a task pins the appointment's provider_id to the referral's
            # to_provider_id and that provider must own the bookable slots.
            preferred_appointment = None
            to_prov = next(
                (p for p in specialist_specs if p["specialty"] == specialty),
                None,
            )
        else:
            to_prov = (
                providers_by_id.get(preferred_appointment["provider_id"])
                if preferred_appointment is not None
                else next((p for p in specialist_specs if p["specialty"] == specialty), None)
            )
        to_prov_id = to_prov["id"] if to_prov else None
        linked_appointment = preferred_appointment or next(
            (
                apt for apt in appointments
                if apt.get("status") in ("scheduled", "completed")
                and not apt.get("linked_referral_id")
                and (
                    (to_prov_id is not None and apt.get("provider_id") == to_prov_id)
                    or specialty == providers_by_id.get(apt.get("provider_id"), {}).get("specialty")
                )
            ),
            None,
        )
        if linked_appointment is not None:
            linked_appointment["linked_referral_id"] = ref_id
        reason = ctx.rng.choice([
            "Specialist consultation",
            "Further evaluation needed",
            "Follow-up recommended by PCP",
            "Diagnostic imaging required",
        ])
        prior_auth_status = "not_required"
        if prior_auth:
            prior_auth_status = "approved" if status == "approved" else "pending"
        if prior_auth_status_override is not None:
            prior_auth_status = prior_auth_status_override

        return {
            "id": ref_id,
            "from_provider_id": pcp_id,
            "to_specialty": specialty,
            "to_provider_id": to_prov_id,
            "reason": reason,
            "status": status,
            "prior_auth_required": prior_auth,
            "prior_auth_status": prior_auth_status,
            "expires_at": (ctx.now + timedelta(days=expires_days)).isoformat(),
            "notes": "",
            "linked_appointment_id": linked_appointment["id"] if linked_appointment else None,
        }

    # Approved — guarantee must_have_specialties first, then fill remaining randomly
    guaranteed = list(must_have_specialties)  # consume in order
    for i in range(approved_count):
        needs_auth = with_prior_auth and prior_auth_ref_id is None and i == 0
        forced_specialty = guaranteed.pop(0) if guaranteed else None
        ref = _make_ref("approved", ctx.rng.randint(60, 180), prior_auth=needs_auth,
                        specialty=forced_specialty,
                        pin_to_specialty_provider=pin_must_have_providers
                        and forced_specialty is not None)
        ctx.base["referrals"].append(ref)
        approved_ref_ids.append(ref["id"])
        if needs_auth:
            prior_auth_ref_id = ref["id"]

    # Pending
    for _ in range(pending_count):
        ref = _make_ref("requested", ctx.rng.randint(30, 90))
        ctx.base["referrals"].append(ref)
        pending_ref_ids.append(ref["id"])

    # Denied
    for _ in range(denied_count):
        ref = _make_ref("denied", ctx.rng.randint(30, 90))
        ctx.base["referrals"].append(ref)
        denied_ref_ids.append(ref["id"])

    # Extra decoy referrals — appended AFTER the eligible approved batch so the
    # scheduling gate (which resolves the FIRST approved same-specialty
    # referral) keeps pointing at the eligible one. These deliberately mimic an
    # eligible referral (same specialty, sometimes status="approved") while
    # being blocked by an unapproved prior-auth or a non-approved status, so the
    # agent must disambiguate rather than pattern-match on specialty alone.
    for spec_ref in extra_specialty_referrals:
        e_specialty = spec_ref.get("specialty")
        e_status = str(spec_ref.get("status", "requested"))
        e_prior_auth = bool(spec_ref.get("prior_auth", False))
        e_pa_override = spec_ref.get("prior_auth_status")
        e_pin = bool(spec_ref.get("pin_to_specialty_provider", True))
        ref = _make_ref(
            e_status,
            ctx.rng.randint(60, 180),
            prior_auth=e_prior_auth,
            specialty=e_specialty,
            pin_to_specialty_provider=e_pin and e_specialty is not None,
            prior_auth_status_override=e_pa_override,
        )
        ctx.base["referrals"].append(ref)
        if e_status == "approved":
            approved_ref_ids.append(ref["id"])
        elif e_status == "requested":
            pending_ref_ids.append(ref["id"])
        elif e_status == "denied":
            denied_ref_ids.append(ref["id"])

    # Expiring soon referral
    if expiring_soon:
        ref = _make_ref("approved", ctx.rng.randint(3, 10))
        ctx.base["referrals"].append(ref)
        approved_ref_ids.append(ref["id"])
        expiring_ref_id = ref["id"]

    # ----------------------------------------------------------------------
    # Eligibility computation. An approved referral clears the
    # create-appointment gate iff it is approved AND (prior-auth not required
    # OR prior-auth already approved). Tasks that pin the appointment's
    # linked_referral_id to an EXACT eligible referral (rather than "any
    # neurology referral") need this precomputed so a canonical_diff predicate
    # never reconstructs the eligibility filter inside a comprehension scope
    # (Class 6/8 hazard).
    # ----------------------------------------------------------------------
    referrals_now = ctx.base["referrals"]
    by_id = {r["id"]: r for r in referrals_now}

    def _is_eligible(r: dict[str, Any]) -> bool:
        return (
            r.get("status") == "approved"
            and (
                not r.get("prior_auth_required")
                or r.get("prior_auth_status") == "approved"
            )
        )

    eligible_approved_ref_ids = [rid for rid in approved_ref_ids if _is_eligible(by_id[rid])]
    ineligible_approved_ref_ids = [
        rid for rid in approved_ref_ids if not _is_eligible(by_id[rid])
    ]
    eligible_ref_id_by_specialty: dict[str, str] = {}
    eligible_provider_id_by_specialty: dict[str, str | None] = {}
    eligible_ref_ids_by_specialty: dict[str, list[str]] = {}
    for rid in eligible_approved_ref_ids:
        r = by_id[rid]
        spec = r.get("to_specialty")
        if spec is None:
            continue
        eligible_ref_ids_by_specialty.setdefault(spec, []).append(rid)
        if spec not in eligible_ref_id_by_specialty:
            eligible_ref_id_by_specialty[spec] = rid
            eligible_provider_id_by_specialty[spec] = r.get("to_provider_id")

    return {
        "approved_ref_ids": approved_ref_ids,
        "pending_ref_ids": pending_ref_ids,
        "denied_ref_ids": denied_ref_ids,
        "prior_auth_ref_id": prior_auth_ref_id,
        "expiring_ref_id": expiring_ref_id,
        "eligible_approved_ref_ids": eligible_approved_ref_ids,
        "ineligible_approved_ref_ids": ineligible_approved_ref_ids,
        "eligible_ref_id_by_specialty": eligible_ref_id_by_specialty,
        "eligible_provider_id_by_specialty": eligible_provider_id_by_specialty,
        "eligible_ref_ids_by_specialty": eligible_ref_ids_by_specialty,
    }


# ---------------------------------------------------------------------------
# 9. insurance_claims
# ---------------------------------------------------------------------------

@_register("insurance_claims")
def build_insurance_claims(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create insurance claims in various statuses.

    Params: approved_count (int), denied_count (int), processing_count (int),
            with_eob (bool), near_appeal_deadline (bool),
            approved_no_eob_count (int), approved_zero_resp_count (int)
    Outputs: approved_claim_ids, denied_claim_ids, processing_claim_ids,
             appealable_claim_id, most_recent_denied_claim_id,
             top_3_appealable_claim_ids, payable_approved_claim_ids,
             total_patient_responsibility

    ``approved_no_eob_count`` adds N approved claims whose ``eob_available`` is
    forced False regardless of ``with_eob`` (the EOB cannot be reviewed yet, so
    these are NOT payable in an "review EOB then pay" workflow even though they
    carry a positive balance).

    ``approved_zero_resp_count`` adds N approved claims that are fully covered
    (``amount_covered == amount_billed`` ⇒ ``patient_responsibility == 0``).
    The ``/claims/{id}/pay`` route rejects these (422 — "No patient
    responsibility to pay"), so they must NOT be acted on.

    ``payable_approved_claim_ids`` is the precomputed discriminator a task can
    drive a saturating bijection over: every approved claim that has an
    available EOB AND a strictly-positive ``patient_responsibility``. Computing
    it here keeps the canonical_diff free of filter-scope date/eob math
    (hazard: ``where``/``filter`` scopes see only id + changed fields, or
    ``a`` + ``target``).
    """
    approved_count = params.get("approved_count", 1)
    denied_count = params.get("denied_count", 0)
    processing_count = params.get("processing_count", 0)
    with_eob = params.get("with_eob", False)
    near_appeal_deadline = params.get("near_appeal_deadline", False)
    approved_no_eob_count = params.get("approved_no_eob_count", 0)
    approved_zero_resp_count = params.get("approved_zero_resp_count", 0)
    # Optional: denied claims that look like appeal candidates but are NOT
    # eligible (deadline already passed, or no EOB issued). These are decoys
    # that force the agent to apply the full eligibility filter
    # (status==denied AND eob_available AND appeal_deadline >= now) rather
    # than acting on every denied claim. Backward-compatible: defaults to 0.
    expired_denied_count = params.get("expired_denied_count", 0)
    no_eob_denied_count = params.get("no_eob_denied_count", 0)

    if "claims" not in ctx.base:
        ctx.base["claims"] = []

    providers = ctx.base.get("providers", [])
    providers_by_id = {p["id"]: p for p in providers}
    appointments = ctx.base.get("appointments", [])
    lab_results = ctx.base.get("lab_results", [])
    referrals = ctx.base.get("referrals", [])
    clinical_completed_apts = [
        a for a in appointments
        if a.get("status") == "completed"
        and providers_by_id.get(a.get("provider_id"), {}).get("specialty") not in ("billing", "admin")
    ]
    completed_apts = clinical_completed_apts or [a for a in appointments if a.get("status") == "completed"]
    used_appointment_ids: set[str] = set()
    labs_by_appointment: dict[str, list[str]] = {}
    for lab in lab_results:
        linked_appointment_id = lab.get("linked_appointment_id")
        if linked_appointment_id:
            labs_by_appointment.setdefault(linked_appointment_id, []).append(lab["id"])

    approved_claim_ids: list[str] = []
    denied_claim_ids: list[str] = []
    processing_claim_ids: list[str] = []
    appealable_claim_id: str | None = None
    total_patient_responsibility = Decimal("0")

    # Procedure and diagnosis code pools
    proc_codes = ["99213", "99214", "99215", "93000", "80053", "71046", "36415"]
    diag_codes = ["E11.65", "I10", "J06.9", "M54.5", "Z00.00", "K21.0", "R51"]

    def _find_supporting_referral(apt: dict[str, Any] | None) -> str | None:
        if apt is None:
            return None
        if apt.get("linked_referral_id"):
            return apt["linked_referral_id"]
        apt_provider = apt.get("provider_id")
        apt_specialty = providers_by_id.get(apt_provider, {}).get("specialty")
        for ref in referrals:
            if ref.get("linked_appointment_id") == apt.get("id"):
                return ref["id"]
            if ref.get("to_provider_id") == apt_provider or ref.get("to_specialty") == apt_specialty:
                return ref["id"]
        return None

    def _pick_completed_appointment(prefer_referral: bool) -> dict[str, Any] | None:
        candidate_groups: list[list[dict[str, Any]]] = []
        if prefer_referral:
            candidate_groups.append([
                apt for apt in completed_apts
                if apt["id"] not in used_appointment_ids and _find_supporting_referral(apt) is not None
            ])
        else:
            candidate_groups.append([
                apt for apt in completed_apts
                if apt["id"] not in used_appointment_ids and _find_supporting_referral(apt) is None
            ])
        candidate_groups.append([apt for apt in completed_apts if apt["id"] not in used_appointment_ids])
        if prefer_referral:
            candidate_groups.append([apt for apt in completed_apts if _find_supporting_referral(apt) is not None])
        else:
            candidate_groups.append([apt for apt in completed_apts if _find_supporting_referral(apt) is None])
        candidate_groups.append(completed_apts)
        for group in candidate_groups:
            if group:
                apt = group[0]
                used_appointment_ids.add(apt["id"])
                return apt
        return None

    def _make_claim(status: str, appeal_days: int, eob: bool = False,
                    zero_resp: bool = False) -> dict[str, Any]:
        clm_id = ctx.next_id("clm")
        service_date = (ctx.now - timedelta(days=ctx.rng.randint(7, 120))).date()

        # Link to a completed appointment if available
        apt = _pick_completed_appointment(prefer_referral=(status == "denied"))
        apt_id = apt["id"] if apt else ctx.next_id("apt")
        prov_id = apt["provider_id"] if apt else (ctx.rng.choice([p["id"] for p in providers]) if providers else "prov_1")
        supporting_referral_id = _find_supporting_referral(apt)
        supporting_lab_ids = labs_by_appointment.get(apt_id, [])[:2] if apt else []

        amount_billed = Decimal(str(ctx.rng.randint(100, 2000)))
        if status == "approved":
            if zero_resp:
                # Fully covered — patient owes nothing. The pay route rejects
                # these (422), so they must never be acted on.
                amount_covered = amount_billed
                patient_resp = Decimal("0")
            else:
                amount_covered = Decimal(str(round(float(amount_billed) * ctx.rng.uniform(0.6, 0.9), 2)))
                patient_resp = amount_billed - amount_covered
        elif status == "denied":
            amount_covered = Decimal("0")
            patient_resp = amount_billed
        else:  # processing
            amount_covered = Decimal("0")
            patient_resp = Decimal("0")

        claim_dict: dict[str, Any] = {
            "id": clm_id,
            "service_date": service_date.isoformat(),
            "provider_id": prov_id,
            "appointment_id": apt_id,
            "procedure_code": ctx.rng.choice(proc_codes),
            "diagnosis_code": ctx.rng.choice(diag_codes),
            "status": status,
            "amount_billed": str(amount_billed),
            "amount_covered": str(amount_covered),
            "patient_responsibility": str(patient_resp),
            "eob_available": eob,
            "appeal_deadline": (ctx.now + timedelta(days=appeal_days)).isoformat(),
            "denial_reason": None,
            "supporting_referral_id": supporting_referral_id,
            "supporting_lab_ids": supporting_lab_ids,
        }
        if status == "denied" and eob:
            claim_dict["denial_reason"] = ctx.rng.choice(_EOB_DENIAL_REASONS)
        if apt is not None:
            evidence_bits: list[str] = []
            if supporting_referral_id:
                evidence_bits.append(f"referral {supporting_referral_id}")
            if supporting_lab_ids:
                evidence_bits.append(f"labs {', '.join(supporting_lab_ids)}")
            if evidence_bits:
                base_notes = apt.get("notes", "").strip()
                evidence_note = "Claim support available via " + " and ".join(evidence_bits) + "."
                if evidence_note not in base_notes:
                    apt["notes"] = f"{base_notes} {evidence_note}".strip()
        return claim_dict

    # Approved claims
    for _ in range(approved_count):
        claim = _make_claim("approved", ctx.rng.randint(30, 90), eob=with_eob)
        ctx.base["claims"].append(claim)
        approved_claim_ids.append(claim["id"])
        total_patient_responsibility += Decimal(claim["patient_responsibility"])

    # Approved claims whose EOB is NOT yet available. These carry a positive
    # balance but the EOB cannot be reviewed, so an "review EOB then pay"
    # workflow must skip them. They are still legitimate approved claims, so
    # they appear in approved_claim_ids.
    for _ in range(approved_no_eob_count):
        claim = _make_claim("approved", ctx.rng.randint(30, 90), eob=False)
        ctx.base["claims"].append(claim)
        approved_claim_ids.append(claim["id"])
        total_patient_responsibility += Decimal(claim["patient_responsibility"])

    # Approved claims that are fully covered (patient_responsibility == 0).
    # The pay route rejects these (422), so they must never be paid.
    for _ in range(approved_zero_resp_count):
        claim = _make_claim("approved", ctx.rng.randint(30, 90), eob=with_eob, zero_resp=True)
        ctx.base["claims"].append(claim)
        approved_claim_ids.append(claim["id"])
        total_patient_responsibility += Decimal(claim["patient_responsibility"])

    # Denied claims
    for i in range(denied_count):
        is_near = near_appeal_deadline and appealable_claim_id is None and i == 0
        appeal_days = ctx.rng.randint(3, 7) if is_near else ctx.rng.randint(30, 60)
        claim = _make_claim("denied", appeal_days, eob=with_eob)
        ctx.base["claims"].append(claim)
        denied_claim_ids.append(claim["id"])
        if is_near:
            appealable_claim_id = claim["id"]

    # Ineligible denied claims (decoys): denied but NOT appealable.
    # Past-deadline claims use a negative appeal_days so appeal_deadline < now;
    # no-EOB claims carry eob_available=False. Both still count as denied
    # (added to denied_claim_ids) so they exercise the eligibility filter and
    # the invariant that forbids touching out-of-scope denied claims.
    ineligible_denied_ids: list[str] = []
    for _ in range(expired_denied_count):
        claim = _make_claim("denied", -ctx.rng.randint(10, 60), eob=with_eob)
        ctx.base["claims"].append(claim)
        denied_claim_ids.append(claim["id"])
        ineligible_denied_ids.append(claim["id"])
    for _ in range(no_eob_denied_count):
        claim = _make_claim("denied", ctx.rng.randint(30, 60), eob=False)
        ctx.base["claims"].append(claim)
        denied_claim_ids.append(claim["id"])
        ineligible_denied_ids.append(claim["id"])

    # Processing claims
    for _ in range(processing_count):
        claim = _make_claim("processing", ctx.rng.randint(60, 120))
        ctx.base["claims"].append(claim)
        processing_claim_ids.append(claim["id"])

    # Derived: the most-recent denied claim by service_date (cid tiebreaker).
    # Canonical_diff filters need a scalar target id to narrow "all claims
    # except the target one" without access to `initial` or lambdas inside
    # the `filter:` scope (hazard: invariant filter only sees `a` + `target`).
    def _claim_service_date(cid: str) -> str:
        for c in ctx.base["claims"]:
            if c["id"] == cid:
                return c["service_date"]
        return ""

    most_recent_denied_claim_id: str | None = None
    if denied_claim_ids:
        most_recent_denied_claim_id = max(
            denied_claim_ids,
            key=lambda cid: (_claim_service_date(cid), cid),
        )

    # Derived: the top-3 appealable denied claims by patient responsibility
    # (highest first, claim-id tiebreaker ascending). "Appealable" means
    # status=='denied' AND eob_available AND appeal_deadline >= ctx.now.
    # Canonical_diff needs a scalar list target to drive a bijection update;
    # computing it in the builder keeps the diff simple and avoids pushing
    # date/filter math into the invariant/where scope (hazard: filter sees
    # only a+target, where sees only id+changed fields).
    def _claim_by_id(cid: str) -> dict[str, Any] | None:
        for c in ctx.base["claims"]:
            if c["id"] == cid:
                return c
        return None

    ctx_now_iso = ctx.now.isoformat()
    appealable_ids = [
        cid for cid in denied_claim_ids
        if (c := _claim_by_id(cid)) is not None
        and c.get("eob_available")
        and c.get("appeal_deadline", "") >= ctx_now_iso
    ]
    appealable_ids_sorted = sorted(
        appealable_ids,
        key=lambda cid: (
            -float(_claim_by_id(cid)["patient_responsibility"]),
            cid,
        ),
    )
    top_3_appealable_claim_ids = appealable_ids_sorted[:3]

    # Derived: the most-recent APPROVED claim by service_date (cid tiebreaker
    # ascending, mirroring most_recent_denied_claim_id). Tasks that ask the
    # agent to act on "your most recent approved claim" need a scalar id +
    # the claim's patient_responsibility so the canonical_diff can gate an
    # exact, re-derived value (the agent must locate the claim, read its
    # balance, and format it) without leaking the answer into the prompt.
    most_recent_approved_claim_id: str | None = None
    most_recent_approved_patient_responsibility: str = "0"
    billing_followup_reason: str = ""
    if approved_claim_ids:
        most_recent_approved_claim_id = max(
            approved_claim_ids,
            key=lambda cid: (_claim_service_date(cid), cid),
        )
        _mra = _claim_by_id(most_recent_approved_claim_id)
        if _mra is not None:
            most_recent_approved_patient_responsibility = str(
                _mra.get("patient_responsibility", "0")
            )
            # Deterministic, re-derivable appointment-reason string that bakes
            # in the most-recent approved claim's id AND its patient balance.
            # The agent must (1) locate the most-recent approved claim, (2) read
            # its patient_responsibility, and (3) format this exact string —
            # making the claim-review step load-bearing rather than decorative.
            # Exposed as a seed output (not a literal in the instruction) so it
            # never leaks the answer into the prompt.
            billing_followup_reason = (
                f"Incorrect charge review for claim "
                f"{most_recent_approved_claim_id} "
                f"(patient balance ${most_recent_approved_patient_responsibility})"
            )

    # Derived: the top-K APPROVED claims that are payable, ranked by
    # patient_responsibility (highest first, claim-id tiebreaker ascending).
    # "Payable" means status=='approved' AND eob_available AND
    # patient_responsibility > 0. This is the approved-claim analogue of
    # top_3_appealable_claim_ids and exists so a pay-claim task can drive a
    # SATURATING bijection over the K highest-balance approved claims without
    # pushing top-K / tie-break math into the where/filter scope (hazard: the
    # invariant filter sees only `a` + `target`, the where sees only `id`).
    # `payable_top_k` (default 3) controls K; the threshold below is exposed so
    # an instruction can describe the boundary ("balance >= $X") without leaking
    # the target ids.
    payable_top_k = int(params.get("payable_top_k", 3))
    payable_ids = [
        cid for cid in approved_claim_ids
        if (c := _claim_by_id(cid)) is not None
        and c.get("eob_available")
        and float(c.get("patient_responsibility", "0")) > 0
    ]
    payable_ids_sorted = sorted(
        payable_ids,
        key=lambda cid: (
            -float(_claim_by_id(cid)["patient_responsibility"]),
            cid,
        ),
    )
    top_k_payable_claim_ids = payable_ids_sorted[:payable_top_k]
    # The smallest patient_responsibility among the selected top-K (the
    # inclusive cutoff). When fewer than K payable claims exist this is the
    # smallest of all payable; when none exist it is "0". Stored as a string to
    # match the Decimal-as-string convention used elsewhere.
    if top_k_payable_claim_ids:
        payable_cutoff = min(
            float(_claim_by_id(cid)["patient_responsibility"])
            for cid in top_k_payable_claim_ids
        )
    else:
        payable_cutoff = 0.0
    payable_cutoff_amount = str(round(payable_cutoff, 2))

    # Derived: the payable approved claims — approved AND eob_available AND a
    # strictly-positive patient_responsibility. This is the discriminator an
    # "review the EOB, then pay the patient responsibility" task drives a
    # saturating bijection over. Approved-but-no-EOB and fully-covered
    # (zero-responsibility) approved claims are deliberately excluded so the
    # agent must filter, not just "pay every approved claim". Sorted by
    # claim id for deterministic ordering across runs (reuses the same payable
    # membership computed above for top_k_payable_claim_ids).
    payable_approved_claim_ids = sorted(payable_ids)

    # Derived: the N most-RECENT appealable denied claims, ordered by
    # service_date descending (most recent first), claim-id descending as the
    # tiebreaker so the newest id wins on a service-date tie. Distinct from
    # top_3_appealable_claim_ids (which orders by patient responsibility): this
    # is the recency-based set used by the dispute-claim task. Computed in the
    # builder so the canonical_diff bijection can target a scalar list without
    # pushing date/sort math into the where/filter scope.
    recent_appealable_n = params.get("recent_appealable_n", 2)
    recent_appealable_sorted = sorted(
        appealable_ids,
        key=lambda cid: (_claim_by_id(cid)["service_date"], cid),
        reverse=True,
    )
    recent_appealable_claim_ids = recent_appealable_sorted[:recent_appealable_n]

    return {
        "approved_claim_ids": approved_claim_ids,
        "denied_claim_ids": denied_claim_ids,
        "processing_claim_ids": processing_claim_ids,
        "appealable_claim_id": appealable_claim_id,
        "most_recent_denied_claim_id": most_recent_denied_claim_id,
        "most_recent_approved_claim_id": most_recent_approved_claim_id,
        "most_recent_approved_patient_responsibility": (
            most_recent_approved_patient_responsibility
        ),
        "billing_followup_reason": billing_followup_reason,
        "top_3_appealable_claim_ids": top_3_appealable_claim_ids,
        "top_k_payable_claim_ids": top_k_payable_claim_ids,
        "payable_cutoff_amount": payable_cutoff_amount,
        "payable_approved_claim_ids": payable_approved_claim_ids,
        "recent_appealable_claim_ids": recent_appealable_claim_ids,
        "appealable_claim_ids": appealable_ids_sorted,
        "ineligible_denied_ids": ineligible_denied_ids,
        "total_patient_responsibility": str(total_patient_responsibility),
    }


# ---------------------------------------------------------------------------
# 10. immunization_record
# ---------------------------------------------------------------------------

@_register("immunization_record")
def build_immunization_record(ctx: PatientPortalSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create a mix of completed and due immunizations.

    Params: completed_count (int), due_count (int), series_incomplete (bool)
    Outputs: completed_imm_ids, due_imm_ids, incomplete_series_imm_id, due_vaccine_names
    """
    completed_count = params.get("completed_count", 3)
    due_count = params.get("due_count", 1)
    series_incomplete = params.get("series_incomplete", False)

    if "immunizations" not in ctx.base:
        ctx.base["immunizations"] = []

    providers = ctx.base.get("providers", [])
    # Immunizations are clinically administered by PCPs (not cardiologists /
    # endocrinologists / dermatologists). Restrict admin-provider candidates
    # to PCPs — this matches real healthcare AND avoids the referral-required
    # gate in the patient_portal UI, which blocks booking with specialists
    # unless an approved referral exists.
    pcp_providers = [
        p for p in providers
        if p.get("available_slots") and p.get("specialty") == "pcp"
    ]
    if pcp_providers:
        providers_with_slots = pcp_providers
    else:
        # Fallback: any non-billing/admin provider with slots, then any at all.
        providers_with_slots = [
            p for p in providers
            if p.get("available_slots") and p.get("specialty") not in ("billing", "admin")
        ]
        if not providers_with_slots:
            providers_with_slots = [
                p for p in providers if p.get("specialty") not in ("billing", "admin")
            ]
        if not providers_with_slots:
            providers_with_slots = providers[:1] if providers else [{"id": "prov_1"}]

    vaccine_pool = list(_VACCINES)
    ctx.rng.shuffle(vaccine_pool)
    vax_idx = 0

    completed_imm_ids: list[str] = []
    due_imm_ids: list[str] = []
    incomplete_series_imm_id: str | None = None
    due_vaccine_names: list[str] = []
    # Parallel list of bare vaccine forms (no parenthetical), for predicates
    # that check whether an appointment reason "contains the vaccine".
    # The agent rarely types "Tdap (Tetanus)" verbatim — they'll write
    # "Tdap" or "Tetanus booster", and the predicate must accept either.
    due_vaccine_short_names: list[str] = []
    # When series_incomplete is True, these describe the remaining doses the
    # agent must schedule: one slot per dose (e.g. doses 2 and 3 of a 3-dose
    # series ⇒ two slot labels). series_admin_provider_id is the provider
    # who administered the first dose — the agent is expected to continue
    # with the same administering provider for clinical continuity.
    remaining_dose_slots: list[str] = []
    series_admin_provider_id: str | None = None
    series_vaccine_name: str | None = None
    series_doses_total: int = 0

    # Completed immunizations. Constrain administered_at so that if the
    # vaccine has a recurring cadence (annual / interval_years), its computed
    # next_due_at is strictly in the FUTURE. Otherwise the UI would display
    # completed vaccines as "overdue" alongside the ones in due_imm_ids, and
    # the agent would see more overdue entries than the task targets —
    # making the bijection unsatisfiable in the agent's frame of reference.
    for _ in range(completed_count):
        if vax_idx >= len(vaccine_pool):
            vax_idx = 0
        vax = vaccine_pool[vax_idx]
        vax_idx += 1
        imm_id = ctx.next_id("imm")
        admin_prov = ctx.rng.choice(providers_with_slots)

        # Pick administered_at based on cadence so next_due ends up in the future.
        if vax.get("annual"):
            # Administered 30–335 days ago → next_due 30–335 days in the future.
            administered_at = ctx.now - timedelta(days=ctx.rng.randint(30, 335))
            next_due = administered_at + timedelta(days=365)
        elif vax.get("interval_years"):
            years = vax["interval_years"]
            max_days_ago = max(60, years * 365 - 30)
            administered_at = ctx.now - timedelta(days=ctx.rng.randint(30, max_days_ago))
            next_due = administered_at + timedelta(days=years * 365)
        else:
            # No recurring cadence — series complete, no next_due.
            administered_at = ctx.now - timedelta(days=ctx.rng.randint(30, 730))
            next_due = None

        imm_dict = {
            "id": imm_id,
            "vaccine_name": vax["name"],
            "administered_at": administered_at.isoformat(),
            "next_due_at": next_due.isoformat() if next_due else None,
            "series_complete": True,
            "administering_provider_id": admin_prov["id"],
        }
        ctx.base["immunizations"].append(imm_dict)
        completed_imm_ids.append(imm_id)

    # Due immunizations (next_due_at is in the past).
    # Rotate through the PCP pool so that when multiple PCPs exist, each
    # overdue vaccine is bound to a DIFFERENT administering provider. This
    # preserves the bijection's identity-test property: the agent must
    # actually look up which provider administered which vaccine, not just
    # book any PCP for any vaccine.
    for _idx_due in range(due_count):
        if vax_idx >= len(vaccine_pool):
            vax_idx = 0
        vax = vaccine_pool[vax_idx]
        vax_idx += 1
        imm_id = ctx.next_id("imm")
        # Round-robin: if N PCPs are available, vaccine i uses PCP (i mod N).
        admin_prov = providers_with_slots[_idx_due % len(providers_with_slots)]
        administered_at = ctx.now - timedelta(days=ctx.rng.randint(365, 1095))
        # next_due is in the past (overdue)
        next_due = ctx.now - timedelta(days=ctx.rng.randint(1, 60))

        imm_dict = {
            "id": imm_id,
            "vaccine_name": vax["name"],
            "administered_at": administered_at.isoformat(),
            "next_due_at": next_due.isoformat(),
            "series_complete": True,
            "administering_provider_id": admin_prov["id"],
        }
        ctx.base["immunizations"].append(imm_dict)
        due_imm_ids.append(imm_id)
        due_vaccine_names.append(vax["name"])
        due_vaccine_short_names.append(vax.get("short_name", vax["name"]))

    # Incomplete series
    if series_incomplete:
        # Find a multi-dose vaccine; prefer the one specified by series_vaccine param
        _series_vaccine_name = params.get("series_vaccine", None)
        if _series_vaccine_name:
            series_vax = next((v for v in _VACCINES if v["name"] == _series_vaccine_name and v.get("series")), None)
            if series_vax is None:
                series_vax = next((v for v in _VACCINES if v.get("series") and v.get("doses", 0) >= 2), _VACCINES[3])
        else:
            series_vax = next((v for v in _VACCINES if v.get("series") and v.get("doses", 0) >= 2), _VACCINES[3])
        imm_id = ctx.next_id("imm")
        admin_prov = ctx.rng.choice(providers_with_slots)
        administered_at = ctx.now - timedelta(days=ctx.rng.randint(30, 180))
        interval = series_vax.get("interval_months", 2) * 30
        next_due = administered_at + timedelta(days=interval)

        imm_dict = {
            "id": imm_id,
            "vaccine_name": series_vax["name"],
            "administered_at": administered_at.isoformat(),
            "next_due_at": next_due.isoformat(),
            "series_complete": False,
            "administering_provider_id": admin_prov["id"],
        }
        ctx.base["immunizations"].append(imm_dict)
        incomplete_series_imm_id = imm_id
        if series_vax["name"] not in due_vaccine_names:
            due_vaccine_names.append(series_vax["name"])
            due_vaccine_short_names.append(series_vax.get("short_name", series_vax["name"]))
        # Record series-level metadata for canonical_diff authoring. The
        # patient has received exactly one dose (this record) — so
        # remaining_doses = total_doses - 1. Emit one slot label per
        # remaining dose so a bijection over remaining_dose_slots generates
        # the right number of target slots. Each slot is distinct so the
        # matcher's identity test doesn't degenerate (hazard Class 4).
        series_admin_provider_id = admin_prov["id"]
        series_vaccine_name = series_vax["name"]
        series_doses_total = int(series_vax.get("doses", 3))
        doses_received = 1
        remaining = max(0, series_doses_total - doses_received)
        remaining_dose_slots = [
            f"{series_vax['name']} (dose {doses_received + i + 1} of {series_doses_total})"
            for i in range(remaining)
        ]

    # -- Extension: admin_providers + scheduling window ------------------
    # For each due immunization, look up the provider(s) who administered the
    # most recent completed dose of the same vaccine. Falls back to the due
    # imm's own administering_provider_id if no completed dose matches.
    all_imms = ctx.base["immunizations"]
    completed_imms = [
        imm for imm in all_imms if imm["id"] in completed_imm_ids
    ]
    admin_providers: dict[str, list[str]] = {}
    for due_id in due_imm_ids:
        due_imm = next((imm for imm in all_imms if imm["id"] == due_id), None)
        if due_imm is None:
            continue
        vaccine_name = due_imm["vaccine_name"]
        # Find completed doses with matching vaccine_name (exact match — seed
        # data uses the canonical vaccine_name strings from _VACCINES).
        matching_completed = [
            imm for imm in completed_imms
            if imm["vaccine_name"] == vaccine_name
        ]
        if matching_completed:
            # Most recent completed dose by administered_at
            most_recent = max(
                matching_completed, key=lambda i: i["administered_at"]
            )
            admin_providers[due_id] = [most_recent["administering_provider_id"]]
        else:
            # Fallback: use the due imm's own administering provider
            admin_providers[due_id] = [due_imm["administering_provider_id"]]

    # Use ctx.now (seed-derived anchor time) so windows stay deterministic
    # across runs with the same seed.
    _window_start = ctx.now
    _window_end = _window_start + timedelta(days=30)

    # Series window — needs to fit (doses_total - 1) consecutive slots with
    # at least 1 month (30d) spacing. Add a generous buffer so the agent has
    # some latitude in picking exact dates while still respecting spacing.
    if series_doses_total > 0:
        _series_window_start = ctx.now
        _series_window_end = _series_window_start + timedelta(
            days=30 * max(1, series_doses_total) + 60
        )
    else:
        _series_window_start = ctx.now
        _series_window_end = ctx.now + timedelta(days=180)

    return {
        "completed_imm_ids": completed_imm_ids,
        "due_imm_ids": due_imm_ids,
        "incomplete_series_imm_id": incomplete_series_imm_id,
        "due_vaccine_names": due_vaccine_names,
        "due_vaccine_short_names": due_vaccine_short_names,
        "admin_providers": admin_providers,
        "window_start": _window_start.isoformat(),
        "window_end": _window_end.isoformat(),
        "remaining_dose_slots": remaining_dose_slots,
        "series_admin_provider_id": series_admin_provider_id,
        "series_vaccine_name": series_vaccine_name,
        "series_doses_total": series_doses_total,
        "series_window_start": _series_window_start.isoformat(),
        "series_window_end": _series_window_end.isoformat(),
    }
