def stage_is_unlocked(user, stage):
    if not stage.is_active:
        return False
    # Stage 0 is always freely accessible, including for anonymous users.
    if stage.number == 0:
        return True
    # Stage 1 and every later stage require authentication.
    if not getattr(user, "is_authenticated", False):
        return False
    # Stage 1 unlocks after the user engages with stage 0.
    if stage.number == 1:
        return True
    previous = ExamStage.objects.filter(track=stage.track, number=stage.number - 1, is_active=True).first()
    if not previous:
        return False
    required = list(previous.exams.filter(is_published=True, counts_for_stage_unlock=True))
    if not required:
        return False
    progress = {p.exam_id: p for p in UserExamProgress.objects.filter(user=user, exam__in=required)}
    passed = sum(1 for exam in required if (progress.get(exam.pk) and progress[exam.pk].status == "completed"))
    return (passed * 100 / len(required)) >= stage.required_completion_percent


def exam_access(user, exam):
    """Return (allowed, reason). Always enforce access on the server."""
    authenticated = bool(getattr(user, "is_authenticated", False))

    # Stage 0 is public. The exam itself must also be free and allow guests.
    if exam.stage_id and exam.stage.number == 0:
        if exam.access_type == "free" or exam.is_free:
            if exam.guest_allowed or authenticated:
                return True, "آزمون آماده شروع است."

    if exam.stage_id and not stage_is_unlocked(user, exam.stage):
        if exam.stage.number >= 1:
            return False, "برای دسترسی به این مرحله ابتدا وارد حساب کاربری شوید."
        return False, "برای باز شدن این مرحله، ابتدا مرحله قبل را تکمیل کن."

    if exam.prerequisite_exam_id:
        if not authenticated:
            return False, "برای بررسی پیش‌نیاز، ابتدا وارد حساب کاربری شو."
        prerequisite = UserExamProgress.objects.filter(user=user, exam=exam.prerequisite_exam).first()
        if not prerequisite or prerequisite.status != "completed":
            return False, f"ابتدا آزمون «{exam.prerequisite_exam.title}» را با حداقل امتیاز لازم تکمیل کن."
        if prerequisite.best_score < exam.prerequisite_exam.minimum_score:
            return False, "حداقل امتیاز آزمون پیش‌نیاز کسب نشده است."

    if exam.access_type == "prerequisite" and not exam.prerequisite_exam_id:
        return False, "پیش‌نیاز این آزمون هنوز در پنل مدیریت تعیین نشده است."
    if exam.access_type == "premium":
        if not authenticated:
            return False, "این آزمون ویژه است؛ ابتدا وارد حساب کاربری شو."
        if not ExamPurchase.objects.filter(user=user, exam=exam, status="paid").exists() and not user.is_superuser:
            return False, "برای دسترسی به این آزمون باید خرید آن ثبت شود."
    return True, "آزمون آماده شروع است."
