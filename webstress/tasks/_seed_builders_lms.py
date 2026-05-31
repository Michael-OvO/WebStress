"""Composable seed builder framework for the LMS environment.

Provides :class:`LMSSeedContext` and a registry of reusable builder
functions that generate deterministic academic test data for benchmark tasks.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

from webstress.backend.models.lms import (
    Announcement,
    Assignment,
    CalendarEvent,
    CategoryPolicy,
    ContentItem,
    Course,
    Discussion,
    DiscussionPost,
    Enrollment,
    Grade,
    LatePolicy,
    Module,
    PeerReview,
    RubricItem,
    Student,
    Syllabus,
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
# Hardcoded course catalog pool
# ---------------------------------------------------------------------------

_COURSE_CATALOG: list[dict[str, Any]] = [
    {"code": "CS101", "title": "Introduction to Computer Science", "dept": "Computer Science", "credits": 3},
    {"code": "CS201", "title": "Data Structures and Algorithms", "dept": "Computer Science", "credits": 3},
    {"code": "CS301", "title": "Operating Systems", "dept": "Computer Science", "credits": 3},
    {"code": "MATH201", "title": "Linear Algebra", "dept": "Mathematics", "credits": 4},
    {"code": "MATH301", "title": "Differential Equations", "dept": "Mathematics", "credits": 4},
    {"code": "ENG102", "title": "Academic Writing", "dept": "English", "credits": 3},
    {"code": "ENG201", "title": "Technical Communication", "dept": "English", "credits": 3},
    {"code": "PHYS201", "title": "Classical Mechanics", "dept": "Physics", "credits": 4},
    {"code": "CHEM101", "title": "General Chemistry", "dept": "Chemistry", "credits": 4},
    {"code": "BIO101", "title": "Introduction to Biology", "dept": "Biology", "credits": 3},
    {"code": "STAT301", "title": "Probability and Statistics", "dept": "Statistics", "credits": 3},
    {"code": "ECON101", "title": "Principles of Economics", "dept": "Economics", "credits": 3},
    {"code": "HIST101", "title": "World History", "dept": "History", "credits": 3},
    {"code": "PSYCH101", "title": "Introduction to Psychology", "dept": "Psychology", "credits": 3},
    {"code": "ART101", "title": "Art Appreciation", "dept": "Fine Arts", "credits": 3},
]

# Grading policy templates: each is a dict of category -> (weight, drop_lowest)
_GRADING_TEMPLATES: list[dict[str, tuple[str, int]]] = [
    {
        "homework": ("0.30", 1),
        "quizzes": ("0.10", 1),
        "midterm": ("0.20", 0),
        "project": ("0.15", 0),
        "final": ("0.25", 0),
    },
    {
        "homework": ("0.25", 2),
        "quizzes": ("0.15", 1),
        "midterm": ("0.25", 0),
        "final": ("0.35", 0),
    },
    {
        "homework": ("0.35", 1),
        "participation": ("0.05", 0),
        "midterm": ("0.20", 0),
        "project": ("0.20", 0),
        "final": ("0.20", 0),
    },
    {
        "homework": ("0.20", 0),
        "essays": ("0.20", 0),
        "quizzes": ("0.10", 0),
        "midterm": ("0.20", 0),
        "final": ("0.30", 0),
    },
]

# Late policy presets: (penalty_per_day, max_late_days, grace_period_hours)
_LATE_PRESETS: list[tuple[str, int, int]] = [
    ("0.05", 7, 6),   # lenient
    ("0.10", 5, 2),   # moderate
    ("0.15", 3, 0),   # strict
]

# Discussion prompts pool
_DISCUSSION_PROMPTS: list[str] = [
    "Discuss the key takeaways from this week's reading and how they relate to previous topics.",
    "What challenges did you encounter in the latest assignment? Share your approach.",
    "Analyze the trade-offs presented in the lecture material and argue for your preferred approach.",
    "How does this week's topic connect to real-world applications? Provide specific examples.",
    "Reflect on your learning process so far. What concepts have been most surprising?",
    "Compare and contrast the two main approaches discussed in class this week.",
]

# Announcement body templates
_ANNOUNCEMENT_BODIES: list[str] = [
    "Please review the updated syllabus before next week's class. Key changes are highlighted.",
    "Office hours have been moved to Thursday 2-4 PM for the remainder of the semester.",
    "The midterm exam will cover all material through Module {mod_num}. Study guide posted.",
    "Reminder: the project proposal is due by end of day Friday. No late submissions accepted.",
    "Guest speaker next Wednesday. Attendance is optional but strongly recommended.",
    "Grades for the latest assignment have been posted. Please review your feedback.",
    "Important: the final exam date has been confirmed. Check the calendar for details.",
    "Lab section is cancelled this week due to facility maintenance. Make-up session TBD.",
]

# Assignment title templates by type
_ASSIGNMENT_TITLES: dict[str, list[str]] = {
    "homework": [
        "Problem Set {n}",
        "Homework {n}: {topic}",
        "Weekly Assignment {n}",
        "Practice Problems {n}",
    ],
    "essay": [
        "Essay {n}: {topic}",
        "Analysis Paper {n}",
        "Reflection Paper {n}",
    ],
    "project": [
        "Course Project: Phase {n}",
        "Project Milestone {n}",
        "Final Project Submission",
    ],
    "quiz": [
        "Quiz {n}: {topic}",
        "Pop Quiz {n}",
        "Weekly Quiz {n}",
    ],
    "exam": [
        "Midterm Exam",
        "Final Exam",
        "Final Exam Retake",
    ],
    "peer_review": [
        "Peer Review: {topic}",
        "Peer Review Assignment {n}",
    ],
    "participation": [
        "Class Participation: Week {n}",
        "Discussion Participation {n}",
    ],
}

# Topics pool for assignment title interpolation
_TOPICS: list[str] = [
    "Fundamentals",
    "Core Concepts",
    "Applications",
    "Advanced Topics",
    "Review",
    "Data Analysis",
    "Theory and Practice",
    "Integration",
    "Optimization",
    "Case Study",
]

# Rubric templates by assignment type (criteria + relative weights summing to 100)
_RUBRIC_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "homework": [
        {"criterion": "Accuracy", "max_points": 40, "description": "Correctness of solutions and calculations"},
        {"criterion": "Completeness", "max_points": 30, "description": "All required parts attempted and addressed"},
        {"criterion": "Presentation", "max_points": 30, "description": "Clear formatting, organization, and explanation"},
    ],
    "essay": [
        {"criterion": "Thesis", "max_points": 25, "description": "Clear, arguable thesis statement"},
        {"criterion": "Evidence", "max_points": 25, "description": "Quality and relevance of supporting evidence"},
        {"criterion": "Analysis", "max_points": 25, "description": "Depth of critical analysis and interpretation"},
        {"criterion": "Writing Quality", "max_points": 25, "description": "Grammar, style, and overall writing clarity"},
    ],
    "project": [
        {"criterion": "Design", "max_points": 30, "description": "Overall architecture and design decisions"},
        {"criterion": "Implementation", "max_points": 40, "description": "Correctness and quality of implementation"},
        {"criterion": "Documentation", "max_points": 30, "description": "Clarity and completeness of documentation"},
    ],
    "exam": [
        {"criterion": "Knowledge", "max_points": 50, "description": "Demonstrated understanding of core concepts"},
        {"criterion": "Application", "max_points": 30, "description": "Ability to apply concepts to new problems"},
        {"criterion": "Critical Thinking", "max_points": 20, "description": "Reasoning and problem-solving approach"},
    ],
    "quiz": [
        {"criterion": "Accuracy", "max_points": 70, "description": "Correctness of answers"},
        {"criterion": "Completeness", "max_points": 30, "description": "All questions answered"},
    ],
    "peer_review": [
        {"criterion": "Clarity", "max_points": 34, "description": "How clearly the work communicates its ideas"},
        {"criterion": "Depth", "max_points": 33, "description": "Depth of analysis and thoroughness"},
        {"criterion": "Originality", "max_points": 33, "description": "Original thinking and contribution"},
    ],
    "participation": [
        {"criterion": "Engagement", "max_points": 50, "description": "Active participation in discussions"},
        {"criterion": "Quality", "max_points": 50, "description": "Quality of contributions"},
    ],
}

_RESUBMIT_FEEDBACK: list[str] = [
    "Your analysis needs more depth in section 2. Please expand on the methodology and add supporting evidence.",
    "The argument structure is weak. Reorganize around your main thesis and address the counterarguments.",
    "Missing citations on key claims. Please add proper references and resubmit.",
    "Good foundation but the conclusion doesn't follow from the evidence. Revise the final section.",
    "Technical accuracy issues in problems 3 and 5. Please double-check your calculations.",
]

_GRADED_FEEDBACK: list[str] = [
    "Good work. Strong analysis with clear methodology.",
    "Well-structured submission. Consider expanding the literature review in future work.",
    "Excellent problem-solving approach. Minor formatting issues noted.",
    "Solid effort. The theoretical framework is well-applied.",
    "Good submission overall. More detail on assumptions would strengthen the work.",
]


# ---------------------------------------------------------------------------
# LMSSeedContext
# ---------------------------------------------------------------------------

class LMSSeedContext:
    """Mutable accumulator threaded through every seed builder step."""

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
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}_{self.counters[prefix]}"

    def email_for_name(self, name: str, domain: str | None = None) -> str:
        local = "".join(
            ch.lower() for ch in name if ch.isalnum() or ch == " "
        ).replace(" ", ".")
        local = ".".join(part for part in local.split(".") if part) or "contact"
        domain = domain or f"{self.fake.domain_word()}.com"
        return f"{local}@{domain}"

    def resolve_actor(self, key: str, domain: str = "thornton.com",
                      is_vip: bool = False, name: str | None = None) -> ResolvedActor:
        if key in self.actors:
            return self.actors[key]
        name = name or self.fake.name()
        actor = ResolvedActor(
            name=name,
            email=self.email_for_name(name, domain),
            first_name=name.split()[0],
        )
        self.actors[key] = actor
        return actor


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BuilderFn = Callable[["LMSSeedContext", dict[str, Any]], dict[str, Any]]

LMS_BUILDER_REGISTRY: dict[str, BuilderFn] = {}


def _register(name: str) -> Callable[[BuilderFn], BuilderFn]:
    def decorator(fn: BuilderFn) -> BuilderFn:
        LMS_BUILDER_REGISTRY[name] = fn
        return fn
    return decorator


# ---------------------------------------------------------------------------
# 1. student_profile
# ---------------------------------------------------------------------------

@_register("student_profile")
def _build_student_profile(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate student demographics, GPA, advisor assignment."""
    gpa = Decimal(str(params.get("gpa", str(round(ctx.rng.uniform(2.5, 3.8), 2)))))
    gpa = gpa.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    status = params.get("enrollment_status", "active")
    name = params.get("name") or ctx.fake.name()
    email = ctx.email_for_name(name, "thornton.com")
    student_id_num = f"S-{ctx.rng.randint(20240000, 20269999)}"
    advisor_name = f"Dr. {ctx.fake.name().split()[-1]}"

    student = Student(
        id=ctx.next_id("student"),
        name=name,
        email=email,
        student_id=student_id_num,
        enrollment_status=status,
        gpa=gpa,
        advisor_id=ctx.next_id("advisor"),
        advisor_name=advisor_name,
    )
    ctx.base["student"] = student.model_dump()
    return {
        "student_id": student.id,
        "student_name": name,
        "student_email": email,
        "gpa": str(gpa),
        "advisor_name": advisor_name,
    }


# ---------------------------------------------------------------------------
# 2. course_catalog
# ---------------------------------------------------------------------------

@_register("course_catalog")
def _build_course_catalog(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate N courses with syllabi, instructors, and varied grading/late policies.

    Params
    ------
    count : int               -- number of courses (default 4)
    must_include : list[str]  -- course codes that MUST be present
    vary_late_policies : bool -- assign varied late policies across courses (default False)
    semester : str            -- semester label (default "Spring 2026")
    """
    count = params.get("count", 4)
    must_include = set(params.get("must_include", []))
    vary_late = params.get("vary_late_policies", False)
    semester = params.get("semester", "Spring 2026")
    grading_template_indices = params.get("grading_template_indices")
    grading_template_index = params.get("grading_template_index")

    # Select courses: must_include first, then random fill
    available = list(_COURSE_CATALOG)
    selected = [c for c in available if c["code"] in must_include]
    remaining = [c for c in available if c["code"] not in must_include]
    ctx.rng.shuffle(remaining)
    selected.extend(remaining[: max(0, count - len(selected))])

    if "courses" not in ctx.base:
        ctx.base["courses"] = []

    course_ids: list[str] = []
    course_codes: list[str] = []
    strictest_id: str | None = None
    most_lenient_id: str | None = None
    strictest_penalty = Decimal("0")
    most_lenient_penalty = Decimal("1")
    first_instructor_name: str | None = None
    first_course_code: str | None = None
    first_course_title: str | None = None
    first_course_id: str | None = None

    for i, cd in enumerate(selected):
        course_id = ctx.next_id("course")
        instructor_name = f"Prof. {ctx.fake.name().split()[-1]}"

        # Pick grading policy
        if isinstance(grading_template_indices, list) and i < len(grading_template_indices):
            template_idx = int(grading_template_indices[i]) % len(_GRADING_TEMPLATES)
            template = _GRADING_TEMPLATES[template_idx]
        elif grading_template_index is not None and i == 0:
            template = _GRADING_TEMPLATES[int(grading_template_index) % len(_GRADING_TEMPLATES)]
        else:
            template = _GRADING_TEMPLATES[ctx.rng.randint(0, len(_GRADING_TEMPLATES) - 1)]
        grading_policy: dict[str, CategoryPolicy] = {}
        for cat_name, (weight_str, drop_low) in template.items():
            grading_policy[cat_name] = CategoryPolicy(
                weight=Decimal(weight_str),
                drop_lowest=drop_low,
            )

        # Pick late policy
        if vary_late:
            preset = _LATE_PRESETS[i % len(_LATE_PRESETS)]
        else:
            preset = _LATE_PRESETS[1]  # moderate default
        late_policy = LatePolicy(
            penalty_per_day=Decimal(preset[0]),
            max_late_days=preset[1],
            grace_period_hours=preset[2],
        )

        # Track strictest/most lenient
        if late_policy.penalty_per_day > strictest_penalty:
            strictest_penalty = late_policy.penalty_per_day
            strictest_id = course_id
        if late_policy.penalty_per_day < most_lenient_penalty:
            most_lenient_penalty = late_policy.penalty_per_day
            most_lenient_id = course_id

        semester_start = ctx.now - timedelta(days=45)
        drop_deadline = ctx.now + timedelta(days=120)  # well past any wall-clock drift
        final_exam_date = ctx.now + timedelta(days=60)

        course = Course(
            id=course_id,
            course_code=cd["code"],
            title=cd["title"],
            instructor_id=ctx.next_id("instructor"),
            instructor_name=instructor_name,
            semester=semester,
            credits=cd["credits"],
            syllabus=Syllabus(
                grading_policy=grading_policy,
                late_policy=late_policy,
            ),
            drop_deadline=drop_deadline,
            final_exam_date=final_exam_date,
        )
        ctx.base["courses"].append(course.model_dump())

        course_ids.append(course_id)
        course_codes.append(cd["code"])

        if i == 0:
            first_instructor_name = instructor_name
            first_course_code = cd["code"]
            first_course_title = cd["title"]
            first_course_id = course_id

    return {
        "course_ids": course_ids,
        "course_codes": course_codes,
        "strictest_late_policy_course_id": strictest_id or (course_ids[0] if course_ids else ""),
        "most_lenient_late_policy_course_id": most_lenient_id or (course_ids[0] if course_ids else ""),
        "instructor_name": first_instructor_name or "",
        "course_code": first_course_code or "",
        "course_title": first_course_title or "",
        "target_course_id": first_course_id or "",
        "course_id_1": course_ids[0] if len(course_ids) > 0 else "",
        "course_id_2": course_ids[1] if len(course_ids) > 1 else "",
        "course_code_1": course_codes[0] if len(course_codes) > 0 else "",
        "course_code_2": course_codes[1] if len(course_codes) > 1 else "",
    }


# ---------------------------------------------------------------------------
# 3. enrollment_set
# ---------------------------------------------------------------------------

@_register("enrollment_set")
def _build_enrollment_set(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Enroll student in all courses from course_catalog.

    Params
    ------
    roles : dict[str, str]      -- course_id -> role override ("ta")
    include_waitlisted : bool   -- add a waitlisted enrollment (default False)
    """
    roles: dict[str, str] = params.get("roles", {})
    include_waitlisted = params.get("include_waitlisted", False)

    student_id = ctx.base.get("student", {}).get("id", "student_1")
    courses = ctx.base.get("courses", [])

    if "enrollments" not in ctx.base:
        ctx.base["enrollments"] = []

    enrollment_ids: list[str] = []
    ta_course_id: str | None = None
    target_course_id = ctx.outputs.get("target_course_id", "")
    target_enrollment_id: str | None = None

    for i, course_data in enumerate(courses):
        course_id = course_data["id"]
        role = roles.get(course_id, "student")
        status = "enrolled"
        if include_waitlisted and i == len(courses) - 1:
            status = "waitlisted"

        enrollment = Enrollment(
            id=ctx.next_id("enrollment"),
            student_id=student_id,
            course_id=course_id,
            role=role,
            status=status,
        )
        ctx.base["enrollments"].append(enrollment.model_dump())
        enrollment_ids.append(enrollment.id)

        if role == "ta":
            ta_course_id = course_id
        if course_id == target_course_id and target_enrollment_id is None:
            target_enrollment_id = enrollment.id

    return {
        "enrollment_ids": enrollment_ids,
        "ta_course_id": ta_course_id or "",
        "target_enrollment_id": target_enrollment_id or "",
    }


# ---------------------------------------------------------------------------
# 4. assignment_battery
# ---------------------------------------------------------------------------

def _pick_assignment_title(
    rng: random.Random, atype: str, n: int, topic: str, weight_category: str,
) -> str:
    """Deterministically pick a title template and interpolate."""
    if atype == "exam":
        if weight_category == "midterm":
            return "Midterm Exam"
        if weight_category == "final":
            return "Final Exam"
    if atype == "project":
        return f"Project {n}: {topic}"
    templates = _ASSIGNMENT_TITLES.get(atype, _ASSIGNMENT_TITLES["homework"])
    tmpl = rng.choice(templates)
    return tmpl.replace("{n}", str(n)).replace("{topic}", topic)


def _cap_to_recoverable(offset_days: int, max_late_days: int) -> int:
    """Return a negative offset (days past due) capped within the late-submission window."""
    return -max(1, min(abs(offset_days), max(1, max_late_days - 1)))


@_register("assignment_battery")
def _build_assignment_battery(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate assignments across courses with varying statuses.

    Params
    ------
    per_course_count : int     -- assignments per course (default 6)
    graded_fraction : float    -- fraction that are graded (default 0.5)
    late_count : int           -- how many assignments are late (default 1)
    late_within_grace_count : int -- how many late assignments are within grace (default 0)
    missing_count : int        -- how many are not_submitted past due (default 1)
    unrecoverable_missing_count : int -- how many missing assignments are past max late days (default 0)
    resubmit_count : int       -- how many request resubmission (default 0)
    target_assignment_status : str -- filter target selection by status
    exclude_course_id : str    -- optionally exclude a course when choosing the target assignment
    """
    per_course = params.get("per_course_count", 6)
    graded_frac = params.get("graded_fraction", 0.5)
    late_count = params.get("late_count", 1)
    late_within_grace_count = params.get("late_within_grace_count", 0)
    missing_count = params.get("missing_count", 1)
    unrecoverable_missing_count = params.get("unrecoverable_missing_count", 0)
    resubmit_count = params.get("resubmit_count", 0)
    target_status = params.get("target_assignment_status", None)
    exclude_course_id = params.get("exclude_course_id", "")
    # When True, force EXACTLY ONE past-due not_submitted assignment to remain
    # inside its course's late-submission window (all other past-due missing
    # work is pushed beyond max_late_days so it is unrecoverable). Lets a task
    # state an unambiguous "the only recoverable past-due assignment" rule that
    # the agent must re-derive from each course's late policy. Default False so
    # every existing task that shares this builder is unaffected (additive).
    sole_recoverable_missing = bool(params.get("sole_recoverable_missing", False))
    # When True, restrict TARGET assignment selection to the recoverable-missing
    # set (size 1 once sole_recoverable_missing has run), so the standard
    # target_* outputs deterministically resolve to that single assignment.
    target_recoverable_missing = bool(params.get("target_recoverable_missing", False))

    courses = ctx.base.get("courses", [])
    courses_by_id = {course["id"]: course for course in courses}
    if "assignments" not in ctx.base:
        ctx.base["assignments"] = []

    all_assignment_ids: list[str] = []
    missing_ids: list[str] = []
    late_ids: list[str] = []
    late_within_grace_ids: list[str] = []
    resubmit_ids: list[str] = []
    quiz_ids: list[str] = []
    project_ids: list[str] = []
    essay_ids: list[str] = []
    lowest_homework_id: str | None = None
    lowest_homework_score: Decimal | None = None
    # Also track lowest homework ID within the target course specifically.
    # This ensures lms_identify_dropped_homework uses the correct course.
    _target_course_id_for_hw = ctx.outputs.get("target_course_id", "")
    _lowest_hw_in_target: str | None = None
    _lowest_hw_score_in_target: Decimal | None = None
    exam_assignment_id: str | None = None
    file_name: str = "submission.pdf"

    # Category -> assignment type mapping
    _cat_to_type: dict[str, str] = {
        "homework": "homework",
        "quizzes": "quiz",
        "midterm": "exam",
        "final": "exam",
        "project": "project",
        "participation": "participation",
        "essays": "essay",
    }

    late_budget = late_count
    missing_budget = missing_count
    resubmit_budget = resubmit_count

    for course_data in courses:
        course_id = course_data["id"]
        grading_policy = course_data["syllabus"]["grading_policy"]
        categories = list(grading_policy.keys())

        for n in range(1, per_course + 1):
            assignment_id = ctx.next_id("assignment")
            cat = categories[(n - 1) % len(categories)]
            atype = _cat_to_type.get(cat, "homework")
            topic = ctx.rng.choice(_TOPICS)
            title = _pick_assignment_title(ctx.rng, atype, n, topic, cat)

            points_possible = Decimal(str(ctx.rng.choice([10, 20, 25, 50, 100])))
            due_offset_days = ctx.rng.randint(-30, 30)
            due_at = ctx.now + timedelta(days=due_offset_days)

            # Determine status
            graded_count = int(per_course * graded_frac)
            is_past_due = due_offset_days < 0

            # Guarantee missing/late slots are filled: force past-due if budget
            # still has capacity and we are at the reserved position but the
            # random offset happened to land in the future. We negate the offset
            # to avoid consuming extra RNG calls (which would shift all subsequent
            # random draws and corrupt other seeded data).
            # missing_count==1: reserve slot at per_course
            # missing_count>=2: also reserve slot at per_course-3 for second missing
            # For the PRIMARY missing slot (n==per_course), cap days past-due to
            # the course's max_late_days so at least one missing assignment is
            # always recoverable (within the late-submission window).
            _course_max_late = course_data.get("syllabus", {}).get("late_policy", {}).get("max_late_days", 7)
            _second_missing_slot = per_course - 3 if missing_count >= 2 else -1
            if missing_budget > 0 and n == per_course and not is_past_due:
                due_offset_days = _cap_to_recoverable(due_offset_days, _course_max_late)
                due_at = ctx.now + timedelta(days=due_offset_days)
                is_past_due = True
            elif missing_budget > 0 and n == per_course and is_past_due and abs(due_offset_days) > _course_max_late:
                due_offset_days = _cap_to_recoverable(due_offset_days, _course_max_late)
                due_at = ctx.now + timedelta(days=due_offset_days)
            elif missing_budget > 0 and n == _second_missing_slot and not is_past_due:
                due_offset_days = -abs(due_offset_days) if due_offset_days != 0 else -7
                due_at = ctx.now + timedelta(days=due_offset_days)
                is_past_due = True
            elif late_budget > 0 and n == per_course - 1 and not is_past_due:
                due_offset_days = _cap_to_recoverable(due_offset_days, _course_max_late)
                due_at = ctx.now + timedelta(days=due_offset_days)
                is_past_due = True
            elif late_budget > 0 and n == per_course - 1 and is_past_due and abs(due_offset_days) > _course_max_late:
                due_offset_days = _cap_to_recoverable(due_offset_days, _course_max_late)
                due_at = ctx.now + timedelta(days=due_offset_days)

            if missing_budget > 0 and is_past_due and n == per_course:
                status = "not_submitted"
                score = None
                submitted_at = None
                missing_budget -= 1
                missing_ids.append(assignment_id)
            elif missing_budget > 0 and is_past_due and n == _second_missing_slot:
                status = "not_submitted"
                score = None
                submitted_at = None
                missing_budget -= 1
                missing_ids.append(assignment_id)
            elif late_budget > 0 and is_past_due and n == per_course - 1:
                status = "late"
                score = Decimal(str(round(ctx.rng.uniform(
                    float(points_possible) * 0.4,
                    float(points_possible) * 0.85,
                ), 1)))
                submitted_at = due_at + timedelta(days=ctx.rng.randint(1, 3))
                late_budget -= 1
                late_ids.append(assignment_id)
            elif resubmit_budget > 0 and n == per_course - 2 and is_past_due:
                status = "resubmit_requested"
                score = Decimal(str(round(ctx.rng.uniform(
                    float(points_possible) * 0.3,
                    float(points_possible) * 0.6,
                ), 1)))
                submitted_at = due_at - timedelta(hours=ctx.rng.randint(1, 12))
                resubmit_budget -= 1
                resubmit_ids.append(assignment_id)
            elif n <= graded_count and is_past_due:
                status = "graded"
                score = Decimal(str(round(ctx.rng.uniform(
                    float(points_possible) * 0.5,
                    float(points_possible) * 1.0,
                ), 1)))
                submitted_at = due_at - timedelta(hours=ctx.rng.randint(1, 48))
            elif is_past_due:
                status = "submitted"
                score = None
                submitted_at = due_at - timedelta(hours=ctx.rng.randint(1, 24))
            else:
                status = "not_submitted"
                score = None
                submitted_at = None

            attempt_count = 1 if status in ("submitted", "graded", "late", "resubmit_requested") else 0
            # For resubmit_requested, ensure at least one more attempt is available
            base_max = 2 if atype in ("homework", "quiz") else 1
            max_attempts = max(base_max, attempt_count + 1) if status == "resubmit_requested" else base_max

            # Build rubric by scaling template to points_possible
            rubric_template = _RUBRIC_TEMPLATES.get(atype, _RUBRIC_TEMPLATES["homework"])
            template_total = sum(item["max_points"] for item in rubric_template)
            scale = float(points_possible) / template_total if template_total > 0 else 1.0
            rubric_items = [
                RubricItem(
                    criterion=item["criterion"],
                    max_points=Decimal(str(round(item["max_points"] * scale, 1))),
                    description=item["description"],
                )
                for item in rubric_template
            ]

            # Choose feedback based on status
            if status == "graded":
                assignment_feedback: str | None = ctx.rng.choice(_GRADED_FEEDBACK)
            elif status == "resubmit_requested":
                assignment_feedback = ctx.rng.choice(_RESUBMIT_FEEDBACK)
            else:
                assignment_feedback = None

            assignment = Assignment(
                id=assignment_id,
                course_id=course_id,
                title=title,
                type=atype,
                due_at=due_at,
                points_possible=points_possible,
                submission_status=status,
                score=score,
                feedback=assignment_feedback,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                rubric=rubric_items,
                weight_category=cat,
                submitted_at=submitted_at,
                file_name=file_name if status != "not_submitted" else None,
            )
            ctx.base["assignments"].append(assignment.model_dump())
            all_assignment_ids.append(assignment_id)

            if atype == "quiz":
                quiz_ids.append(assignment_id)
            elif atype == "project":
                project_ids.append(assignment_id)
            elif atype == "essay":
                essay_ids.append(assignment_id)

            # Track lowest homework score for drop-lowest (global and target-course-specific)
            if cat == "homework" and score is not None:
                pct = score / points_possible
                if lowest_homework_score is None or pct < lowest_homework_score:
                    lowest_homework_score = pct
                    lowest_homework_id = assignment_id
                # Also track within target course
                if _target_course_id_for_hw and course_id == _target_course_id_for_hw:
                    if _lowest_hw_score_in_target is None or pct < _lowest_hw_score_in_target:
                        _lowest_hw_score_in_target = pct
                        _lowest_hw_in_target = assignment_id

            # Track an exam assignment
            if atype == "exam" and exam_assignment_id is None:
                exam_assignment_id = assignment_id

    # ── Post-loop: ensure drop-lowest validity ──
    # For each course that has drop_lowest > 0 on homework, ensure at least 2 graded
    # homework assignments exist, so dropped_grades_for_category() returns ≥ 1 entry.
    for course_data in courses:
        cid = course_data["id"]
        gp = course_data["syllabus"]["grading_policy"]
        hw_policy = gp.get("homework")
        if hw_policy is None:
            continue
        drop_low = hw_policy["drop_lowest"] if isinstance(hw_policy, dict) else hw_policy.drop_lowest
        if drop_low == 0:
            continue
        graded_hw = [
            a for a in ctx.base["assignments"]
            if a["course_id"] == cid and a["weight_category"] == "homework" and a["score"] is not None
        ]
        if len(graded_hw) < 2:
            # Promote the first non-graded homework to graded.
            # Skip assignments already committed as missing — promoting them
            # would corrupt the missing/recoverable tracking built above.
            for a in ctx.base["assignments"]:
                if (a["course_id"] == cid and a["weight_category"] == "homework"
                        and a["score"] is None and a["id"] not in [g["id"] for g in graded_hw]
                        and a["id"] not in missing_ids):
                    pts = Decimal(str(a["points_possible"]))
                    # Assign a moderate score (higher than lowest so it won't be the new lowest)
                    if graded_hw:
                        low_pct = Decimal(str(graded_hw[0]["score"])) / Decimal(str(graded_hw[0]["points_possible"]))
                        new_pct = min(low_pct + Decimal("0.10"), Decimal("1.0"))
                    else:
                        new_pct = Decimal("0.75")
                    new_score = (pts * new_pct).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                    a["score"] = str(new_score)
                    a["submission_status"] = "graded"
                    a["feedback"] = ctx.rng.choice(_GRADED_FEEDBACK)
                    a["attempt_count"] = 1
                    a["submitted_at"] = ctx.now.isoformat()
                    a["file_name"] = "submission.pdf"
                    # Also update lowest_homework tracking (global and target-course-specific)
                    cur_pct = Decimal(str(a["score"])) / pts
                    if lowest_homework_score is None:
                        lowest_homework_score = cur_pct
                        lowest_homework_id = a["id"]
                    if _target_course_id_for_hw and a["course_id"] == _target_course_id_for_hw:
                        if _lowest_hw_score_in_target is None:
                            _lowest_hw_score_in_target = cur_pct
                            _lowest_hw_in_target = a["id"]
                    break

    # Use target-course-specific lowest homework ID when available (course-scoped tasks).
    # Fall back to global lowest only when no target course was resolved.
    if _target_course_id_for_hw and _lowest_hw_in_target:
        lowest_homework_id = _lowest_hw_in_target

    all_assignments = ctx.base.get("assignments", [])

    def _ensure_scored_submission(assignment: dict[str, Any], score_fraction: Decimal) -> None:
        points = Decimal(str(assignment["points_possible"]))
        assignment["score"] = str((points * score_fraction).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        assignment["submitted_at"] = assignment.get("submitted_at") or (ctx.now - timedelta(hours=4)).isoformat()
        assignment["file_name"] = assignment.get("file_name") or "submission.pdf"
        assignment["attempt_count"] = max(1, assignment.get("attempt_count", 0))
        assignment["max_attempts"] = max(assignment.get("max_attempts", 1), assignment["attempt_count"] + 1)

    # Guarantee requested resubmission targets exist so feedback-based tasks are non-vacuous.
    # Also ensure at least one resubmit_requested assignment is in the catalog-level
    # target_course_id so course-scoped tasks (lms_resubmit_after_feedback's instruction
    # says "Find the assignment in {target.course_code}…") can find one. Without scoping,
    # the resubmit can land in any course and the agent has nothing to act on in the
    # course they were told to look in.
    _resubmit_target_cid = ctx.outputs.get("target_course_id", "")
    needed_resubmits = max(0, resubmit_count - len(resubmit_ids))
    if needed_resubmits > 0:
        # Prefer target-course candidates first
        target_course_candidates = [
            a for a in all_assignments
            if a["id"] not in resubmit_ids
            and a["course_id"] == _resubmit_target_cid
            and a["submission_status"] in ("graded", "late", "submitted")
        ] if _resubmit_target_cid else []
        other_candidates = [
            a for a in all_assignments
            if a["id"] not in resubmit_ids
            and a["course_id"] != _resubmit_target_cid
            and a["submission_status"] in ("graded", "late", "submitted")
        ]
        resubmit_candidates = target_course_candidates + other_candidates
        for assignment in resubmit_candidates[:needed_resubmits]:
            _ensure_scored_submission(assignment, Decimal("0.62"))
            assignment["submission_status"] = "resubmit_requested"
            assignment["feedback"] = ctx.rng.choice(_RESUBMIT_FEEDBACK)
            if assignment["id"] not in resubmit_ids:
                resubmit_ids.append(assignment["id"])
    # Even if needed_resubmits was 0, ensure a target-course resubmit_requested exists
    # when target_course_id is set. If existing resubmit_ids are all outside target course,
    # convert one target-course assignment so user finds it where instruction said to look.
    if (
        _resubmit_target_cid
        and resubmit_count > 0
        and not any(
            (a["id"] in resubmit_ids and a["course_id"] == _resubmit_target_cid)
            for a in all_assignments
        )
    ):
        # Pick the first eligible non-resubmit assignment in target course and convert it.
        convert = next(
            (
                a for a in all_assignments
                if a["course_id"] == _resubmit_target_cid
                and a["id"] not in resubmit_ids
                and a["submission_status"] in ("graded", "late", "submitted")
            ),
            None,
        )
        if convert is not None:
            _ensure_scored_submission(convert, Decimal("0.62"))
            convert["submission_status"] = "resubmit_requested"
            convert["feedback"] = ctx.rng.choice(_RESUBMIT_FEEDBACK)
            resubmit_ids.append(convert["id"])

    # Ensure resubmit_ids[0] (which task YAMLs map to target.resubmit_assignment_id) is
    # inside the target course. Some task seeds end up with multiple resubmit_requested
    # assignments across courses; the eval picks index 0, so it must point at the one
    # the user is told to find in the target course.
    if _resubmit_target_cid and len(resubmit_ids) > 1:
        target_idx = next(
            (i for i, rid in enumerate(resubmit_ids)
             if any(a["id"] == rid and a["course_id"] == _resubmit_target_cid for a in all_assignments)),
            -1,
        )
        if target_idx > 0:
            target_id = resubmit_ids.pop(target_idx)
            resubmit_ids.insert(0, target_id)

    # Guarantee late-within-grace examples when requested for grading-dispute tasks.
    needed_grace_lates = late_within_grace_count
    if needed_grace_lates > 0:
        dispute_target_cid = ctx.outputs.get("target_course_id", "")
        grace_candidates = [
            a for a in all_assignments
            if a["submission_status"] in ("graded", "late", "resubmit_requested")
            and a.get("id") not in late_within_grace_ids
            and (a["course_id"] == dispute_target_cid or not dispute_target_cid)
        ]
        if len(grace_candidates) < needed_grace_lates:
            grace_candidates = [
                a for a in all_assignments
                if a["submission_status"] in ("graded", "late", "resubmit_requested")
                and a.get("id") not in late_within_grace_ids
            ]
        for assignment in grace_candidates[:needed_grace_lates]:
            course = courses_by_id.get(assignment["course_id"])
            if not course:
                continue
            grace_hours = max(1, int(course["syllabus"]["late_policy"]["grace_period_hours"]))
            due_dt = ctx.now - timedelta(hours=max(2, min(grace_hours, 6)))
            submitted_dt = due_dt + timedelta(hours=max(1, min(grace_hours - 1, 3)))
            if submitted_dt <= due_dt:
                submitted_dt = due_dt + timedelta(minutes=30)
            if submitted_dt > ctx.now:
                submitted_dt = ctx.now - timedelta(minutes=15)
            _ensure_scored_submission(assignment, Decimal("0.78"))
            assignment["due_at"] = due_dt.isoformat()
            assignment["submitted_at"] = submitted_dt.isoformat()
            assignment["submission_status"] = "late"
            if assignment["id"] not in late_ids:
                late_ids.append(assignment["id"])
            late_within_grace_ids.append(assignment["id"])

    # Guarantee unrecoverable missing work when requested for late-policy tasks.
    needed_unrecoverable = unrecoverable_missing_count
    if needed_unrecoverable > 0:
        missing_candidates = [
            a for a in all_assignments
            if a["submission_status"] == "not_submitted"
        ]
        for assignment in missing_candidates[:needed_unrecoverable]:
            course = courses_by_id.get(assignment["course_id"])
            if not course:
                continue
            max_late_days = int(course["syllabus"]["late_policy"]["max_late_days"])
            assignment["due_at"] = (ctx.now - timedelta(days=max_late_days + 2)).isoformat()
            assignment["submission_status"] = "not_submitted"
            assignment["score"] = None
            assignment["feedback"] = None
            assignment["submitted_at"] = None
            assignment["file_name"] = None
            assignment["attempt_count"] = 0
            if assignment["id"] not in missing_ids:
                missing_ids.append(assignment["id"])

    def _is_recoverable_missing(assignment: dict[str, Any]) -> bool:
        if assignment["submission_status"] != "not_submitted":
            return False
        course = courses_by_id.get(assignment["course_id"])
        if not course:
            return False
        due_at_raw = assignment["due_at"]
        due_dt = datetime.fromisoformat(due_at_raw) if isinstance(due_at_raw, str) else due_at_raw
        if due_dt >= ctx.now:
            return False
        days_late = (ctx.now - due_dt).days
        return days_late <= int(course["syllabus"]["late_policy"]["max_late_days"])

    if missing_count > 0 and not any(_is_recoverable_missing(a) for a in all_assignments):
        for assignment in reversed(all_assignments):
            if assignment["submission_status"] != "not_submitted":
                continue
            course = courses_by_id.get(assignment["course_id"])
            if not course:
                continue
            max_late_days = int(course["syllabus"]["late_policy"]["max_late_days"])
            recoverable_days_late = max(1, min(max_late_days, 1))
            assignment["due_at"] = (ctx.now - timedelta(days=recoverable_days_late)).isoformat()
            assignment["score"] = None
            assignment["feedback"] = None
            assignment["submitted_at"] = None
            assignment["file_name"] = None
            assignment["attempt_count"] = 0
            if assignment["id"] not in missing_ids:
                missing_ids.append(assignment["id"])
            break

    # ── Force a UNIQUE recoverable past-due missing assignment ──
    # When sole_recoverable_missing is set, keep EXACTLY ONE past-due
    # not_submitted assignment inside its course's late window and make every
    # other not_submitted assignment unambiguous w.r.t. that window. The seed
    # anchor (ctx.now) floats within ~1 day of wall-clock and the evaluator
    # measures "days late" against wall-clock, so the guarantee is made robust
    # to ±1 day of drift:
    #   • the sole assignment is pinned to exactly 1 day past due (slack =
    #     max_late_days − 1 ≥ 2 days for the lenient/moderate presets), so a
    #     +1 day drift cannot push it out of its window;
    #   • every other not_submitted assignment is forced to be EITHER clearly
    #     unrecoverable (≥ max_late_days + 3 days past due) OR clearly future
    #     (≥ 5 days out), so a ±1 day drift cannot make a second assignment
    #     simultaneously past-due AND inside its window.
    # The retained "sole" assignment is the recoverable past-due one with the
    # least slack remaining, tie-broken by highest points, then earliest due,
    # then id — deterministic for a given seed.
    sole_recoverable_id: str = ""
    if sole_recoverable_missing:
        def _due_dt(a: dict[str, Any]) -> datetime:
            raw = a["due_at"]
            return datetime.fromisoformat(raw) if isinstance(raw, str) else raw

        rec_missing = [a for a in all_assignments if _is_recoverable_missing(a)]
        if rec_missing:
            def _slack(a: dict[str, Any]) -> int:
                course = courses_by_id.get(a["course_id"], {})
                ml = int(course["syllabus"]["late_policy"]["max_late_days"])
                return ml - (ctx.now - _due_dt(a)).days

            ranked = sorted(
                rec_missing,
                key=lambda a: (
                    _slack(a),
                    -Decimal(str(a["points_possible"])),
                    _due_dt(a),
                    a["id"],
                ),
            )
            sole = ranked[0]
            sole_recoverable_id = sole["id"]
            # Pin the sole assignment to 2 days past due. With the late-policy
            # presets (max_late_days ∈ {7, 5, 3}) this leaves slack ≥ 1 day, and
            # the 2-day cushion keeps it past-due-and-recoverable even when the
            # floating seed anchor sits up to ~24h ahead of wall-clock at eval.
            sole["due_at"] = (ctx.now - timedelta(days=2)).isoformat()
            sole["submission_status"] = "not_submitted"
            sole["score"] = None
            sole["feedback"] = None
            sole["submitted_at"] = None
            sole["file_name"] = None
            sole["attempt_count"] = 0

        # Make every OTHER not_submitted assignment unambiguous.
        for a in all_assignments:
            if a["submission_status"] != "not_submitted":
                continue
            if a["id"] == sole_recoverable_id:
                continue
            course = courses_by_id.get(a["course_id"])
            if not course:
                continue
            ml = int(course["syllabus"]["late_policy"]["max_late_days"])
            due = _due_dt(a)
            # Near the boundary (within a generous drift margin of "now") or
            # already past due → force clearly unrecoverable.
            if due < ctx.now + timedelta(days=2):
                a["due_at"] = (ctx.now - timedelta(days=ml + 3)).isoformat()
            else:
                # Comfortably future → keep future but at least 5 days out so
                # drift cannot make it past-due-and-recoverable.
                if due < ctx.now + timedelta(days=5):
                    a["due_at"] = (ctx.now + timedelta(days=5)).isoformat()
            a["submission_status"] = "not_submitted"
            a["score"] = None
            a["feedback"] = None
            a["submitted_at"] = None
            a["file_name"] = None
            a["attempt_count"] = 0
            if a["due_at"] and _due_dt(a) < ctx.now and a["id"] not in missing_ids:
                missing_ids.append(a["id"])

    # Select target and decoy assignments
    target_assignment_id: str | None = None
    target_assignment_title: str = ""
    target_course_id: str = ""
    target_course_code: str = ""
    decoy_assignment_id: str | None = None

    candidates = list(all_assignments)
    if exclude_course_id:
        candidates = [a for a in candidates if a["course_id"] != exclude_course_id]
    if target_status:
        candidates = [a for a in candidates if a["submission_status"] == target_status]
    if target_recoverable_missing:
        # Pin the target to the (now unique) recoverable past-due missing
        # assignment so the standard target_* outputs resolve to it.
        rec_only = [a for a in candidates if _is_recoverable_missing(a)]
        if rec_only:
            if sole_recoverable_id:
                rec_only = [a for a in rec_only if a["id"] == sole_recoverable_id] or rec_only
            candidates = rec_only
    if not candidates:
        if exclude_course_id:
            raise ValueError(
                f"assignment_battery could not find a target assignment outside excluded course {exclude_course_id!r}"
            )
        candidates = all_assignments

    if candidates:
        target = ctx.rng.choice(candidates)
        target_assignment_id = target["id"]
        target_assignment_title = target["title"]
        target_course_id = target["course_id"]
        # Find course code
        for c in courses:
            if c["id"] == target_course_id:
                target_course_code = c["course_code"]
                break

        # Find a decoy in a different course — prefer statuses that agent hasn't
        # interacted with so the negative check (decoy was not submitted) fires correctly.
        decoy_candidates = [
            a for a in all_assignments
            if a["course_id"] != target_course_id
            and a["id"] != target_assignment_id
            and a["submission_status"] in ("not_submitted", "graded")
        ]
        if not decoy_candidates:
            # Fallback: any assignment in a different course
            decoy_candidates = [
                a for a in all_assignments
                if a["course_id"] != target_course_id and a["id"] != target_assignment_id
            ]
        if decoy_candidates:
            decoy_assignment_id = ctx.rng.choice(decoy_candidates)["id"]

    # ── score_below_70: whether target assignment score < 70% of points ──
    score_below_70 = "false"
    if target_assignment_id:
        tgt = next((a for a in all_assignments if a["id"] == target_assignment_id), None)
        if tgt and tgt.get("score") is not None and tgt.get("points_possible"):
            pct = Decimal(str(tgt["score"])) / Decimal(str(tgt["points_possible"]))
            score_below_70 = "true" if pct < Decimal("0.70") else "false"

    # ── feedback_assignment_id: first assignment with non-null feedback ──
    # Scope to target_course_id when set so course-specific tasks pick the
    # feedback-bearing assignment in the agent's course view, not a globally
    # earlier feedback in another course. After choosing, null out feedback on
    # every other graded assignment so the agent has a single, unambiguous
    # "feedback-bearing" target to resubmit.
    feedback_assignment_id = ""
    feedback_search = (
        [a for a in all_assignments if a["course_id"] == target_course_id]
        if target_course_id else all_assignments
    )
    for a in feedback_search:
        if a.get("feedback"):
            feedback_assignment_id = a["id"]
            break
    if not feedback_assignment_id:
        # Fallback: any assignment with feedback, regardless of course
        for a in all_assignments:
            if a.get("feedback"):
                feedback_assignment_id = a["id"]
                break
    if feedback_assignment_id:
        for a in all_assignments:
            if (
                a["id"] != feedback_assignment_id
                and a.get("feedback")
                and a.get("submission_status") == "graded"
            ):
                a["feedback"] = None

    # ── course_plan_assignment_id: first unsubmitted assignment ──
    # Scope to target_course_id so the chosen plan assignment is visible to
    # the agent on the target course's assignments tab.
    course_plan_assignment_id = ""
    plan_search = (
        [a for a in all_assignments if a["course_id"] == target_course_id]
        if target_course_id else all_assignments
    )
    for a in plan_search:
        if a["submission_status"] == "not_submitted":
            course_plan_assignment_id = a["id"]
            break
    if not course_plan_assignment_id:
        for a in all_assignments:
            if a["submission_status"] == "not_submitted":
                course_plan_assignment_id = a["id"]
                break

    # ── unsubmitted_hw_id: first unsubmitted assignment ──
    # 4-tier preference so the target stays inside the course the user is told
    # to focus on (target_course_id), keeping the catch-up flow scoped:
    #   1) homework in target course   (best — matches "homework catch-up" intent)
    #   2) any not_submitted in target course
    #   3) homework in any course
    #   4) any not_submitted anywhere   (last-resort fallback)
    unsubmitted_hw_id = ""
    target_scoped = (
        [a for a in all_assignments if a["course_id"] == target_course_id]
        if target_course_id else []
    )
    for a in target_scoped:
        if a["submission_status"] == "not_submitted" and a["type"] == "homework":
            unsubmitted_hw_id = a["id"]
            break
    if not unsubmitted_hw_id:
        for a in target_scoped:
            if a["submission_status"] == "not_submitted":
                unsubmitted_hw_id = a["id"]
                break
    if not unsubmitted_hw_id:
        for a in all_assignments:
            if a["submission_status"] == "not_submitted" and a["type"] == "homework":
                unsubmitted_hw_id = a["id"]
                break
    if not unsubmitted_hw_id:
        for a in all_assignments:
            if a["submission_status"] == "not_submitted":
                unsubmitted_hw_id = a["id"]
                break

    # ── disputed_assignment_id_1, disputed_assignment_id_2 ──
    # Use catalog-level target_course_id when available (more reliable than the
    # locally-random target_course_id derived from ctx.rng.choice(candidates)).
    dispute_target_cid = ctx.outputs.get("target_course_id", target_course_id) or target_course_id
    graded_in_target = [
        a for a in all_assignments
        if a.get("score") is not None
        and a["submission_status"] == "graded"
        and a["course_id"] == dispute_target_cid
        and a.get("attempt_count", 0) < a.get("max_attempts", 1)
    ]
    if len(graded_in_target) < 2:
        # Fallback: any resubmittable graded assignment across all courses
        graded_in_target = [
            a for a in all_assignments
            if a.get("score") is not None
            and a["submission_status"] == "graded"
            and a.get("attempt_count", 0) < a.get("max_attempts", 1)
        ]
    if len(graded_in_target) < 2:
        # Final fallback: any graded regardless of resubmit capacity
        graded_in_target = [
            a for a in all_assignments
            if a.get("score") is not None
            and a["submission_status"] == "graded"
        ]
    disputed_assignment_id_1 = graded_in_target[0]["id"] if graded_in_target else ""
    grace_candidates = [
        a for a in all_assignments
        if a["id"] in late_within_grace_ids
        and a["id"] != disputed_assignment_id_1
        and (a["course_id"] == dispute_target_cid or not dispute_target_cid)
    ]
    if not grace_candidates:
        grace_candidates = [a for a in all_assignments if a["id"] in late_within_grace_ids and a["id"] != disputed_assignment_id_1]
    disputed_assignment_id_2 = (
        grace_candidates[0]["id"]
        if grace_candidates
        else (graded_in_target[1]["id"] if len(graded_in_target) >= 2 else "")
    )
    # ── LMS-4: force resubmit capacity on the dispute targets ──
    # When the final fallback is hit (any-graded), the chosen assignments may
    # already have attempt_count == max_attempts and the agent cannot
    # resubmit. Bump max_attempts so attempt_count + 1 still fits.
    for _disp_id in (disputed_assignment_id_1, disputed_assignment_id_2):
        if not _disp_id:
            continue
        for a in all_assignments:
            if a["id"] != _disp_id:
                continue
            _attempts = int(a.get("attempt_count", 0) or 0)
            _max_attempts = int(a.get("max_attempts", 1) or 1)
            if _max_attempts < _attempts + 1:
                a["max_attempts"] = _attempts + 1
            break
    disputed_title_1 = next((a["title"] for a in all_assignments if a["id"] == disputed_assignment_id_1), "")
    disputed_title_2 = next((a["title"] for a in all_assignments if a["id"] == disputed_assignment_id_2), "")

    # ── overdue_assignment_id / overdue_assignment_title ──
    overdue_assignment_id = ""
    overdue_assignment_title = ""
    for a in all_assignments:
        if a["submission_status"] == "not_submitted":
            due_at_raw = a["due_at"]
            due_dt = datetime.fromisoformat(due_at_raw) if isinstance(due_at_raw, str) else due_at_raw
            if due_dt < ctx.now:
                overdue_assignment_id = a["id"]
                overdue_assignment_title = a["title"]
                break

    # ── has_remaining_attempts: whether target quiz has remaining attempts ──
    has_remaining_attempts = "false"
    if target_assignment_id:
        tgt = next((a for a in all_assignments if a["id"] == target_assignment_id), None)
        if tgt and tgt.get("attempt_count", 0) < tgt.get("max_attempts", 1):
            has_remaining_attempts = "true"

    # ── unsubmitted_project_ids ──
    unsubmitted_project_ids_list = [
        a["id"] for a in all_assignments
        if a["type"] == "project" and a["submission_status"] == "not_submitted"
    ]
    # Guarantee at least one unsubmitted project so the task is non-vacuous.
    # If the RNG happened to grade every project, forcibly reset the first
    # graded project to not_submitted (preserving score so grade_book still has data).
    if not unsubmitted_project_ids_list:
        for a in all_assignments:
            if a["type"] == "project" and a["submission_status"] == "graded":
                a["submission_status"] = "not_submitted"
                a["submitted_at"] = None
                a["file_name"] = None
                a["attempt_count"] = 0
                # Keep score as None so grade_book skips it
                a["score"] = None
                a["feedback"] = None
                unsubmitted_project_ids_list.append(a["id"])
                break

    # ── next_deadline_assignment_id: earliest upcoming due_at (fallback: nearest overdue) ──
    next_deadline_assignment_id = ""
    next_deadline_dt: datetime | None = None
    fallback_deadline_dt: datetime | None = None
    fallback_deadline_id = ""
    for a in all_assignments:
        if a["submission_status"] == "not_submitted":
            due_at_raw = a["due_at"]
            due_dt = datetime.fromisoformat(due_at_raw) if isinstance(due_at_raw, str) else due_at_raw
            if due_dt > ctx.now:
                if next_deadline_dt is None or due_dt < next_deadline_dt:
                    next_deadline_dt = due_dt
                    next_deadline_assignment_id = a["id"]
            elif fallback_deadline_dt is None or due_dt > fallback_deadline_dt:
                fallback_deadline_dt = due_dt
                fallback_deadline_id = a["id"]

    if not next_deadline_assignment_id:
        next_deadline_assignment_id = fallback_deadline_id

    # ── allows_late_submit: whether the overdue assignment's course allows > 3 late days ──
    # Use the overdue assignment's course (not the randomly-selected target assignment's
    # course) since allows_late_submit gates the oneof branch for overdue submissions.
    overdue_course_id = ""
    if overdue_assignment_id:
        for a in all_assignments:
            if a["id"] == overdue_assignment_id:
                overdue_course_id = a["course_id"]
                break
    allows_late_submit = "false"
    check_cid = overdue_course_id or target_course_id
    if check_cid:
        for c in courses:
            if c["id"] == check_cid:
                max_late = c["syllabus"]["late_policy"]["max_late_days"]
                allows_late_submit = "true" if max_late > 3 else "false"
                break

    # ── missing_assignment_in_lenient_course_id ──
    most_lenient_id = ctx.outputs.get("most_lenient_late_policy_course_id", "")
    if not most_lenient_id:
        # Compute from courses
        best_penalty = None
        for c in courses:
            lp = c["syllabus"]["late_policy"]
            pen = Decimal(str(lp["penalty_per_day"]))
            if best_penalty is None or pen < best_penalty:
                best_penalty = pen
                most_lenient_id = c["id"]
    # Scope to target_course_id when no lenient course is set (so we don't
    # silently fall through to "" when the lookup is empty); otherwise the
    # most-lenient course id is already course-scoped by construction.
    missing_assignment_in_lenient_course_id = ""
    lenient_course_id = most_lenient_id or target_course_id
    for a in all_assignments:
        if (
            a["submission_status"] == "not_submitted"
            and a["course_id"] == lenient_course_id
        ):
            missing_assignment_in_lenient_course_id = a["id"]
            break

    # ── recoverable / unrecoverable assignment IDs ──
    # recoverable_assignment_ids remains the legacy "missing-only" output used by
    # existing LMS tasks. recoverable_submission_ids is the broader combined set
    # for tasks that can submit both late and missing work.
    recoverable_ids: list[str] = []
    unrecoverable_ids: list[str] = []
    recoverable_submission_ids: list[str] = []
    recoverable_missing_ids: list[str] = []
    recoverable_late_ids: list[str] = []
    for a in all_assignments:
        if a["submission_status"] not in ("not_submitted", "late"):
            continue
        due_at_raw = a["due_at"]
        due_dt = datetime.fromisoformat(due_at_raw) if isinstance(due_at_raw, str) else due_at_raw
        if due_dt >= ctx.now:
            continue  # Not overdue
        days_late = (ctx.now - due_dt).days
        # Find course late policy
        max_late = 0
        for c in courses:
            if c["id"] == a["course_id"]:
                max_late = c["syllabus"]["late_policy"]["max_late_days"]
                break
        if days_late <= max_late:
            recoverable_submission_ids.append(a["id"])
            if a["submission_status"] == "not_submitted":
                recoverable_ids.append(a["id"])
                recoverable_missing_ids.append(a["id"])
            else:
                recoverable_late_ids.append(a["id"])
        else:
            unrecoverable_ids.append(a["id"])

    # ── most_disputed_assignment_ids: 2 resubmittable graded assignments with lowest score/max ratio ──
    # Use the catalog-level target_course_id (set by course_catalog builder) when available,
    # since the locally-selected target_course_id may differ from the task's target course.
    _dispute_cid = ctx.outputs.get("target_course_id") or target_course_id
    graded_all = [
        a for a in all_assignments
        if a.get("score") is not None
        and a["submission_status"] == "graded"
        and a["course_id"] == _dispute_cid
        and a.get("attempt_count", 0) < a.get("max_attempts", 1)  # must have remaining attempts
    ]
    if len(graded_all) < 2:
        # Fallback: any resubmittable graded across all courses
        graded_all = [
            a for a in all_assignments
            if a.get("score") is not None
            and a["submission_status"] == "graded"
            and a.get("attempt_count", 0) < a.get("max_attempts", 1)
        ]
    if len(graded_all) < 2:
        # Final fallback: any graded regardless of resubmit capacity
        graded_all = [
            a for a in all_assignments
            if a.get("score") is not None
            and a["submission_status"] == "graded"
        ]
    graded_sorted_by_ratio = sorted(
        graded_all,
        key=lambda a: Decimal(str(a["score"])) / Decimal(str(a["points_possible"]))
        if Decimal(str(a["points_possible"])) != 0 else Decimal("0"),
    )
    most_disputed_ids = [a["id"] for a in graded_sorted_by_ratio[:2]]

    # ── priority_order_ids: unsubmitted assignments due within 7 days ──
    # The instruction in lms_submission_priority.yaml ("due within the next
    # 7 days") and similar tasks expect this list to be the *visible*
    # near-term workload, not every globally-unsubmitted assignment. Filter
    # to a 7-day horizon so the agent's UI-driven shortlist matches.
    seven_day_horizon = ctx.now + timedelta(days=7)
    def _due_within(a: dict) -> bool:
        due_raw = a.get("due_at")
        if not due_raw:
            return False
        due_dt = datetime.fromisoformat(due_raw) if isinstance(due_raw, str) else due_raw
        return due_dt <= seven_day_horizon
    unsubmitted_future = [
        a for a in all_assignments
        if a["submission_status"] == "not_submitted" and _due_within(a)
    ]
    # Build weight lookup from courses
    weight_lookup: dict[str, dict[str, Decimal]] = {}
    for c in courses:
        gp = c["syllabus"]["grading_policy"]
        w: dict[str, Decimal] = {}
        for cat_name, cat_raw in gp.items():
            w[cat_name] = Decimal(str(cat_raw["weight"] if isinstance(cat_raw, dict) else cat_raw.weight))
        weight_lookup[c["id"]] = w

    def _priority_key(a: dict) -> tuple:
        cw = weight_lookup.get(a["course_id"], {})
        w = cw.get(a["weight_category"], Decimal("0"))
        due_raw = a["due_at"]
        due_dt = datetime.fromisoformat(due_raw) if isinstance(due_raw, str) else due_raw
        return (-w, due_dt)

    priority_sorted = sorted(unsubmitted_future, key=_priority_key)
    priority_order_ids = [a["id"] for a in priority_sorted]

    # ── GPA risk analysis (needs grade_book data if available, otherwise best-effort) ──
    # These are computed here for tasks that request them from assignment_battery.
    # grade_book will also compute them with full grade data.
    current_scores = ctx.outputs.get("current_weighted_scores", {})
    student_gpa = Decimal(str(ctx.base.get("student", {}).get("gpa", "3.0")))

    # GPA letter grade mapping
    def _letter_gpa(score: Decimal) -> Decimal:
        if score >= 93: return Decimal("4.0")
        if score >= 90: return Decimal("3.7")
        if score >= 87: return Decimal("3.3")
        if score >= 83: return Decimal("3.0")
        if score >= 80: return Decimal("2.7")
        if score >= 77: return Decimal("2.3")
        if score >= 73: return Decimal("2.0")
        if score >= 70: return Decimal("1.7")
        if score >= 67: return Decimal("1.3")
        if score >= 63: return Decimal("1.0")
        if score >= 60: return Decimal("0.7")
        return Decimal("0.0")

    gpa_risk_ids: list[str] = []
    improvement_ids: list[str] = []
    for c in courses:
        cid = c["id"]
        sc_str = current_scores.get(cid)
        if not sc_str:
            continue
        sc = Decimal(str(sc_str))
        projected_gpa_pt = _letter_gpa(sc)
        if projected_gpa_pt < student_gpa:
            gpa_risk_ids.append(cid)
            # Find unsubmitted assignment in this course
            for a in all_assignments:
                if a["course_id"] == cid and a["submission_status"] == "not_submitted":
                    improvement_ids.append(a["id"])
                    break
    no_risk_flag = "true" if not gpa_risk_ids else "false"

    # ── Discrepancy analysis (best-effort without grade_book) ──
    # These are also computed in grade_book with full data.
    discrepant_cids: list[str] = []
    non_discrepant_cids: list[str] = []
    discrepant_resubmit_ids: list[str] = []

    # ── impossible/achievable course analysis ──
    impossible_cids: list[str] = []
    achievable_cids: list[str] = []
    impossible_b_cids: list[str] = []
    final_exam_asgn_ids: list[str] = []
    next_unsubmitted: list[str] = []

    for c in courses:
        cid = c["id"]
        gp = c["syllabus"]["grading_policy"]

        # Collect graded + ungraded for this course
        course_assignments = [a for a in all_assignments if a["course_id"] == cid]
        remaining_in_course = [a for a in course_assignments if a["score"] is None]
        graded_in_course = [a for a in course_assignments if a["score"] is not None]

        # Find final exam assignment for this course
        final_a = next(
            (a for a in course_assignments if a["type"] == "exam" and "final" in a["weight_category"].lower()),
            None,
        )
        if final_a:
            final_exam_asgn_ids.append(final_a["id"])

        # Next unsubmitted
        next_unsub = next(
            (a for a in course_assignments if a["submission_status"] == "not_submitted"),
            None,
        )
        if next_unsub:
            next_unsubmitted.append(next_unsub["id"])

        # Solve for minimum score needed for B (80%)
        target_pct = Decimal("80")
        total_weight = Decimal("0")
        fixed_part = Decimal("0")
        x_coeff = Decimal("0")

        for cat_name, cat_raw in gp.items():
            weight = Decimal(str(cat_raw["weight"] if isinstance(cat_raw, dict) else cat_raw.weight))
            cat_graded = [a for a in graded_in_course if a["weight_category"] == cat_name and a["score"] is not None]
            cat_remaining = [a for a in remaining_in_course if a["weight_category"] == cat_name]
            n = len(cat_graded) + len(cat_remaining)
            if n == 0:
                continue
            score_sum = Decimal("0")
            for a in cat_graded:
                score_sum += (Decimal(str(a["score"])) / Decimal(str(a["points_possible"]))) * Decimal("100")
            total_weight += weight
            denom = Decimal(str(n))
            fixed_part += weight * score_sum / denom
            x_coeff += weight * Decimal(str(len(cat_remaining))) / denom

        if total_weight > 0 and x_coeff > 0:
            needed = (target_pct * total_weight - fixed_part) / x_coeff
            if needed > Decimal("100"):
                impossible_cids.append(cid)
                impossible_b_cids.append(cid)
            else:
                achievable_cids.append(cid)
        else:
            achievable_cids.append(cid)

    def _first_assignment_id(
        *,
        assignment_type: str,
        course_id: str | None = None,
        status: str | None = None,
    ) -> str:
        for a in all_assignments:
            if a["type"] != assignment_type:
                continue
            if course_id and a["course_id"] != course_id:
                continue
            if status and a["submission_status"] != status:
                continue
            return a["id"]
        return ""

    target_course_id_for_semantics = ctx.outputs.get("target_course_id", "")
    target_quiz_assignment_id = _first_assignment_id(
        assignment_type="quiz",
        course_id=target_course_id_for_semantics or None,
        status="not_submitted",
    )
    if not target_quiz_assignment_id:
        target_quiz_assignment_id = _first_assignment_id(
            assignment_type="quiz",
            status="not_submitted",
        )
    if not target_quiz_assignment_id and quiz_ids:
        # Skip quizzes already reserved as resubmit targets — resetting them to
        # not_submitted would silently invalidate the resubmit_assignment_ids
        # output consumed by lms_resubmit_after_feedback and siblings.
        reset_candidate_ids = [qid for qid in quiz_ids if qid not in resubmit_ids]
        if reset_candidate_ids:
            target_quiz_assignment_id = reset_candidate_ids[0]
            quiz_assignment = next((a for a in all_assignments if a["id"] == target_quiz_assignment_id), None)
            if quiz_assignment is not None:
                quiz_assignment["submission_status"] = "not_submitted"
                quiz_assignment["score"] = None
                quiz_assignment["feedback"] = None
                quiz_assignment["submitted_at"] = None
                quiz_assignment["file_name"] = None
                quiz_assignment["attempt_count"] = 0

    target_project_assignment_id = _first_assignment_id(
        assignment_type="project",
        course_id=target_course_id_for_semantics or None,
        status="not_submitted",
    )
    if not target_project_assignment_id:
        target_project_assignment_id = _first_assignment_id(
            assignment_type="project",
            status="not_submitted",
        )
    if not target_project_assignment_id and project_ids:
        # Skip projects already reserved as resubmit targets (see quiz case above).
        reset_candidate_ids = [pid for pid in project_ids if pid not in resubmit_ids]
        if reset_candidate_ids:
            target_project_assignment_id = reset_candidate_ids[0]
            project_assignment = next((a for a in all_assignments if a["id"] == target_project_assignment_id), None)
            if project_assignment is not None:
                project_assignment["submission_status"] = "not_submitted"
                project_assignment["score"] = None
                project_assignment["feedback"] = None
                project_assignment["submitted_at"] = None
                project_assignment["file_name"] = None
                project_assignment["attempt_count"] = 0

    target_essay_assignment_id = _first_assignment_id(
        assignment_type="essay",
        status="not_submitted",
    )
    if not target_essay_assignment_id:
        target_essay_assignment_id = _first_assignment_id(assignment_type="essay")
    if not target_essay_assignment_id and essay_ids:
        target_essay_assignment_id = essay_ids[0]

    # ── highest_points_unsubmitted_id (+ rubric count, decoy) ──
    # For lms_review_rubric_submit: the agent must NOT be told which assignment
    # to submit. Instead it must enumerate the still-unsubmitted assignments in
    # the target course, review each rubric, and pick the one worth the most
    # points (the rubric with the highest point total). Tie-break: earliest
    # due_at, then the title that comes first alphabetically (both are
    # agent-observable on the assignments page). The rubric criteria count of
    # that winner is encoded into the required file_name so the agent must
    # actually open the rubric. A sibling decoy in a DIFFERENT course (also
    # unsubmitted) is exposed so the task can freeze it as a critical invariant.
    _rrs_target_cid = ctx.outputs.get("target_course_id", "") or target_course_id

    def _id_num(aid: str) -> int:
        try:
            return int(str(aid).rsplit("_", 1)[-1])
        except (ValueError, IndexError):
            return 0

    def _due_dt(a: dict[str, Any]) -> datetime:
        raw = a.get("due_at")
        if isinstance(raw, str):
            return datetime.fromisoformat(raw)
        return raw  # type: ignore[return-value]

    rrs_unsubmitted = [
        a for a in all_assignments
        if a["course_id"] == _rrs_target_cid
        and a["submission_status"] == "not_submitted"
    ]
    highest_points_unsubmitted_id = ""
    highest_points_rubric_count = 0
    if rrs_unsubmitted:
        rrs_winner = sorted(
            rrs_unsubmitted,
            key=lambda a: (
                -Decimal(str(a["points_possible"])),
                _due_dt(a),
                str(a.get("title", "")),
            ),
        )[0]
        highest_points_unsubmitted_id = rrs_winner["id"]
        highest_points_rubric_count = len(rrs_winner.get("rubric", []) or [])

    # Sibling decoy: an unsubmitted assignment in a DIFFERENT course than the
    # winner (so escalating its invariant to critical punishes the agent that
    # submits the wrong-course look-alike). Prefer one with the same or higher
    # points so it is genuinely tempting.
    rrs_decoy_id = ""
    if highest_points_unsubmitted_id:
        decoy_pool = [
            a for a in all_assignments
            if a["course_id"] != _rrs_target_cid
            and a["submission_status"] == "not_submitted"
            and a["id"] != highest_points_unsubmitted_id
        ]
        if decoy_pool:
            rrs_decoy_id = sorted(
                decoy_pool,
                key=lambda a: (-Decimal(str(a["points_possible"])), _id_num(a["id"])),
            )[0]["id"]
    # ── FINAL uniqueness pass for sole_recoverable_missing ──
    # This is the AUTHORITATIVE enforcement and runs as the last mutation, after
    # every quiz/project/essay/exam reset above (any of which can flip an extra
    # assignment back to not_submitted with a past-due-recoverable date). It
    # recomputes the sole recoverable assignment from scratch over the CURRENT
    # not_submitted set, pins it to 1 day past due (slack = max_late_days − 1),
    # and forces every other not_submitted assignment to be unambiguously
    # unrecoverable or comfortably future — so exactly one assignment sits inside
    # its course's late window with margin against the ±1 day seed-anchor drift.
    # The standard target_* outputs are re-pointed at the recomputed sole so the
    # task's instruction ("submit that one") stays consistent with the seed.
    if sole_recoverable_missing:
        def _due_dt_final(a: dict[str, Any]) -> datetime:
            raw = a["due_at"]
            return datetime.fromisoformat(raw) if isinstance(raw, str) else raw

        def _is_rec_now(a: dict[str, Any]) -> bool:
            if a["submission_status"] != "not_submitted":
                return False
            course = courses_by_id.get(a["course_id"])
            if not course:
                return False
            due = _due_dt_final(a)
            if due >= ctx.now:
                return False
            ml = int(course["syllabus"]["late_policy"]["max_late_days"])
            return (ctx.now - due).days <= ml

        rec_now = [a for a in all_assignments if _is_rec_now(a)]
        if rec_now:
            def _slack_now(a: dict[str, Any]) -> int:
                ml = int(courses_by_id[a["course_id"]]["syllabus"]["late_policy"]["max_late_days"])
                return ml - (ctx.now - _due_dt_final(a)).days

            ranked = sorted(
                rec_now,
                key=lambda a: (
                    _slack_now(a),
                    -Decimal(str(a["points_possible"])),
                    _due_dt_final(a),
                    a["id"],
                ),
            )
            sole = ranked[0]
            sole_recoverable_id = sole["id"]
            sole["due_at"] = (ctx.now - timedelta(days=2)).isoformat()
            sole["submission_status"] = "not_submitted"
            sole["score"] = None
            sole["feedback"] = None
            sole["submitted_at"] = None
            sole["file_name"] = None
            sole["attempt_count"] = 0
            # Re-point the task target at the (authoritative) sole assignment.
            target_assignment_id = sole["id"]
            target_assignment_title = sole["title"]
            target_course_id = sole["course_id"]
            for c in courses:
                if c["id"] == target_course_id:
                    target_course_code = c["course_code"]
                    break

        for a in all_assignments:
            if a["submission_status"] != "not_submitted" or a["id"] == sole_recoverable_id:
                continue
            course = courses_by_id.get(a["course_id"])
            if not course:
                continue
            ml = int(course["syllabus"]["late_policy"]["max_late_days"])
            due = _due_dt_final(a)
            if due < ctx.now + timedelta(days=2):
                a["due_at"] = (ctx.now - timedelta(days=ml + 3)).isoformat()
            elif due < ctx.now + timedelta(days=5):
                a["due_at"] = (ctx.now + timedelta(days=5)).isoformat()
            a["score"] = None
            a["feedback"] = None
            a["submitted_at"] = None
            a["file_name"] = None
            a["attempt_count"] = 0
            if _due_dt_final(a) < ctx.now and a["id"] not in missing_ids:
                missing_ids.append(a["id"])

        # Re-pick the decoy from a DIFFERENT course than the (possibly updated)
        # target so the decoy invariant/constraint references a real sibling.
        if not decoy_assignment_id or any(
            a["id"] == decoy_assignment_id and a["course_id"] == target_course_id
            for a in all_assignments
        ):
            decoy_pool = [
                a for a in all_assignments
                if a["course_id"] != target_course_id and a["id"] != target_assignment_id
                and a["submission_status"] in ("not_submitted", "graded")
            ] or [
                a for a in all_assignments
                if a["course_id"] != target_course_id and a["id"] != target_assignment_id
            ]
            if decoy_pool:
                decoy_assignment_id = decoy_pool[0]["id"]

    return {
        "assignment_ids": all_assignment_ids,
        "highest_points_unsubmitted_id": highest_points_unsubmitted_id,
        "highest_points_rubric_count": str(highest_points_rubric_count),
        "rubric_review_decoy_id": rrs_decoy_id,
        "missing_assignment_ids": missing_ids,
        "late_assignment_ids": late_ids,
        "late_within_grace_ids": late_within_grace_ids,
        "lowest_homework_id": lowest_homework_id or "",
        "exam_assignment_id": exam_assignment_id or "",
        "resubmit_assignment_ids": resubmit_ids,
        "target_assignment_id": target_assignment_id or "",
        "target_assignment_title": target_assignment_title,
        "target_course_id": target_course_id,
        "target_course_code": target_course_code,
        "decoy_assignment_id": decoy_assignment_id or "",
        "file_name": file_name,
        # ── Sole-recoverable discriminator (lms_submit_late hardening) ──
        # The unique past-due not_submitted assignment kept inside its course's
        # late window when sole_recoverable_missing is set; "" otherwise.
        "sole_recoverable_missing_id": sole_recoverable_id,
        # ── New outputs ──
        "score_below_70": score_below_70,
        "feedback_assignment_id": feedback_assignment_id,
        "course_plan_assignment_id": course_plan_assignment_id,
        "unsubmitted_hw_id": unsubmitted_hw_id,
        "disputed_assignment_id_1": disputed_assignment_id_1,
        "disputed_assignment_id_2": disputed_assignment_id_2,
        "disputed_title_1": disputed_title_1,
        "disputed_title_2": disputed_title_2,
        "overdue_assignment_id": overdue_assignment_id,
        "overdue_assignment_title": overdue_assignment_title,
        "has_remaining_attempts": has_remaining_attempts,
        "unsubmitted_project_ids": ",".join(unsubmitted_project_ids_list),
        "next_deadline_assignment_id": next_deadline_assignment_id,
        "allows_late_submit": allows_late_submit,
        "missing_assignment_in_lenient_course_id": missing_assignment_in_lenient_course_id,
        "recoverable_assignment_ids": ",".join(recoverable_ids),
        "recoverable_missing_assignment_ids": ",".join(recoverable_missing_ids),
        "recoverable_late_assignment_ids": ",".join(recoverable_late_ids),
        "recoverable_submission_ids": ",".join(recoverable_submission_ids),
        "unrecoverable_assignment_ids": ",".join(unrecoverable_ids),
        "worth_submitting_ids": ",".join(recoverable_ids),
        "not_worth_ids": ",".join(unrecoverable_ids),
        "most_disputed_assignment_ids": ",".join(most_disputed_ids),
        "priority_order_ids": ",".join(priority_order_ids),
        "gpa_risk_course_ids": ",".join(gpa_risk_ids),
        "improvement_assignment_ids": ",".join(improvement_ids),
        "no_risk_flag": no_risk_flag,
        "discrepant_course_ids": ",".join(discrepant_cids),
        "discrepant_resubmit_assignment_ids": ",".join(discrepant_resubmit_ids),
        "non_discrepant_course_ids": ",".join(non_discrepant_cids),
        "impossible_course_ids": ",".join(impossible_cids),
        "achievable_course_ids": ",".join(achievable_cids),
        "impossible_b_course_ids": ",".join(impossible_b_cids),
        "final_exam_assignment_ids": ",".join(final_exam_asgn_ids),
        "final_exam_assignment_id": final_exam_asgn_ids[0] if final_exam_asgn_ids else (exam_assignment_id or ""),
        "next_unsubmitted_ids": ",".join(next_unsubmitted),
        "lowest_hw_id": lowest_homework_id or "",
        "most_impactful_graded_id": most_disputed_ids[0] if most_disputed_ids else "",
        "worst_category_assignment_id": "",
        "target_quiz_assignment_id": target_quiz_assignment_id,
        "target_project_assignment_id": target_project_assignment_id,
        "target_essay_assignment_id": target_essay_assignment_id,
    }


# ---------------------------------------------------------------------------
# 5. grade_book
# ---------------------------------------------------------------------------

@_register("grade_book")
def _build_grade_book(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create Grade records for all graded assignments.

    Applies drop-lowest logic, computes current weighted scores per course,
    and solves for minimum_final_score_for_b.

    Params
    ------
    (no params -- operates on existing assignments in ctx.base)
    """
    assignments = ctx.base.get("assignments", [])
    courses = ctx.base.get("courses", [])
    enrollments = ctx.base.get("enrollments", [])

    if "grades" not in ctx.base:
        ctx.base["grades"] = []

    grade_ids: list[str] = []
    dropped_grade_ids: list[str] = []

    # Build enrollment lookup: course_id -> enrollment_id
    enrollment_by_course: dict[str, str] = {}
    for e in enrollments:
        enrollment_by_course[e["course_id"]] = e["id"]

    # Create grade records for graded/late assignments
    for a in assignments:
        if a["score"] is None:
            continue
        if a["submission_status"] not in ("graded", "late", "resubmit_requested"):
            continue

        enrollment_id = enrollment_by_course.get(a["course_id"], "")
        if not enrollment_id:
            continue

        # Compute late penalty
        late_penalty = Decimal("0")
        if a["submission_status"] == "late" and a.get("submitted_at"):
            # Find course late policy
            for c in courses:
                if c["id"] == a["course_id"]:
                    lp = c["syllabus"]["late_policy"]
                    due_at = datetime.fromisoformat(a["due_at"]) if isinstance(a["due_at"], str) else a["due_at"]
                    sub_at = datetime.fromisoformat(a["submitted_at"]) if isinstance(a["submitted_at"], str) else a["submitted_at"]
                    elapsed = sub_at - due_at
                    elapsed_hours = Decimal(str(elapsed.total_seconds())) / Decimal("3600")
                    grace = Decimal(str(lp["grace_period_hours"]))
                    if elapsed_hours > grace:
                        hours_past = elapsed_hours - grace
                        days_late = math.ceil(float(hours_past) / 24)
                        if days_late > lp["max_late_days"]:
                            late_penalty = Decimal("1")
                        else:
                            late_penalty = min(
                                Decimal(str(days_late)) * Decimal(str(lp["penalty_per_day"])),
                                Decimal("1"),
                            )
                    break

        grade = Grade(
            id=ctx.next_id("grade"),
            enrollment_id=enrollment_id,
            course_id=a["course_id"],
            assignment_id=a["id"],
            score=Decimal(str(a["score"])),
            points_possible=Decimal(str(a["points_possible"])),
            weight_category=a["weight_category"],
            is_dropped=False,
            late_penalty_applied=late_penalty,
        )
        ctx.base["grades"].append(grade.model_dump())
        grade_ids.append(grade.id)

    # Apply drop-lowest logic per course/category
    for c in courses:
        course_id = c["id"]
        grading_policy = c["syllabus"]["grading_policy"]
        for cat_name, cat_policy_raw in grading_policy.items():
            drop_lowest = cat_policy_raw["drop_lowest"] if isinstance(cat_policy_raw, dict) else cat_policy_raw.drop_lowest
            if drop_lowest == 0:
                continue

            cat_grades = [
                g for g in ctx.base["grades"]
                if g["course_id"] == course_id
                and g["weight_category"] == cat_name
                and g["score"] is not None
            ]
            if not cat_grades:
                continue

            effective_drop = drop_lowest
            if len(cat_grades) <= effective_drop:
                effective_drop = max(0, len(cat_grades) - 1)

            sorted_grades = sorted(
                cat_grades,
                key=lambda g: Decimal(str(g["score"])) / Decimal(str(g["points_possible"]))
                if Decimal(str(g["points_possible"])) != 0 else Decimal("0"),
            )
            for g in sorted_grades[:effective_drop]:
                g["is_dropped"] = True
                dropped_grade_ids.append(g["id"])

    # Compute current weighted scores per course
    current_weighted_scores: dict[str, str] = {}
    minimum_final_score_for_b: str | None = None

    for c in courses:
        course_id = c["id"]
        grading_policy = c["syllabus"]["grading_policy"]
        graded_weight = Decimal("0")
        weighted_sum = Decimal("0")

        for cat_name, cat_policy_raw in grading_policy.items():
            weight = Decimal(str(cat_policy_raw["weight"] if isinstance(cat_policy_raw, dict) else cat_policy_raw.weight))

            cat_grades = [
                g for g in ctx.base["grades"]
                if g["course_id"] == course_id
                and g["weight_category"] == cat_name
                and g["score"] is not None
                and not g["is_dropped"]
            ]
            if not cat_grades:
                continue

            total = Decimal("0")
            for g in cat_grades:
                # LMS-8: use the raw score the UI displays, not the
                # penalty-adjusted internal value. Late penalty is shown as
                # a separate column on the Grades page; the seed branch
                # decisions (drop_impact_above_3, drop_changes_letter, etc.)
                # should match what the agent computes from the visible
                # numbers, not the hidden post-penalty math.
                pct = (Decimal(str(g["score"])) / Decimal(str(g["points_possible"]))) * Decimal("100")
                total += pct

            cat_avg = (total / Decimal(str(len(cat_grades)))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            graded_weight += weight
            weighted_sum += cat_avg * weight

        if graded_weight > Decimal("0"):
            course_score = (weighted_sum / graded_weight).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            current_weighted_scores[course_id] = str(course_score)

    # Compute minimum score for B (80%) on the first course with ungraded assignments
    for c in courses:
        course_id = c["id"]
        remaining = [
            {"weight_category": a["weight_category"], "points_possible": Decimal(str(a["points_possible"]))}
            for a in assignments
            if a["course_id"] == course_id and a["score"] is None
        ]
        if not remaining:
            continue

        # Use the LMSState model's computation by constructing a temporary state
        # Instead, replicate the logic inline to avoid full state construction
        grading_policy = c["syllabus"]["grading_policy"]
        target_pct = Decimal("80")  # B

        total_weight = Decimal("0")
        fixed_part = Decimal("0")
        x_coefficient = Decimal("0")

        for cat_name, cat_policy_raw in grading_policy.items():
            weight = Decimal(str(cat_policy_raw["weight"] if isinstance(cat_policy_raw, dict) else cat_policy_raw.weight))
            active_grades = [
                g for g in ctx.base["grades"]
                if g["course_id"] == course_id
                and g["weight_category"] == cat_name
                and g["score"] is not None
                and not g["is_dropped"]
            ]
            remaining_in_cat = [r for r in remaining if r["weight_category"] == cat_name]

            current_count = len(active_grades)
            remaining_count = len(remaining_in_cat)
            n = current_count + remaining_count
            if n == 0:
                continue

            score_sum = Decimal("0")
            for g in active_grades:
                effective = Decimal(str(g["score"])) * (Decimal("1") - Decimal(str(g["late_penalty_applied"])))
                score_sum += (effective / Decimal(str(g["points_possible"]))) * Decimal("100")

            total_weight += weight
            denom = Decimal(str(n))
            fixed_part += weight * score_sum / denom
            x_coefficient += weight * Decimal(str(remaining_count)) / denom

        if total_weight > Decimal("0") and x_coefficient > Decimal("0"):
            needed = (target_pct * total_weight - fixed_part) / x_coefficient
            if needed <= Decimal("100") and needed >= Decimal("0"):
                minimum_final_score_for_b = str(
                    needed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
            elif needed < Decimal("0"):
                minimum_final_score_for_b = "0.00"
            break  # Use first course with remaining assignments

    # ── Helper: compute weighted score for a course (with/without drops) ──
    def _weighted_score(course_id: str, *, include_dropped: bool = False) -> Decimal:
        c = next((c for c in courses if c["id"] == course_id), None)
        if not c:
            return Decimal("0")
        gp = c["syllabus"]["grading_policy"]
        gw = Decimal("0")
        ws = Decimal("0")
        for cn, cp in gp.items():
            w = Decimal(str(cp["weight"] if isinstance(cp, dict) else cp.weight))
            cg = [
                g for g in ctx.base["grades"]
                if g["course_id"] == course_id and g["weight_category"] == cn
                and g["score"] is not None
                and (include_dropped or not g["is_dropped"])
            ]
            if not cg:
                continue
            t = Decimal("0")
            for g in cg:
                # LMS-8: drop the (1 - late_penalty_applied) factor for the
                # seed branch decision; see comment above current_weighted_scores.
                pct = (Decimal(str(g["score"])) / Decimal(str(g["points_possible"]))) * Decimal("100")
                t += pct
            avg = t / Decimal(str(len(cg)))
            gw += w
            ws += avg * w
        if gw > 0:
            return (ws / gw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return Decimal("0")

    def _recompute_dropped_grade_ids() -> list[str]:
        recomputed: list[str] = []
        for g in ctx.base["grades"]:
            g["is_dropped"] = False
        for c in courses:
            course_id = c["id"]
            for cat_name, cat_policy_raw in c["syllabus"]["grading_policy"].items():
                drop_lowest = cat_policy_raw["drop_lowest"] if isinstance(cat_policy_raw, dict) else cat_policy_raw.drop_lowest
                if drop_lowest == 0:
                    continue
                cat_grades = [
                    g for g in ctx.base["grades"]
                    if g["course_id"] == course_id
                    and g["weight_category"] == cat_name
                    and g["score"] is not None
                ]
                if not cat_grades:
                    continue
                effective_drop = drop_lowest
                if len(cat_grades) <= effective_drop:
                    effective_drop = max(0, len(cat_grades) - 1)
                sorted_grades = sorted(
                    cat_grades,
                    key=lambda g: Decimal(str(g["score"])) / Decimal(str(g["points_possible"]))
                    if Decimal(str(g["points_possible"])) != 0 else Decimal("0"),
                )
                for g in sorted_grades[:effective_drop]:
                    g["is_dropped"] = True
                    recomputed.append(g["id"])
        return recomputed

    def _letter(score: Decimal) -> str:
        if score >= 93: return "A"
        if score >= 90: return "A-"
        if score >= 87: return "B+"
        if score >= 83: return "B"
        if score >= 80: return "B-"
        if score >= 77: return "C+"
        if score >= 73: return "C"
        if score >= 70: return "C-"
        if score >= 67: return "D+"
        if score >= 63: return "D"
        if score >= 60: return "D-"
        return "F"

    def _letter_gpa(score: Decimal) -> Decimal:
        if score >= 93: return Decimal("4.0")
        if score >= 90: return Decimal("3.7")
        if score >= 87: return Decimal("3.3")
        if score >= 83: return Decimal("3.0")
        if score >= 80: return Decimal("2.7")
        if score >= 77: return Decimal("2.3")
        if score >= 73: return Decimal("2.0")
        if score >= 70: return Decimal("1.7")
        if score >= 67: return Decimal("1.3")
        if score >= 63: return Decimal("1.0")
        if score >= 60: return Decimal("0.7")
        return Decimal("0.0")

    # ── has_discrepancy: displayed vs computed > 1pt ──
    # We compare current_weighted_scores (computed with drops) vs without drops
    has_discrepancy = "false"
    for c in courses:
        cid = c["id"]
        with_drops = current_weighted_scores.get(cid)
        if with_drops is None:
            continue
        without_drops = _weighted_score(cid, include_dropped=True)
        if abs(Decimal(with_drops) - without_drops) > Decimal("1"):
            has_discrepancy = "true"
            break

    # ── most_recent_graded_id: grade with the most recent assignment due_at ──
    most_recent_graded_id = ""
    most_recent_dt: datetime | None = None
    for g in ctx.base["grades"]:
        if g["score"] is None:
            continue
        # Find assignment due_at
        a = next((a for a in assignments if a["id"] == g["assignment_id"]), None)
        if not a:
            continue
        due_raw = a["due_at"]
        due_dt = datetime.fromisoformat(due_raw) if isinstance(due_raw, str) else due_raw
        if most_recent_dt is None or due_dt > most_recent_dt:
            most_recent_dt = due_dt
            most_recent_graded_id = g["assignment_id"]

    # ── most_impactful_graded_id: graded assignment in highest-weight category ──
    most_impactful_graded_id = ""
    best_impact_weight = Decimal("-1")
    for g in ctx.base["grades"]:
        if g["score"] is None or g["is_dropped"]:
            continue
        a = next((a for a in assignments if a["id"] == g["assignment_id"]), None)
        if not a:
            continue
        c = next((c for c in courses if c["id"] == g["course_id"]), None)
        if not c:
            continue
        gp = c["syllabus"]["grading_policy"]
        cat_raw = gp.get(g["weight_category"])
        if not cat_raw:
            continue
        w = Decimal(str(cat_raw["weight"] if isinstance(cat_raw, dict) else cat_raw.weight))
        if w > best_impact_weight:
            best_impact_weight = w
            most_impactful_graded_id = g["assignment_id"]

    # ── drop_changes_letter: whether applying drop-lowest changes letter grade ──
    # Compare letter grade of first course with and without drops
    drop_changes_letter = "false"
    target_cid = ctx.outputs.get("target_course_id", "")
    if not target_cid and courses:
        target_cid = courses[0]["id"]
    if target_cid:
        score_with = current_weighted_scores.get(target_cid)
        score_without = _weighted_score(target_cid, include_dropped=True)
        if score_with is not None:
            if _letter(Decimal(score_with)) != _letter(score_without):
                drop_changes_letter = "true"

    # ── drop_impact_above_3: whether drop impact > 3 points ──
    drop_impact_above_3 = "false"
    if target_cid:
        score_with = current_weighted_scores.get(target_cid)
        score_without = _weighted_score(target_cid, include_dropped=True)
        if score_with is not None:
            if abs(Decimal(score_with) - score_without) > Decimal("3"):
                drop_impact_above_3 = "true"

    # ── curve_changes_letter: whether +5pts on midterm changes letter grade ──
    curve_changes_letter = "false"
    if target_cid:
        # Find midterm grade and temporarily boost by 5
        midterm_grades = [
            g for g in ctx.base["grades"]
            if g["course_id"] == target_cid
            and g["weight_category"] in ("midterm",)
            and g["score"] is not None
            and not g["is_dropped"]
        ]
        if midterm_grades:
            original_letter = _letter(Decimal(current_weighted_scores.get(target_cid, "0")))
            # Temporarily add 5 to midterm score
            mg = midterm_grades[0]
            orig_score = Decimal(str(mg["score"]))
            mg["score"] = str(min(orig_score + Decimal("5"), Decimal(str(mg["points_possible"]))))
            curved_score = _weighted_score(target_cid)
            mg["score"] = str(orig_score)  # Restore
            if _letter(curved_score) != original_letter:
                curve_changes_letter = "true"

    # ── min_score_achievable: recomputed below after final exam target selection ──
    min_score_achievable = "false"

    # ── final_exam_assignment_id: ID of a final exam assignment (single) ──
    # Prefer the target_course_id's final exam so tasks like
    # lms_minimum_final_score (whose instruction names a specific course) stay
    # internally consistent: instruction says "in CS101" → final exam target is
    # CS101's final, not some other course's exam picked by iteration order.
    # The reset block below will force it to not_submitted if needed.
    _grade_book_target_cid = ctx.outputs.get("target_course_id", "")
    final_exam_assignment_id = ""
    if _grade_book_target_cid:
        # Target-course final: prefer not_submitted, then any status
        for a in assignments:
            if (a["course_id"] == _grade_book_target_cid
                    and a["type"] == "exam"
                    and a["weight_category"] == "final"
                    and a["submission_status"] == "not_submitted"):
                final_exam_assignment_id = a["id"]
                break
        if not final_exam_assignment_id:
            for a in assignments:
                if (a["course_id"] == _grade_book_target_cid
                        and a["type"] == "exam"
                        and a["weight_category"] == "final"):
                    final_exam_assignment_id = a["id"]
                    break
    if not final_exam_assignment_id:
        # No target course (or target has no final): fall back to any final exam
        for a in assignments:
            if a["type"] == "exam" and a["weight_category"] == "final" and a["submission_status"] == "not_submitted":
                final_exam_assignment_id = a["id"]
                break
    if not final_exam_assignment_id:
        for a in assignments:
            if a["type"] == "exam" and a["weight_category"] == "final":
                final_exam_assignment_id = a["id"]
                break
    if not final_exam_assignment_id:
        # Fallback: any unsubmitted exam
        for a in assignments:
            if a["type"] == "exam" and a["submission_status"] == "not_submitted":
                final_exam_assignment_id = a["id"]
                break
    if not final_exam_assignment_id:
        # Last resort: any exam
        for a in assignments:
            if a["type"] == "exam":
                final_exam_assignment_id = a["id"]
                break

    # ── Guarantee final_exam_assignment_id is submittable ──
    # The task may need to submit study_plan.pdf to this final exam. When the
    # final belongs to the target course (the selection above), force-reset it
    # to not_submitted unconditionally so the task is always solvable. For
    # non-target finals (fallback path) only reset if there are no remaining
    # attempts, to avoid disturbing fixtures other tasks rely on.
    if final_exam_assignment_id:
        fa = next((a for a in assignments if a["id"] == final_exam_assignment_id), None)
        if fa and fa["submission_status"] not in ("not_submitted",):
            is_target_final = (
                _grade_book_target_cid
                and fa["course_id"] == _grade_book_target_cid
            )
            needs_reset = is_target_final or (
                fa.get("attempt_count", 0) >= fa.get("max_attempts", 1)
            )
            if needs_reset:
                fa["submission_status"] = "not_submitted"
                fa["score"] = None
                fa["file_name"] = None
                fa["submitted_at"] = None
                fa["attempt_count"] = 0
                # Also remove the corresponding grade record so weighted score stays consistent
                ctx.base["grades"] = [
                    g for g in ctx.base["grades"]
                    if g["assignment_id"] != final_exam_assignment_id
                ]
        if fa:
            current_weighted_scores[fa["course_id"]] = str(_weighted_score(fa["course_id"]))

    def _minimum_final_exam_score_for_b(assignment_id: str) -> str:
        final_assignment = next((a for a in assignments if a["id"] == assignment_id), None)
        if final_assignment is None:
            return ""
        course = next((c for c in courses if c["id"] == final_assignment["course_id"]), None)
        if course is None:
            return ""

        total_weight = Decimal("0")
        fixed_part = Decimal("0")
        x_coefficient = Decimal("0")
        for cat_name, cat_policy_raw in course["syllabus"]["grading_policy"].items():
            weight = Decimal(str(cat_policy_raw["weight"] if isinstance(cat_policy_raw, dict) else cat_policy_raw.weight))
            active = [
                g for g in ctx.base["grades"]
                if g["course_id"] == final_assignment["course_id"]
                and g["weight_category"] == cat_name
                and g["assignment_id"] != assignment_id
                and g["score"] is not None
                and not g["is_dropped"]
            ]
            includes_final = cat_name == final_assignment["weight_category"]
            n = len(active) + (1 if includes_final else 0)
            if n == 0:
                continue
            score_sum = Decimal("0")
            for g in active:
                effective = Decimal(str(g["score"])) * (Decimal("1") - Decimal(str(g["late_penalty_applied"])))
                score_sum += (effective / Decimal(str(g["points_possible"]))) * Decimal("100")
            total_weight += weight
            denom = Decimal(str(n))
            fixed_part += weight * score_sum / denom
            if includes_final:
                x_coefficient += weight / denom

        if total_weight == 0 or x_coefficient == 0:
            return ""
        needed = (Decimal("80") * total_weight - fixed_part) / x_coefficient
        if needed > Decimal("100"):
            return ""
        if needed < Decimal("0"):
            needed = Decimal("0")
        return str(needed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    minimum_final_score_for_b = (
        _minimum_final_exam_score_for_b(final_exam_assignment_id)
        if final_exam_assignment_id else ""
    )
    min_score_achievable = "true" if minimum_final_score_for_b else "false"

    # ── final_exam_assignment_ids: per-course final exam assignment IDs ──
    final_exam_assignment_ids_list: list[str] = []
    for c in courses:
        cid = c["id"]
        fa = next(
            (a for a in assignments if a["course_id"] == cid and a["type"] == "exam" and a["weight_category"] == "final"),
            None,
        )
        if not fa:
            fa = next((a for a in assignments if a["course_id"] == cid and a["type"] == "exam"), None)
        if fa:
            final_exam_assignment_ids_list.append(fa["id"])

    # ── lower_grade_course_id / lower_grade_enrollment_id ──
    lower_grade_course_id = ""
    lower_grade_enrollment_id = ""
    if len(courses) >= 2:
        scored = [(cid, Decimal(s)) for cid, s in current_weighted_scores.items()]
        if scored:
            scored.sort(key=lambda x: x[1])
            lower_grade_course_id = scored[0][0]
            lower_grade_enrollment_id = enrollment_by_course.get(lower_grade_course_id, "")

    # ── lowest_hw_id: lowest homework assignment in the target course (by grade record ratio) ──
    # Recompute from grade records (post victim-modification) so that the result matches
    # what LMSState.dropped_grades_for_category() returns.
    lowest_hw_id = ""
    _hw_target_cid = target_cid or ctx.outputs.get("target_course_id", "")
    if _hw_target_cid:
        _hw_grades = [
            g for g in ctx.base["grades"]
            if g["course_id"] == _hw_target_cid
            and g["weight_category"] == "homework"
            and g.get("score") is not None
        ]
        if _hw_grades:
            _hw_candidates = [g for g in _hw_grades if g.get("is_dropped", False)] or _hw_grades
            _hw_lowest = min(
                _hw_candidates,
                key=lambda g: Decimal(str(g["score"])) / Decimal(str(g["points_possible"]))
                if Decimal(str(g["points_possible"])) != 0 else Decimal("0"),
            )
            lowest_hw_id = _hw_lowest["assignment_id"]
    if not lowest_hw_id:
        # Fallback to what assignment_battery computed
        lowest_hw_id = ctx.outputs.get("lowest_homework_id", "")

    # ── grade_below_80: whether weighted score < 80 ──
    grade_below_80 = "false"
    if target_cid:
        sc_str = current_weighted_scores.get(target_cid)
        if sc_str and Decimal(sc_str) < Decimal("80"):
            grade_below_80 = "true"

    # ── highest_weight_ungraded_id ──
    highest_weight_ungraded_id = ""
    best_hw_weight = Decimal("-1")
    for a in assignments:
        if a["score"] is not None or a["course_id"] != target_cid:
            continue
        c = next((c for c in courses if c["id"] == a["course_id"]), None)
        if not c:
            continue
        gp = c["syllabus"]["grading_policy"]
        cat_raw = gp.get(a["weight_category"])
        if not cat_raw:
            continue
        w = Decimal(str(cat_raw["weight"] if isinstance(cat_raw, dict) else cat_raw.weight))
        if w > best_hw_weight:
            best_hw_weight = w
            highest_weight_ungraded_id = a["id"]

    # ── worst_category_assignment_id ──
    worst_category_assignment_id = ""
    if target_cid:
        c = next((c for c in courses if c["id"] == target_cid), None)
        if c:
            gp = c["syllabus"]["grading_policy"]
            # Find worst-performing category (lowest avg) with high weight
            worst_score = None
            worst_cat = None
            for cn, cp in gp.items():
                w = Decimal(str(cp["weight"] if isinstance(cp, dict) else cp.weight))
                if w < Decimal("0.10"):
                    continue  # skip low-weight
                cg = [
                    g for g in ctx.base["grades"]
                    if g["course_id"] == target_cid and g["weight_category"] == cn
                    and g["score"] is not None and not g["is_dropped"]
                ]
                if not cg:
                    continue
                avg = sum(Decimal(str(g["score"])) / Decimal(str(g["points_possible"])) for g in cg) / len(cg)
                if worst_score is None or avg < worst_score:
                    worst_score = avg
                    worst_cat = cn
            if worst_cat:
                unsub = next(
                    (a for a in assignments
                     if a["course_id"] == target_cid and a["weight_category"] == worst_cat
                     and a["submission_status"] == "not_submitted"),
                    None,
                )
                if unsub:
                    worst_category_assignment_id = unsub["id"]
                else:
                    # Fallback: search across all enrolled courses for a
                    # not_submitted assignment in the same worst category
                    enrolled_cids = {e["course_id"] for e in ctx.base.get("enrollments", [])}
                    unsub_any = next(
                        (a for a in assignments
                         if a["course_id"] in enrolled_cids
                         and a["weight_category"] == worst_cat
                         and a["submission_status"] == "not_submitted"),
                        None,
                    )
                    if unsub_any:
                        worst_category_assignment_id = unsub_any["id"]

    # ── impossible/achievable course IDs (computed with grade data) ──
    impossible_course_ids: list[str] = []
    achievable_course_ids: list[str] = []
    impossible_b_course_ids: list[str] = []
    next_unsubmitted_ids: list[str] = []

    def _compute_need(cid: str, gp: dict) -> tuple[Decimal, Decimal, Decimal]:
        """Return (need, tw, xc) for a course. need=Decimal('inf') if xc==0."""
        rem = [a for a in assignments if a["course_id"] == cid and a["score"] is None]
        t_pct = Decimal("80")
        tw = Decimal("0")
        fp = Decimal("0")
        xc = Decimal("0")
        for cn, cp in gp.items():
            w = Decimal(str(cp["weight"] if isinstance(cp, dict) else cp.weight))
            ag = [g for g in ctx.base["grades"]
                  if g["course_id"] == cid and g["weight_category"] == cn
                  and g["score"] is not None and not g["is_dropped"]]
            rc = [r for r in rem if r["weight_category"] == cn]
            n = len(ag) + len(rc)
            if n == 0:
                continue
            ss = Decimal("0")
            for g in ag:
                eff = Decimal(str(g["score"])) * (Decimal("1") - Decimal(str(g["late_penalty_applied"])))
                ss += (eff / Decimal(str(g["points_possible"]))) * Decimal("100")
            tw += w
            dn = Decimal(str(n))
            fp += w * ss / dn
            xc += w * Decimal(str(len(rc))) / dn
        if tw > 0 and xc > 0:
            need = (Decimal("80") * tw - fp) / xc
        else:
            need = Decimal("0")  # no remaining → achievable
        return need, tw, xc

    for c in courses:
        cid = c["id"]
        gp = c["syllabus"]["grading_policy"]
        rem = [a for a in assignments if a["course_id"] == cid and a["score"] is None]
        if not rem:
            achievable_course_ids.append(cid)
            continue

        need, tw, xc = _compute_need(cid, gp)

        if tw > 0 and xc > 0:
            if need > Decimal("100"):
                impossible_course_ids.append(cid)
                impossible_b_course_ids.append(cid)
            else:
                achievable_course_ids.append(cid)
                # Only add next unsubmitted for achievable courses
                nu = next((a for a in assignments if a["course_id"] == cid and a["submission_status"] == "not_submitted"), None)
                if nu:
                    next_unsubmitted_ids.append(nu["id"])
        else:
            achievable_course_ids.append(cid)
            # Only add next unsubmitted for achievable courses
            nu = next((a for a in assignments if a["course_id"] == cid and a["submission_status"] == "not_submitted"), None)
            if nu:
                next_unsubmitted_ids.append(nu["id"])

    # ── Guarantee at least 1 impossible course when multiple achievable courses exist ──
    # The lms_multi_course_thresholds task requires at least 1 impossible course for the
    # eval check to be satisfiable. If randomness produced all achievable courses, force
    # the first achievable course (that has remaining assignments) to be impossible by
    # reducing its existing grade scores so need > 100%.
    if not impossible_course_ids and len(achievable_course_ids) >= 2:
        # Find first achievable course with remaining assignments AND existing grades
        victim_cid = None
        for cid in achievable_course_ids:
            rem = [a for a in assignments if a["course_id"] == cid and a["score"] is None]
            cid_grades = [g for g in ctx.base["grades"]
                          if g["course_id"] == cid and g["score"] is not None and not g["is_dropped"]]
            if rem and cid_grades:
                victim_cid = cid
                break
        if victim_cid is not None:
            # Set all active grade scores to 25% of points_possible. Realistic
            # "struggling student" range — for any sane weight distribution
            # (final weight ∈ [0.2, 0.3], remaining weight ≥ 0.7) the required
            # final score still exceeds 100%, so the course remains impossible.
            # Previously this was 1%, which looked like a data bug to annotators
            # (an annotator flagged this as "The score is 0.96%, there's a bug!!!!").
            for g in ctx.base["grades"]:
                if g["course_id"] == victim_cid and not g["is_dropped"]:
                    pts = Decimal(str(g["points_possible"]))
                    g["score"] = float((pts * Decimal("0.25")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            # NOTE: Do NOT update assignment["score"] — grade records and assignment
            # records serve different purposes. grade_book weighted-score computation
            # uses grade records only. Assignment records' score field is used by
            # assignment_battery outputs (e.g. most_disputed_assignment_ids) which are
            # computed before grade_book runs and must reflect realistic scores.
            # Recompute and reclassify
            victim_course = next(c for c in courses if c["id"] == victim_cid)
            gp = victim_course["syllabus"]["grading_policy"]
            need, tw, xc = _compute_need(victim_cid, gp)
            # Move from achievable to impossible
            achievable_course_ids.remove(victim_cid)
            # Remove from next_unsubmitted_ids if it was added
            victim_nu = next((a for a in assignments if a["course_id"] == victim_cid and a["submission_status"] == "not_submitted"), None)
            if victim_nu and victim_nu["id"] in next_unsubmitted_ids:
                next_unsubmitted_ids.remove(victim_nu["id"])
            impossible_course_ids.append(victim_cid)
            impossible_b_course_ids.append(victim_cid)
            # Update current_weighted_scores for the victim course (matches the
            # 25% per-grade score set above).
            current_weighted_scores[victim_cid] = "25.00"

    impossible_b_enrollment_ids: list[str] = []
    for cid in impossible_b_course_ids:
        enrollment_id = enrollment_by_course.get(cid)
        if enrollment_id:
            impossible_b_enrollment_ids.append(enrollment_id)

    achievable_final_exam_assignment_ids: list[str] = []
    for cid in achievable_course_ids:
        fa = next(
            (a for a in assignments if a["course_id"] == cid and a["type"] == "exam" and a["weight_category"] == "final"),
            None,
        )
        if not fa:
            fa = next((a for a in assignments if a["course_id"] == cid and a["type"] == "exam"), None)
        if fa:
            achievable_final_exam_assignment_ids.append(fa["id"])

    # ── priority_order_ids (with grade data) ──
    # Same 7-day horizon as the assignment_battery priority filter — keeps
    # priority_order_ids aligned with what agents see as "due this week".
    _seven_day_horizon_g = ctx.now + timedelta(days=7)
    def _due_within_7d_g(a: dict) -> bool:
        due_raw = a.get("due_at")
        if not due_raw:
            return False
        due_dt = datetime.fromisoformat(due_raw) if isinstance(due_raw, str) else due_raw
        return due_dt <= _seven_day_horizon_g
    unsubmitted_all = [
        a for a in assignments
        if a["submission_status"] == "not_submitted" and _due_within_7d_g(a)
    ]
    wl: dict[str, dict[str, Decimal]] = {}
    for c in courses:
        gp = c["syllabus"]["grading_policy"]
        wd: dict[str, Decimal] = {}
        for cn, cp in gp.items():
            wd[cn] = Decimal(str(cp["weight"] if isinstance(cp, dict) else cp.weight))
        wl[c["id"]] = wd

    def _prio(a: dict) -> tuple:
        cw = wl.get(a["course_id"], {})
        w = cw.get(a["weight_category"], Decimal("0"))
        dr = a["due_at"]
        dd = datetime.fromisoformat(dr) if isinstance(dr, str) else dr
        return (-w, dd)

    priority_sorted = sorted(unsubmitted_all, key=_prio)
    priority_order_ids = [a["id"] for a in priority_sorted]

    # ── lowest_performing_course_id ──
    lowest_performing_course_id = ""
    lowest_score_val: Decimal | None = None
    for cid, sc_str in current_weighted_scores.items():
        sc = Decimal(sc_str)
        if lowest_score_val is None or sc < lowest_score_val:
            lowest_score_val = sc
            lowest_performing_course_id = cid

    # ── can_add_course: GPA stays above 3.0 with C in new 3-credit course ──
    student_gpa = Decimal(str(ctx.base.get("student", {}).get("gpa", "3.0")))
    total_credits = sum(c.get("credits", 3) for c in courses)
    current_quality_pts = student_gpa * Decimal(str(total_credits))
    # A C = 2.0 quality points per credit; new course = 3 credits
    new_quality_pts = current_quality_pts + Decimal("2.0") * Decimal("3")
    new_total_credits = total_credits + 3
    new_gpa = (new_quality_pts / Decimal(str(new_total_credits))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )
    can_add_course = "true" if new_gpa >= Decimal("3.0") else "false"

    # ── GPA risk analysis ──
    gpa_risk_course_ids: list[str] = []
    improvement_assignment_ids: list[str] = []
    for c in courses:
        cid = c["id"]
        sc_str = current_weighted_scores.get(cid)
        if not sc_str:
            continue
        sc = Decimal(sc_str)
        projected = _letter_gpa(sc)
        if projected < student_gpa:
            gpa_risk_course_ids.append(cid)
            unsub = next(
                (a for a in assignments if a["course_id"] == cid and a["submission_status"] == "not_submitted"),
                None,
            )
            if unsub:
                improvement_assignment_ids.append(unsub["id"])
    no_risk_flag = "true" if not gpa_risk_course_ids else "false"

    # ── most_disputed_assignment_ids ──
    gb_target_cid = target_cid
    graded_assignment_ids = {
        a["id"]
        for a in assignments
        if a.get("score") is not None
        and a["submission_status"] == "graded"
        and a.get("attempt_count", 0) < a.get("max_attempts", 1)
    }
    graded_for_dispute = [
        g for g in ctx.base["grades"]
        if g["course_id"] == gb_target_cid
        and g["score"] is not None
        and g["assignment_id"] in graded_assignment_ids
    ]
    if len(graded_for_dispute) < 2:
        graded_for_dispute = [
            g for g in ctx.base["grades"]
            if g["score"] is not None and g["assignment_id"] in graded_assignment_ids
        ]
    if len(graded_for_dispute) < 2:
        graded_for_dispute = [g for g in ctx.base["grades"] if g["score"] is not None]
    graded_for_dispute.sort(
        key=lambda g: Decimal(str(g["score"])) / Decimal(str(g["points_possible"]))
        if Decimal(str(g["points_possible"])) != 0 else Decimal("0"),
    )
    most_disputed_assignment_ids = [g["assignment_id"] for g in graded_for_dispute[:2]]

    # ── discrepancy analysis (with full grade data) ──
    discrepant_course_ids: list[str] = []
    non_discrepant_course_ids: list[str] = []
    discrepant_resubmit_assignment_ids: list[str] = []
    for c in courses:
        cid = c["id"]
        sc_with = current_weighted_scores.get(cid)
        if sc_with is None:
            non_discrepant_course_ids.append(cid)
            continue
        sc_without = _weighted_score(cid, include_dropped=True)
        if abs(Decimal(sc_with) - sc_without) > Decimal("1"):
            discrepant_course_ids.append(cid)
            # Find most recently graded assignment in this course
            course_grades = [
                g for g in ctx.base["grades"]
                if g["course_id"] == cid and g["score"] is not None
            ]
            if course_grades:
                best_g = None
                best_dt: datetime | None = None
                for g in course_grades:
                    a = next((a for a in assignments if a["id"] == g["assignment_id"]), None)
                    if a:
                        dr = a["due_at"]
                        dd = datetime.fromisoformat(dr) if isinstance(dr, str) else dr
                        if best_dt is None or dd > best_dt:
                            best_dt = dd
                            best_g = g
                if best_g:
                    discrepant_resubmit_assignment_ids.append(best_g["assignment_id"])
        else:
            non_discrepant_course_ids.append(cid)

    # ── score_below_70 (forwarded: whether target assignment score < 70%) ──
    _gb_score_below_70 = "false"
    tgt_id = ctx.outputs.get("target_assignment_id", "")
    if tgt_id:
        tgt_a = next((a for a in assignments if a["id"] == tgt_id), None)
        if tgt_a and tgt_a.get("score") is not None and tgt_a.get("points_possible"):
            pct = Decimal(str(tgt_a["score"])) / Decimal(str(tgt_a["points_possible"]))
            _gb_score_below_70 = "true" if pct < Decimal("0.70") else "false"

    # ── unsubmitted_hw_id (forwarded) ──
    # Prefer homework; fall back to any not_submitted assignment so the target is always set.
    _gb_unsubmitted_hw_id = ""
    for a in assignments:
        if a["submission_status"] == "not_submitted" and a["type"] == "homework":
            _gb_unsubmitted_hw_id = a["id"]
            break
    if not _gb_unsubmitted_hw_id:
        for a in assignments:
            if a["submission_status"] == "not_submitted":
                _gb_unsubmitted_hw_id = a["id"]
                break

    # ── latest_announcement_id (from announcements if available) ──
    # Scope to target_course_id when set so course-specific tasks ("the latest
    # announcement in your course") match what the agent sees in the course's
    # announcements tab. Without this, a globally-newest announcement from
    # another course can be picked, making the task unsolvable from the
    # in-course view.
    latest_announcement_id = ""
    announcements_list = ctx.base.get("announcements", [])
    _scoped_target_cid = target_cid or ctx.outputs.get("target_course_id", "")
    if announcements_list and _scoped_target_cid:
        scoped = [a for a in announcements_list if a.get("course_id") == _scoped_target_cid]
        if scoped:
            announcements_list = scoped
    if announcements_list:
        sorted_ann = sorted(
            announcements_list,
            key=lambda a: a["posted_at"] if isinstance(a["posted_at"], str)
            else a["posted_at"].isoformat(),
            reverse=True,
        )
        latest_announcement_id = sorted_ann[0]["id"]

    # Late seed adjustments can change grade scores after the first drop-lowest
    # pass. Recompute stored flags and derived targets once more so the UI,
    # task target, and LMSState.dropped_grades_for_category() agree.
    dropped_grade_ids = _recompute_dropped_grade_ids()
    current_weighted_scores = {
        c["id"]: str(_weighted_score(c["id"]))
        for c in courses
        if _weighted_score(c["id"]) != Decimal("0")
    }
    # Recompute grade_below_80 against the FINAL weighted score so the flag the
    # oneof discriminator reads agrees with what the agent computes from the
    # grades page (state.weighted_score_for_course). The earlier pass that set
    # grade_below_80 ran before this final recompute and could be stale.
    grade_below_80 = "false"
    if target_cid:
        _final_sc = current_weighted_scores.get(target_cid)
        if _final_sc and Decimal(_final_sc) < Decimal("80"):
            grade_below_80 = "true"
        minimum_final_score_for_b = _minimum_final_exam_score_for_b(final_exam_assignment_id)
        min_score_achievable = "true" if minimum_final_score_for_b else "false"
    if _hw_target_cid:
        _hw_grades = [
            g for g in ctx.base["grades"]
            if g["course_id"] == _hw_target_cid
            and g["weight_category"] == "homework"
            and g.get("score") is not None
        ]
        if _hw_grades:
            _hw_candidates = [g for g in _hw_grades if g.get("is_dropped", False)] or _hw_grades
            _hw_lowest = min(
                _hw_candidates,
                key=lambda g: Decimal(str(g["score"])) / Decimal(str(g["points_possible"]))
                if Decimal(str(g["points_possible"])) != 0 else Decimal("0"),
            )
            lowest_hw_id = _hw_lowest["assignment_id"]

    # ── named-pair comparison (compare_pair_codes) ───────────────────────────
    # For lms_compare_course_grades: the instruction names exactly two courses
    # (A, B) and asks the agent to drop the one with the lower *displayed*
    # weighted grade. The legacy lower_grade_* outputs above are the GLOBAL
    # minimum across all enrolled courses, which is wrong once the catalog has
    # more than two courses. These outputs scope the comparison to exactly the
    # two named courses and use the SAME weighted-score method the agent sees on
    # the Grades page (LMSState.weighted_score_for_course, which applies late
    # penalties) so the target always matches what the UI displays.
    named_pair_lower_course_id = ""
    named_pair_lower_enrollment_id = ""
    named_pair_higher_course_id = ""
    named_pair_higher_enrollment_id = ""
    named_pair_gap = "0.00"
    named_pair_gap_above_3 = "false"
    compare_pair_codes = params.get("compare_pair_codes") or []
    if len(compare_pair_codes) == 2:
        from webstress.backend.models.lms import LMSState as _LMSState

        _snapshot = _LMSState.model_validate(ctx.base)
        _pair = []
        for _code in compare_pair_codes:
            _c = next((c for c in courses if c["course_code"] == _code), None)
            if _c is None:
                continue
            _sc = _snapshot.weighted_score_for_course(_c["id"])
            if _sc is None:
                continue
            _pair.append((_c["id"], Decimal(str(_sc))))
        if len(_pair) == 2:
            _pair.sort(key=lambda t: t[1])
            (_lo_cid, _lo_sc), (_hi_cid, _hi_sc) = _pair[0], _pair[1]
            named_pair_lower_course_id = _lo_cid
            named_pair_higher_course_id = _hi_cid
            named_pair_lower_enrollment_id = enrollment_by_course.get(_lo_cid, "")
            named_pair_higher_enrollment_id = enrollment_by_course.get(_hi_cid, "")
            _gap = (_hi_sc - _lo_sc).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            named_pair_gap = str(_gap)
            named_pair_gap_above_3 = "true" if _gap > Decimal("3") else "false"

    return {
        "grade_ids": grade_ids,
        "dropped_grade_ids": dropped_grade_ids,
        "current_weighted_scores": current_weighted_scores,
        "minimum_final_score_for_b": minimum_final_score_for_b or "",
        # ── New outputs ──
        "has_discrepancy": has_discrepancy,
        "most_recent_graded_id": most_recent_graded_id,
        "most_impactful_graded_id": most_impactful_graded_id,
        "drop_changes_letter": drop_changes_letter,
        "drop_impact_above_3": drop_impact_above_3,
        "curve_changes_letter": curve_changes_letter,
        "min_score_achievable": min_score_achievable,
        "final_exam_assignment_id": final_exam_assignment_id,
        "final_exam_assignment_ids": ",".join(final_exam_assignment_ids_list),
        "impossible_b_enrollment_ids": ",".join(impossible_b_enrollment_ids),
        "achievable_final_exam_assignment_ids": ",".join(achievable_final_exam_assignment_ids),
        "lower_grade_course_id": lower_grade_course_id,
        "lower_grade_enrollment_id": lower_grade_enrollment_id,
        "named_pair_lower_course_id": named_pair_lower_course_id,
        "named_pair_lower_enrollment_id": named_pair_lower_enrollment_id,
        "named_pair_higher_course_id": named_pair_higher_course_id,
        "named_pair_higher_enrollment_id": named_pair_higher_enrollment_id,
        "named_pair_gap": named_pair_gap,
        "named_pair_gap_above_3": named_pair_gap_above_3,
        "lowest_hw_id": lowest_hw_id,
        "lowest_homework_id": lowest_hw_id,  # alias for YAML outputs that use this key
        "grade_below_80": grade_below_80,
        "highest_weight_ungraded_id": highest_weight_ungraded_id,
        "worst_category_assignment_id": worst_category_assignment_id,
        "impossible_course_ids": ",".join(impossible_course_ids),
        "achievable_course_ids": ",".join(achievable_course_ids),
        "impossible_b_course_ids": ",".join(impossible_b_course_ids),
        "gpa_risk_course_ids": ",".join(gpa_risk_course_ids),
        "improvement_assignment_ids": ",".join(improvement_assignment_ids),
        "no_risk_flag": no_risk_flag,
        "most_disputed_assignment_ids": ",".join(most_disputed_assignment_ids),
        "discrepant_course_ids": ",".join(discrepant_course_ids),
        "discrepant_resubmit_assignment_ids": ",".join(discrepant_resubmit_assignment_ids),
        "non_discrepant_course_ids": ",".join(non_discrepant_course_ids),
        "next_unsubmitted_ids": ",".join(next_unsubmitted_ids),
        "priority_order_ids": ",".join(priority_order_ids),
        "lowest_performing_course_id": lowest_performing_course_id,
        "can_add_course": can_add_course,
        "latest_announcement_id": latest_announcement_id,
        # ── Forwarded from earlier builders ──
        "score_below_70": _gb_score_below_70,
        "unsubmitted_hw_id": _gb_unsubmitted_hw_id,
    }


# ---------------------------------------------------------------------------
# 6. module_sequence
# ---------------------------------------------------------------------------

@_register("module_sequence")
def _build_module_sequence(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate modules with prerequisite chains.

    Params
    ------
    course_id : str          -- which course to attach modules to
    count : int              -- number of modules (default 5)
    chain_type : str         -- "linear", "branching", or "mixed" (default "linear")
    completed_count : int    -- how many modules are already completed (default 2)
    content_items_per_module : int -- total content items per module (default 2,
                                      min effectively 2; values >2 append
                                      quiz/reading/external_link content)
    """
    course_id = params.get("course_id", "")
    count = params.get("count", 5)
    chain_type = params.get("chain_type", "linear")
    completed_count = params.get("completed_count", 2)
    output_prefix = params.get("output_prefix", "")
    linked_assignment_id = params.get("linked_assignment_id", "")

    if not course_id:
        courses = ctx.base.get("courses", [])
        if courses:
            course_id = courses[0]["id"]

    if "modules" not in ctx.base:
        ctx.base["modules"] = []

    module_ids: list[str] = []
    first_locked_id: str | None = None
    next_available_id: str | None = None

    for i in range(count):
        module_id = ctx.next_id("module")
        module_ids.append(module_id)

        # Determine unlock condition based on chain type
        if i == 0:
            unlock_condition = "none"
            unlock_value: list[str] = []
        elif chain_type == "linear":
            unlock_condition = "prerequisite"
            unlock_value = [module_ids[i - 1]]
        elif chain_type == "branching":
            if i <= 1:
                unlock_condition = "prerequisite"
                unlock_value = [module_ids[0]]
            else:
                unlock_condition = "prerequisite"
                # Any one of the previous modules
                unlock_value = [module_ids[i - 1]]
        else:  # mixed
            if i == 1:
                unlock_condition = "prerequisite"
                unlock_value = [module_ids[0]]
            elif i > 1 and i % 2 == 0:
                unlock_condition = "min_score"
                unlock_value = [f"{module_ids[i - 1]}:70"]
            else:
                unlock_condition = "prerequisite"
                unlock_value = [module_ids[i - 1]]

        # Status
        if i < completed_count:
            status = "completed"
        elif i == completed_count:
            status = "available"
        else:
            status = "locked"

        # Content items. The base two (reading + video) are always present; an
        # optional `content_items_per_module` knob appends additional graded
        # content (quiz / reading / external_link rotation) so a task can demand
        # more per-module work without changing the unlock semantics. Seeded
        # items default to completed only for already-completed modules
        # (i < completed_count); every item on an incomplete module starts
        # uncompleted so the agent must finish all of them before marking done.
        content_items = [
            ContentItem(
                title=f"Reading: Chapter {i + 1}",
                type="reading",
                completed=i < completed_count,
            ),
            ContentItem(
                title=f"Video Lecture {i + 1}",
                type="video",
                completed=i < completed_count,
                linked_assignment_id=(linked_assignment_id or None) if i == completed_count else None,
            ),
        ]
        extra_items = max(0, int(params.get("content_items_per_module", 2)) - 2)
        _extra_cycle = ["quiz", "reading", "external_link"]
        for j in range(extra_items):
            item_type = _extra_cycle[j % len(_extra_cycle)]
            content_items.append(
                ContentItem(
                    title=f"{item_type.replace('_', ' ').title()} {i + 1}.{j + 1}",
                    type=item_type,
                    completed=i < completed_count,
                )
            )

        module = Module(
            id=module_id,
            course_id=course_id,
            title=f"Module {i + 1}: {ctx.rng.choice(_TOPICS)}",
            position=i,
            unlock_condition=unlock_condition,
            unlock_value=unlock_value,
            status=status,
            content_items=content_items,
        )
        ctx.base["modules"].append(module.model_dump())

        if status == "locked" and first_locked_id is None:
            first_locked_id = module_id
        if status == "available" and next_available_id is None:
            next_available_id = module_id

    result = {
        "module_ids": module_ids,
        "first_locked_module_id": first_locked_id or "",
        "next_available_module_id": next_available_id or "",
    }
    if linked_assignment_id:
        result["linked_assignment_id"] = linked_assignment_id

    if output_prefix:
        result[f"{output_prefix}_module_ids"] = module_ids
        result[f"{output_prefix}_first_locked_module_id"] = first_locked_id or ""
        result[f"{output_prefix}_next_available_module_id"] = next_available_id or ""

    return result


# ---------------------------------------------------------------------------
# 7. discussion_forums
# ---------------------------------------------------------------------------

_INSTRUCTOR_FEEDBACK_REPLIES: list[str] = [
    "Good start, but your argument needs more supporting evidence. Please cite at least two sources and strengthen your conclusion.",
    "Interesting perspective. However, you haven't addressed the counterargument. Please revise to acknowledge opposing views.",
    "Your analysis is surface-level. Dig deeper into the underlying causes and provide concrete examples.",
    "Solid initial post. Please expand on point 3 with more detail and connect it to this week's reading.",
]


@_register("discussion_forums")
def _build_discussion_forums(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate discussions with existing classmate posts.

    Params
    ------
    course_id : str              -- which course (default first)
    count : int                  -- number of discussions (default 2)
    posts_per : int              -- classmate posts per discussion (default 3)
    include_student_post : bool  -- add a student post + instructor reply in target discussion (default False)
    target_min_posts : int       -- min_posts on the TARGET discussion (default 1)
    target_min_replies : int     -- min_replies on the TARGET discussion (default 1)
    min_posts : int              -- min_posts on non-target discussions (default 1)
    min_replies : int            -- min_replies on non-target discussions (default 1)
    """
    course_id = params.get("course_id", "")
    count = params.get("count", 2)
    posts_per = params.get("posts_per", 3)
    include_student_post = params.get("include_student_post", False)
    target_min_posts = int(params.get("target_min_posts", 1))
    target_min_replies = int(params.get("target_min_replies", 1))
    other_min_posts = int(params.get("min_posts", 1))
    other_min_replies = int(params.get("min_replies", 1))

    if not course_id:
        courses = ctx.base.get("courses", [])
        if courses:
            course_id = courses[0]["id"]

    if "discussions" not in ctx.base:
        ctx.base["discussions"] = []
    if "discussion_posts" not in ctx.base:
        ctx.base["discussion_posts"] = []

    discussion_ids: list[str] = []
    target_discussion_id: str | None = None
    target_discussion_title: str = ""
    # Top-level classmate post IDs in the TARGET discussion, in creation order.
    # These are the only posts the student can reply to, so reply-distinctness
    # tasks precompute their eligible parent set from here.
    target_top_level_post_ids: list[str] = []

    for d in range(count):
        disc_id = ctx.next_id("discussion")
        discussion_ids.append(disc_id)

        prompt = ctx.rng.choice(_DISCUSSION_PROMPTS)
        due_offset = ctx.rng.randint(3, 14)
        disc_title = f"Discussion {d + 1}: {ctx.rng.choice(_TOPICS)}"

        # Discussion 0 is the target; it carries the (possibly higher) target
        # minimums. Sibling discussions keep the generic minimums so the agent
        # must read the target's own requirements rather than assume a default.
        is_target = d == 0
        disc_min_posts = target_min_posts if is_target else other_min_posts
        disc_min_replies = target_min_replies if is_target else other_min_replies

        discussion = Discussion(
            id=disc_id,
            course_id=course_id,
            title=disc_title,
            prompt=prompt,
            due_at=ctx.now + timedelta(days=due_offset),
            min_posts=disc_min_posts,
            min_replies=disc_min_replies,
            points_possible=Decimal("10"),
            weight_category="participation",
        )
        ctx.base["discussions"].append(discussion.model_dump())

        if d == 0:
            target_discussion_id = disc_id
            target_discussion_title = disc_title

        # Generate classmate posts
        for p in range(posts_per):
            classmate_name = ctx.fake.name()
            post_id = ctx.next_id("post")
            post = DiscussionPost(
                id=post_id,
                discussion_id=disc_id,
                author_id=ctx.next_id("classmate"),
                author_name=classmate_name,
                body=ctx.fake.paragraph(nb_sentences=2),
                timestamp=ctx.now - timedelta(hours=ctx.rng.randint(1, 72)),
            )
            ctx.base["discussion_posts"].append(post.model_dump())
            if is_target:
                target_top_level_post_ids.append(post_id)

            # Add a reply to the first post sometimes
            if p == 0 and posts_per > 1:
                reply_name = ctx.fake.name()
                reply = DiscussionPost(
                    id=ctx.next_id("post"),
                    discussion_id=disc_id,
                    author_id=ctx.next_id("classmate"),
                    author_name=reply_name,
                    body=ctx.fake.paragraph(nb_sentences=1),
                    parent_post_id=post_id,
                    timestamp=ctx.now - timedelta(hours=ctx.rng.randint(1, 24)),
                )
                ctx.base["discussion_posts"].append(reply.model_dump())

    # Look up course code for the target discussion
    target_course_code = ""
    instructor_name = "Prof. Smith"
    if course_id:
        for c in ctx.base.get("courses", []):
            if c["id"] == course_id:
                target_course_code = c["course_code"]
                instructor_name = c.get("instructor_name", instructor_name)
                break

    # Optionally add a student post + instructor reply to the target discussion
    if include_student_post and target_discussion_id:
        student_id = ctx.base.get("student", {}).get("id", "student_1")
        student_name = ctx.base.get("student", {}).get("name", "Student")

        student_post_id = ctx.next_id("post")
        student_post = DiscussionPost(
            id=student_post_id,
            discussion_id=target_discussion_id,
            author_id=student_id,
            author_name=student_name,
            body="Here is my initial analysis of the topic. I believe the key factors are the interplay between theoretical foundations and practical application, which we have been exploring throughout the course.",
            parent_post_id=None,
            timestamp=ctx.now - timedelta(days=3),
        )
        ctx.base["discussion_posts"].append(student_post.model_dump())

        instructor_reply_id = ctx.next_id("post")
        instructor_reply = DiscussionPost(
            id=instructor_reply_id,
            discussion_id=target_discussion_id,
            author_id="instructor_1",
            author_name=instructor_name,
            body=ctx.rng.choice(_INSTRUCTOR_FEEDBACK_REPLIES),
            parent_post_id=student_post_id,
            timestamp=ctx.now - timedelta(days=2),
        )
        ctx.base["discussion_posts"].append(instructor_reply.model_dump())

    return {
        "discussion_ids": discussion_ids,
        "target_discussion_id": target_discussion_id or "",
        "target_discussion_title": target_discussion_title,
        "target_course_code": target_course_code,
        # Required participation minimums on the TARGET discussion (serialized
        # as strings so they can be coerced inside expr predicates).
        "target_min_posts": str(target_min_posts),
        "target_min_replies": str(target_min_replies),
        # The first `target_min_replies` top-level classmate posts in the target
        # discussion. Reply-distinctness tasks bind one reply to each of these so
        # the agent must reply to DISTINCT parents, not the same post twice.
        "reply_parent_post_ids": ",".join(target_top_level_post_ids[:target_min_replies]),
        # Full set of top-level classmate posts in the target discussion (handy
        # for tasks that need the complete reply-eligible set).
        "target_top_level_post_ids": ",".join(target_top_level_post_ids),
    }


# ---------------------------------------------------------------------------
# 8. announcements_feed
# ---------------------------------------------------------------------------

@_register("announcements_feed")
def _build_announcements_feed(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate announcements distributed across courses.

    Params
    ------
    count : int        -- total announcements (default 6)
    unread_count : int -- how many are unread (default 2)
    urgent_count : int -- how many are urgent priority (default 1)
    """
    count = params.get("count", 6)
    unread_count = params.get("unread_count", 2)
    urgent_count = params.get("urgent_count", 1)
    # When True, urgent announcements receive deterministic, mutually-distinct
    # posted_at timestamps such that the single most-recently-posted urgent
    # UNREAD announcement is a non-first urgent index. This turns "find the
    # urgent one" into "re-derive top-1-by-recency over the urgent unread set",
    # with no tie-break ambiguity. Defaults False so every other task that uses
    # this builder keeps byte-identical fixtures.
    distinct_urgent_recency = params.get("distinct_urgent_recency", False)
    # target_course_unread_min: guarantee at least this many unread announcements
    # land in the catalog-level target course so course-scoped bijection tasks
    # (e.g. "mark every unread announcement in your course as read") are non-vacuous.
    # Defaults to 0 so existing tasks keep their round-robin behaviour unchanged.
    target_course_unread_min = int(params.get("target_course_unread_min", 0))
    _tc_target_cid = ctx.outputs.get("target_course_id", "")
    require_target_in_waitlisted = params.get("require_target_in_waitlisted", False)

    courses = ctx.base.get("courses", [])
    if "announcements" not in ctx.base:
        ctx.base["announcements"] = []

    announcement_ids: list[str] = []
    unread_ids: list[str] = []
    unread_urgent_ids: list[str] = []
    unread_normal_ids: list[str] = []
    urgent_announcement_id: str | None = None
    # (id, posted_at iso) for the urgent + unread pool used to derive the
    # single "most recently posted urgent unread" discriminator.
    urgent_unread_pool: list[tuple[str, str]] = []

    for i in range(count):
        ann_id = ctx.next_id("announcement")
        course_data = courses[i % len(courses)] if courses else {"id": "course_1"}
        course_id = course_data["id"]

        body = ctx.rng.choice(_ANNOUNCEMENT_BODIES).replace(
            "{mod_num}", str(ctx.rng.randint(1, 5)),
        )
        is_read = i >= unread_count
        is_urgent = i < urgent_count

        # Original behaviour (preserved exactly when the flag is off so that
        # every other consumer of this builder is unaffected). Consume the
        # RNG draw unconditionally to keep the stream identical.
        posted_at = ctx.now - timedelta(days=ctx.rng.randint(0, 14))
        if distinct_urgent_recency and is_urgent:
            # Deterministic, distinct timestamps for urgent announcements.
            # Rotate the "most recent" off the first urgent index: index 1 is
            # newest (0 minutes back), then 0, then 2, 3, ... — so the latest
            # urgent unread is NOT the first urgent the agent encounters.
            rank = 0 if i == 1 else (1 if i == 0 else i)
            posted_at = ctx.now - timedelta(hours=12) - timedelta(minutes=rank)

        announcement = Announcement(
            id=ann_id,
            course_id=course_id,
            title=f"{'URGENT: ' if is_urgent else ''}Announcement {i + 1}",
            body=body,
            posted_at=posted_at,
            is_read=is_read,
            priority="urgent" if is_urgent else "normal",
        )
        ctx.base["announcements"].append(announcement.model_dump())
        announcement_ids.append(ann_id)

        if not is_read:
            unread_ids.append(ann_id)
            if is_urgent:
                unread_urgent_ids.append(ann_id)
            else:
                unread_normal_ids.append(ann_id)
        if is_urgent and urgent_announcement_id is None:
            urgent_announcement_id = ann_id
        if is_urgent and not is_read:
            urgent_unread_pool.append((ann_id, posted_at.isoformat()))

    # ── latest_announcement_id: most recent UNREAD by posted_at (fallback to any) ──
    latest_announcement_id = ""
    all_announcements = ctx.base.get("announcements", [])

    # ── Guarantee target-course unread coverage for course-scoped bijection tasks ──
    # If the catalog target course has fewer than target_course_unread_min unread
    # announcements, reassign read announcements belonging to the target course to
    # unread (preferred), otherwise re-home other-course announcements onto the
    # target course and mark them unread. This never changes the total announcement
    # count, only course_id / is_read on a deterministic subset.
    if target_course_unread_min > 0 and _tc_target_cid:
        def _tc_unread() -> list[dict[str, Any]]:
            return [
                a for a in all_announcements
                if a.get("course_id") == _tc_target_cid and not a.get("is_read", True)
            ]
        # First, flip read target-course announcements to unread.
        for a in all_announcements:
            if len(_tc_unread()) >= target_course_unread_min:
                break
            if a.get("course_id") == _tc_target_cid and a.get("is_read", True):
                a["is_read"] = False
                if a["id"] not in unread_ids:
                    unread_ids.append(a["id"])
        # Then, re-home other-course announcements onto the target course as unread.
        for a in all_announcements:
            if len(_tc_unread()) >= target_course_unread_min:
                break
            if a.get("course_id") != _tc_target_cid:
                a["course_id"] = _tc_target_cid
                a["is_read"] = False
                if a["id"] not in unread_ids:
                    unread_ids.append(a["id"])
    # ── Enrollment-aware completion discriminator ──
    # Some tasks must mark read ONLY the unread+urgent announcements that belong
    # to a course the student is ACTIVELY enrolled in (status == "enrolled").
    # Unread+urgent announcements in a waitlisted/dropped course are decoys that
    # must stay unread, as are all non-urgent unread announcements. This forces
    # the agent to cross-reference announcement priority against enrollment
    # status rather than blindly using mark_all_read or "all unread".
    enrollments = ctx.base.get("enrollments", [])
    enrolled_course_ids = {
        e["course_id"] for e in enrollments if e.get("status") == "enrolled"
    }
    non_enrolled_course_ids = {
        e["course_id"] for e in enrollments if e.get("status") != "enrolled"
    }

    # Guarantee (when requested) that at least one unread+urgent announcement
    # lands in a NON-enrolled (e.g. waitlisted) course so the enrollment
    # cross-reference is load-bearing. Round-robin distribution does not always
    # place an urgent announcement in the waitlisted course, so reassign one
    # urgent+unread announcement to a non-enrolled course if none is there yet.
    if require_target_in_waitlisted and non_enrolled_course_ids:
        urgent_unread = [
            a for a in all_announcements
            if a["priority"] == "urgent" and not a.get("is_read", True)
        ]
        already_in_waitlisted = any(
            a["course_id"] in non_enrolled_course_ids for a in urgent_unread
        )
        in_enrolled = [
            a for a in urgent_unread if a["course_id"] in enrolled_course_ids
        ]
        # Only move one if there will still be >=1 urgent+unread left in an
        # enrolled course (keeps the positive target set non-empty).
        if not already_in_waitlisted and len(in_enrolled) >= 2:
            target_waitlisted_cid = sorted(non_enrolled_course_ids)[0]
            in_enrolled[-1]["course_id"] = target_waitlisted_cid

    enrolled_unread_urgent_ids = [
        a["id"] for a in all_announcements
        if a["priority"] == "urgent"
        and not a.get("is_read", True)
        and a["course_id"] in enrolled_course_ids
    ]
    preserved_unread_ids = [
        a["id"] for a in all_announcements
        if not a.get("is_read", True)
        and a["id"] not in set(enrolled_unread_urgent_ids)
    ]

    if all_announcements:
        sorted_ann = sorted(
            all_announcements,
            key=lambda a: a["posted_at"] if isinstance(a["posted_at"], str)
            else a["posted_at"].isoformat(),
            reverse=True,
        )
        # Prefer unread so conditional tasks don't vacuously pass
        unread_sorted = [a for a in sorted_ann if not a.get("is_read", True)]
        latest_announcement_id = (unread_sorted[0]["id"] if unread_sorted
                                  else sorted_ann[0]["id"])

    # ── course_announcement_ids: mapping of course_id -> announcement IDs ──
    course_ann_map: dict[str, list[str]] = {}
    for ann in all_announcements:
        cid = ann["course_id"]
        course_ann_map.setdefault(cid, []).append(ann["id"])
    # Flatten as comma-separated
    course_announcement_ids = ",".join(
        f"{cid}:{','.join(aids)}" for cid, aids in course_ann_map.items()
    )

    # ── target_urgent_announcement_id: the single most recently posted ──
    # urgent + unread announcement. Timestamps are distinct by construction,
    # so this top-1-by-recency discriminator is always unique. The agent must
    # re-derive it (filter urgent AND unread, pick latest posted_at) rather
    # than just "find the urgent one" — multiple urgent unread compete.
    urgent_unread_announcement_ids = [aid for aid, _ in urgent_unread_pool]
    target_urgent_announcement_id = ""
    if urgent_unread_pool:
        target_urgent_announcement_id = max(
            urgent_unread_pool, key=lambda pair: pair[1],
        )[0]
    # ── target_course_unread_announcement_ids: unread announcements in the
    # catalog target course, ordered most-recent-first. Exposed as a scalar list
    # so course-scoped "mark every unread announcement read" bijection tasks can
    # bind it without computing the set inside an invariant filter. ──
    target_course_unread_announcement_ids: list[str] = []
    if _tc_target_cid:
        _tc_unread_sorted = sorted(
            [
                a for a in all_announcements
                if a.get("course_id") == _tc_target_cid and not a.get("is_read", True)
            ],
            key=lambda a: a["posted_at"] if isinstance(a["posted_at"], str)
            else a["posted_at"].isoformat(),
            reverse=True,
        )
        target_course_unread_announcement_ids = [a["id"] for a in _tc_unread_sorted]
    # ── exam-scoped unread sets (depends on calendar_events output) ──
    # Precompute the intersection the agent must re-derive: unread announcements
    # whose course has an exam in the upcoming window, and the complementary set
    # of unread announcements in courses WITHOUT an upcoming exam. This is the
    # canonical eligible-set for exam-prep tasks; it is computed here (not inside
    # a diff filter) per the canonical_diff set-precomputation rule. When the
    # task did not run calendar_events first, both lists fall back to empty and
    # the legacy unread_announcement_ids output is used instead.
    exam_courses_raw = ctx.outputs.get("courses_with_upcoming_exams", "")
    exam_course_ids = {c for c in str(exam_courses_raw).split(",") if c}
    unread_in_exam_courses: list[str] = []
    unread_in_non_exam_courses: list[str] = []
    if exam_course_ids:
        for ann in all_announcements:
            if ann.get("is_read", True):
                continue
            if ann["course_id"] in exam_course_ids:
                unread_in_exam_courses.append(ann["id"])
            else:
                unread_in_non_exam_courses.append(ann["id"])

    return {
        "announcement_ids": announcement_ids,
        "unread_announcement_ids": unread_ids,
        "unread_urgent_announcement_ids": unread_urgent_ids,
        "unread_normal_announcement_ids": unread_normal_ids,
        "urgent_announcement_id": urgent_announcement_id or "",
        "urgent_unread_announcement_ids": urgent_unread_announcement_ids,
        "target_urgent_announcement_id": target_urgent_announcement_id,
        "latest_announcement_id": latest_announcement_id,
        "course_announcement_ids": course_announcement_ids,
        "target_course_unread_announcement_ids": ",".join(target_course_unread_announcement_ids),
        "enrolled_unread_urgent_announcement_ids": ",".join(enrolled_unread_urgent_ids),
        "preserved_unread_announcement_ids": ",".join(preserved_unread_ids),
        "non_enrolled_course_ids": ",".join(sorted(non_enrolled_course_ids)),
        # ── New computed outputs (exam-prep tasks) ──
        "unread_in_exam_courses_ids": ",".join(unread_in_exam_courses),
        "unread_in_non_exam_courses_ids": ",".join(unread_in_non_exam_courses),
        "unread_in_exam_courses_count": str(len(unread_in_exam_courses)),
    }


# ---------------------------------------------------------------------------
# 9. calendar_events
# ---------------------------------------------------------------------------

@_register("calendar_events")
def _build_calendar_events(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Generate calendar events (lectures, office hours, exams, deadlines).

    Params
    ------
    include_recurring : bool -- expand weekly recurrences (default True)
    weeks : int              -- how many weeks of recurrences (default 4)
    near_exam_courses : int  -- how many courses get exams within 14 days (default 0)
    force_exam_conflict : bool -- force two courses to share an exam day (default False)
    """
    include_recurring = params.get("include_recurring", True)
    weeks = params.get("weeks", 4)
    near_exam_courses = params.get("near_exam_courses", 0)

    courses = ctx.base.get("courses", [])
    if "calendar_events" not in ctx.base:
        ctx.base["calendar_events"] = []

    event_ids: list[str] = []
    next_exam_id: str | None = None
    next_deadline_id: str | None = None

    # For each course, create lecture and office hours recurrences + one-off events
    for ci, course_data in enumerate(courses):
        course_id = course_data["id"]

        # Weekly lecture
        lecture_day_offset = ci % 5  # Mon-Fri
        lecture_hour = 9 + ci  # stagger start times

        if include_recurring:
            semester_start = ctx.now - timedelta(days=45)
            for w in range(weeks):
                week_start = semester_start + timedelta(weeks=w)
                lecture_start = week_start + timedelta(
                    days=lecture_day_offset,
                    hours=lecture_hour - week_start.hour,
                )
                lecture_start = lecture_start.replace(
                    hour=lecture_hour, minute=0, second=0,
                )
                event_id = ctx.next_id("event")
                event = CalendarEvent(
                    id=event_id,
                    course_id=course_id,
                    title=f"{course_data['course_code']} Lecture",
                    event_type="lecture",
                    start_datetime=lecture_start,
                    end_datetime=lecture_start + timedelta(hours=1, minutes=15),
                    location=f"Room {100 + ci * 10 + ctx.rng.randint(1, 9)}",
                    recurrence="none",
                )
                ctx.base["calendar_events"].append(event.model_dump())
                event_ids.append(event_id)

            # Weekly office hours
            oh_day_offset = (lecture_day_offset + 2) % 5
            for w in range(weeks):
                week_start = semester_start + timedelta(weeks=w)
                oh_start = week_start + timedelta(days=oh_day_offset)
                oh_start = oh_start.replace(hour=14, minute=0, second=0)
                event_id = ctx.next_id("event")
                event = CalendarEvent(
                    id=event_id,
                    course_id=course_id,
                    title=f"{course_data['course_code']} Office Hours",
                    event_type="office_hours",
                    start_datetime=oh_start,
                    end_datetime=oh_start + timedelta(hours=2),
                    location=f"Office {200 + ci}",
                    recurrence="none",
                )
                ctx.base["calendar_events"].append(event.model_dump())
                event_ids.append(event_id)
        else:
            # Just add template events
            event_id = ctx.next_id("event")
            lecture_start = ctx.now.replace(hour=lecture_hour, minute=0, second=0)
            recurrence_end = ctx.now + timedelta(weeks=weeks)
            event = CalendarEvent(
                id=event_id,
                course_id=course_id,
                title=f"{course_data['course_code']} Lecture",
                event_type="lecture",
                start_datetime=lecture_start,
                end_datetime=lecture_start + timedelta(hours=1, minutes=15),
                location=f"Room {100 + ci * 10}",
                recurrence="weekly",
                recurrence_end_date=recurrence_end,
            )
            ctx.base["calendar_events"].append(event.model_dump())
            event_ids.append(event_id)

        # Exam event (from course data)
        exam_date_raw = course_data.get("final_exam_date")
        if exam_date_raw:
            exam_dt = datetime.fromisoformat(exam_date_raw) if isinstance(exam_date_raw, str) else exam_date_raw
            event_id = ctx.next_id("event")
            event = CalendarEvent(
                id=event_id,
                course_id=course_id,
                title=f"{course_data['course_code']} Final Exam",
                event_type="exam",
                start_datetime=exam_dt,
                end_datetime=exam_dt + timedelta(hours=3),
                location=f"Exam Hall {ctx.rng.choice(['A', 'B', 'C'])}",
            )
            ctx.base["calendar_events"].append(event.model_dump())
            event_ids.append(event_id)
            if next_exam_id is None and exam_dt > ctx.now:
                next_exam_id = event_id

    # Move some exams to within 14 days if near_exam_courses > 0
    if near_exam_courses > 0:
        moved = 0
        for ev in ctx.base["calendar_events"]:
            if ev["event_type"] != "exam" or moved >= near_exam_courses:
                continue
            near_dt = ctx.now + timedelta(days=ctx.rng.randint(3, 13))
            near_dt = near_dt.replace(hour=9, minute=0, second=0)
            ev["start_datetime"] = near_dt.isoformat() if isinstance(ev["start_datetime"], str) else near_dt
            ev["end_datetime"] = (near_dt + timedelta(hours=3)).isoformat() if isinstance(ev["end_datetime"], str) else near_dt + timedelta(hours=3)
            moved += 1

    # Add deadline events from assignments due in the future
    assignments = ctx.base.get("assignments", [])
    for a in assignments:
        due_at = datetime.fromisoformat(a["due_at"]) if isinstance(a["due_at"], str) else a["due_at"]
        if due_at > ctx.now and a["submission_status"] == "not_submitted":
            event_id = ctx.next_id("event")
            event = CalendarEvent(
                id=event_id,
                course_id=a["course_id"],
                title=f"Due: {a['title']}",
                event_type="deadline",
                start_datetime=due_at,
                end_datetime=due_at + timedelta(minutes=1),
            )
            ctx.base["calendar_events"].append(event.model_dump())
            event_ids.append(event_id)
            if next_deadline_id is None:
                next_deadline_id = event_id

    # ── courses_with/without_upcoming_exams (next 14 days) ──
    cutoff = ctx.now + timedelta(days=14)
    courses_with_exams: list[str] = []
    all_course_ids = [c["id"] for c in courses]
    for ev in ctx.base["calendar_events"]:
        if ev["event_type"] != "exam":
            continue
        start_raw = ev["start_datetime"]
        start_dt = datetime.fromisoformat(start_raw) if isinstance(start_raw, str) else start_raw
        if ctx.now < start_dt <= cutoff:
            if ev["course_id"] not in courses_with_exams:
                courses_with_exams.append(ev["course_id"])
    courses_without_exams = [cid for cid in all_course_ids if cid not in courses_with_exams]

    # ── conflicting_course_ids: courses with exams on the same day ──
    # Group exam events by date
    exam_by_date: dict[str, list[str]] = {}
    for ev in ctx.base["calendar_events"]:
        if ev["event_type"] != "exam":
            continue
        start_raw = ev["start_datetime"]
        start_dt = datetime.fromisoformat(start_raw) if isinstance(start_raw, str) else start_raw
        day_key = start_dt.strftime("%Y-%m-%d")
        exam_by_date.setdefault(day_key, [])
        if ev["course_id"] not in exam_by_date[day_key]:
            exam_by_date[day_key].append(ev["course_id"])

    conflicting_cids: list[str] = []
    for day_key, cids in exam_by_date.items():
        if len(cids) >= 2:
            for cid in cids:
                if cid not in conflicting_cids:
                    conflicting_cids.append(cid)

    # ── force_exam_conflict: if requested, ensure at least 2 courses share an exam day ──
    force_conflict = params.get("force_exam_conflict", False)
    if force_conflict and len(conflicting_cids) < 2 and len(courses) >= 2:
        # Move second course's exam to same date as first course's exam
        first_exam = None
        for ev in ctx.base["calendar_events"]:
            if ev["event_type"] == "exam" and ev["course_id"] == courses[0]["id"]:
                first_exam = ev
                break
        if first_exam:
            first_start = datetime.fromisoformat(first_exam["start_datetime"]) if isinstance(first_exam["start_datetime"], str) else first_exam["start_datetime"]
            for ev in ctx.base["calendar_events"]:
                if ev["event_type"] == "exam" and ev["course_id"] == courses[1]["id"]:
                    # Move to same date but different time
                    new_start = first_start.replace(hour=first_start.hour + 4)
                    ev["start_datetime"] = new_start.isoformat() if isinstance(ev["start_datetime"], str) else new_start
                    ev["end_datetime"] = (new_start + timedelta(hours=3)).isoformat() if isinstance(ev["end_datetime"], str) else new_start + timedelta(hours=3)
                    break
            conflicting_cids = [courses[0]["id"], courses[1]["id"]]

    # ── lower/higher grade conflict course IDs ──
    lower_grade_conflict_course_id = ""
    higher_grade_conflict_course_id = ""
    lower_grade_conflict_enrollment_id = ""
    if len(conflicting_cids) >= 2:
        current_scores = ctx.outputs.get("current_weighted_scores", {})
        scored_conflicts = []
        for cid in conflicting_cids:
            sc = current_scores.get(cid, "50")
            scored_conflicts.append((cid, Decimal(str(sc))))
        scored_conflicts.sort(key=lambda x: x[1])
        lower_grade_conflict_course_id = scored_conflicts[0][0]
        higher_grade_conflict_course_id = scored_conflicts[-1][0]
        for enrollment in ctx.base.get("enrollments", []):
            if enrollment.get("course_id") == lower_grade_conflict_course_id:
                lower_grade_conflict_enrollment_id = enrollment.get("id", "")
                break

    return {
        "event_ids": event_ids,
        "next_exam_event_id": next_exam_id or "",
        "next_deadline_event_id": next_deadline_id or "",
        # ── New outputs ──
        "courses_with_upcoming_exams": ",".join(courses_with_exams),
        "courses_without_upcoming_exams": ",".join(courses_without_exams),
        "conflicting_course_ids": ",".join(conflicting_cids),
        "lower_grade_conflict_course_id": lower_grade_conflict_course_id,
        "higher_grade_conflict_course_id": higher_grade_conflict_course_id,
        "lower_grade_conflict_enrollment_id": lower_grade_conflict_enrollment_id,
    }


# ---------------------------------------------------------------------------
# 10. peer_review_assignments
# ---------------------------------------------------------------------------

@_register("peer_review_assignments")
def _build_peer_review_assignments(ctx: LMSSeedContext, params: dict[str, Any]) -> dict[str, Any]:
    """Create peer review records in various states.

    Params
    ------
    count : int                   -- number of peer reviews (default 3)
    assignment_id : str           -- which assignment the reviews are for
    course_id : str               -- optional course filter for choosing the review source assignment
    statuses : list[str]          -- status distribution (default mixed)
    returned_review_count : int   -- how many reviews were returned for revision
    """
    count = params.get("count", 3)
    assignment_id = params.get("assignment_id", "")
    course_id = params.get("course_id", "")
    statuses = params.get("statuses", ["assigned", "in_progress", "submitted"])
    returned_review_count = params.get("returned_review_count", 0)
    # When True, the reviews flagged "returned for revision" are placed at the
    # TAIL indices instead of the head. This keeps peer_reviews[0] a NON-returned
    # review, which matters for tasks whose intervention variant clones
    # peer_reviews[0] as a decoy template — otherwise the cloned decoys would
    # inherit returned_for_revision=True and there would be more than one
    # "returned" review, breaking a "find the single returned review" framing.
    # Default False preserves head-placement for all existing tasks.
    returned_at_end = bool(params.get("returned_at_end", False))
    assignments = ctx.base.get("assignments", [])

    if not assignment_id:
        source_candidates = [
            a for a in assignments
            if (not course_id or a["course_id"] == course_id)
            and a["type"] in ("essay", "project", "homework")
        ]
        if not source_candidates:
            source_candidates = [a for a in assignments if not course_id or a["course_id"] == course_id]
        if not source_candidates:
            source_candidates = assignments
        if source_candidates:
            assignment_id = source_candidates[0]["id"]

    student_id = ctx.base.get("student", {}).get("id", "student_1")
    source_assignment = next((a for a in assignments if a["id"] == assignment_id), None)
    rubric_items = [
        RubricItem(criterion="clarity", max_points=Decimal("5"), description="Ideas are clearly communicated."),
        RubricItem(criterion="depth", max_points=Decimal("5"), description="Analysis is well developed and specific."),
        RubricItem(criterion="originality", max_points=Decimal("5"), description="Argument shows independent thought."),
    ]
    submission_title = (
        f"{source_assignment['title']} Draft"
        if source_assignment is not None
        else "Peer Review Draft Submission"
    )

    if "peer_reviews" not in ctx.base:
        ctx.base["peer_reviews"] = []

    review_ids: list[str] = []
    pending_review_ids: list[str] = []
    completed_review_ids: list[str] = []
    returned_review_ids: list[str] = []

    for i in range(count):
        review_id = ctx.next_id("review")
        status = statuses[i % len(statuses)]
        reviewee_name = ctx.fake.name()
        reviewee_id = ctx.next_id("reviewee")
        reviewee_submission = (
            f"Introduction:\n{ctx.fake.paragraph(nb_sentences=3)}\n\n"
            f"Main Argument:\n{ctx.fake.paragraph(nb_sentences=4)}\n\n"
            f"Conclusion:\n{ctx.fake.paragraph(nb_sentences=2)}"
        )
        returned_for_revision = (
            i >= count - returned_review_count
            if returned_at_end
            else i < returned_review_count
        )

        rubric_scores: dict[str, int] = {}
        comments = ""
        previous_rubric_scores: dict[str, int] = {}
        previous_comments = ""
        if status == "submitted":
            rubric_scores = {item.criterion: ctx.rng.randint(3, 5) for item in rubric_items}
            comments = ctx.fake.paragraph(nb_sentences=2)
        elif status == "in_progress":
            rubric_scores = {rubric_items[0].criterion: ctx.rng.randint(2, 4)}

        if returned_for_revision:
            status = "in_progress"
            previous_rubric_scores = {
                rubric_items[0].criterion: 2,
                rubric_items[1].criterion: 1 if len(rubric_items) > 1 else 2,
            }
            previous_comments = (
                "This review was returned because not all rubric categories were scored "
                "and the explanation was too brief."
            )
            if not rubric_scores:
                rubric_scores = previous_rubric_scores.copy()
            if review_id not in returned_review_ids:
                returned_review_ids.append(review_id)

        review = PeerReview(
            id=review_id,
            assignment_id=assignment_id,
            reviewer_student_id=student_id,
            reviewee_student_id=reviewee_id,
            reviewee_name=reviewee_name,
            submission_title=submission_title,
            submission_body=reviewee_submission,
            assignment_rubric=rubric_items,
            rubric_scores=rubric_scores,
            comments=comments,
            status=status,
            returned_for_revision=returned_for_revision,
            previous_rubric_scores=previous_rubric_scores,
            previous_comments=previous_comments,
            due_at=ctx.now + timedelta(days=ctx.rng.randint(3, 10)),
        )
        ctx.base["peer_reviews"].append(review.model_dump())
        review_ids.append(review_id)

        if status in ("assigned", "in_progress"):
            pending_review_ids.append(review_id)
        else:
            completed_review_ids.append(review_id)

    # Distinct list of assignment_ids referenced by these peer reviews. Used
    # by canonical_diff invariants that need to whitelist cascade effects on
    # the underlying assignments when a peer review is submitted.
    peer_review_assignment_id_list: list[str] = []
    seen_aid: set[str] = set()
    for pr in ctx.base["peer_reviews"]:
        aid = pr.get("assignment_id", "")
        if aid and aid not in seen_aid:
            peer_review_assignment_id_list.append(aid)
            seen_aid.add(aid)

    # ── Returned-review discriminators ──
    # When at least one review was returned for revision, expose scalar targets
    # for the FIRST returned review so "redo" tasks can gate on (a) the precise
    # target review, (b) the reviewee name the comment must address, and
    # (c) the previously-recorded rubric scores the agent must change. These
    # let a canonical_diff require the new scores to DIFFER from the old ones on
    # every previously-scored criterion (a re-derived discriminator) without
    # the eval having to introspect read-only proxy dicts at scoring time.
    returned_review_target_id = ""
    returned_review_reviewee_name = ""
    returned_prev_clarity = ""
    returned_prev_depth = ""
    returned_prev_originality = ""
    if returned_review_ids:
        returned_review_target_id = returned_review_ids[0]
        _rt = next(
            (pr for pr in ctx.base["peer_reviews"] if pr["id"] == returned_review_target_id),
            None,
        )
        if _rt is not None:
            returned_review_reviewee_name = _rt.get("reviewee_name", "")
            _prev = _rt.get("previous_rubric_scores", {}) or {}
            if "clarity" in _prev:
                returned_prev_clarity = str(_prev["clarity"])
            if "depth" in _prev:
                returned_prev_depth = str(_prev["depth"])
            if "originality" in _prev:
                returned_prev_originality = str(_prev["originality"])

    return {
        "review_ids": review_ids,
        "pending_review_ids": pending_review_ids,
        "completed_review_ids": completed_review_ids,
        "returned_review_ids": returned_review_ids,
        "target_review_id": pending_review_ids[0] if pending_review_ids else (review_ids[0] if review_ids else ""),
        "peer_review_assignment_ids": peer_review_assignment_id_list,
        "returned_review_target_id": returned_review_target_id,
        "returned_review_reviewee_name": returned_review_reviewee_name,
        "returned_prev_clarity": returned_prev_clarity,
        "returned_prev_depth": returned_prev_depth,
        "returned_prev_originality": returned_prev_originality,
    }
